"""Tracking plan gate: the only path from raw app events to the warehouse and to third parties.

For every raw event it decides: accept, repair (documented fixes only), or quarantine with a reason.
Accepted events are pseudonymized and privacy treated per the plan's pii classes before anything is written.

Outputs (data/governed/):
  events.parquet       accepted, pseudonymized events (properties as JSON)
  quarantine.parquet   rejected events with reason, payload already redacted
  gate_log.parquet     one row per action taken (repair, redaction, drop) for audit
  installs.parquet, skan_postbacks.parquet, ad_spend.parquet
and data/exports/ mixpanel_events.jsonl, ad_platform_conversions.csv
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW, GOV, EXP = ROOT / "data" / "raw", ROOT / "data" / "governed", ROOT / "data" / "exports"
PLAN_PATH = ROOT / "tracking_plan" / "tracking_plan.yml"

DEMO_KEY = "demo-only-rotate-me"
PSEUDO_KEY = os.environ.get("PSEUDO_KEY", DEMO_KEY).encode()

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
HOME_RADIUS_KM = 2.0
GRID_DECIMALS = 2  # ~1.1 km
IP_PREFIX_COUNTRY = {str(10 + i): c for i, c in enumerate(["NL", "DE", "GB", "FR", "ES", "BE", "DK", "IT", "US"])}
NEVER_EXPORTED = {"direct", "free_text", "location"}  # pii classes that never leave the warehouse


def pseudo(value: str, kind: str) -> str:
    """Keyed HMAC: stable join key, not reversible without the key, rotatable."""
    return hmac.new(PSEUDO_KEY, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:20]


def redact(text: str) -> tuple[str, list[str]]:
    hits = []
    if EMAIL_RE.search(text):
        text, hits = EMAIL_RE.sub("[email]", text), hits + ["email"]
    if PHONE_RE.search(text):
        text, hits = PHONE_RE.sub("[phone]", text), hits + ["phone"]
    return text, hits


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def type_ok(v, t: str) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "timestamp":
        try:
            datetime.fromisoformat(v)
            return True
        except (TypeError, ValueError):
            return False
    return False


class Gate:
    def __init__(self, plan: dict):
        self.plan = plan
        self.alias = {a: name for name, spec in plan["events"].items() for a in spec.get("aliases", [])}
        self.seen: set[str] = set()
        self.log: list[dict] = []

    def _log(self, ev: dict, action: str, detail: str = "") -> None:
        self.log.append({"event_id": ev.get("event_id"), "action": action, "detail": detail})

    def treat_props(self, ev: dict, spec: dict, props: dict) -> dict:
        """Apply pii classes. Location stays precise here; the batch pass in apply_location_policy treats it."""
        out = {}
        for k, v in props.items():
            p = spec["properties"].get(k)
            if p is None:
                continue
            if p["pii"] == "direct":
                self._log(ev, "dropped_direct_pii", k)
                continue
            if p["pii"] == "free_text" and isinstance(v, str):
                v, hits = redact(v)
                for h in hits:
                    self._log(ev, f"redacted_{h}", k)
            out[k] = v
        return out

    def process(self, ev: dict) -> tuple[str, dict]:
        """Returns ("accepted" | "quarantined" | "duplicate", record)."""
        if ev["event_id"] in self.seen:
            self._log(ev, "duplicate_dropped")
            return "duplicate", {}
        self.seen.add(ev["event_id"])

        name = ev["event"]
        reasons: list[str] = []
        if name not in self.plan["events"]:
            if name in self.alias:
                self._log(ev, "renamed_alias", f"{name}->{self.alias[name]}")
                name = self.alias[name]
            else:
                reasons.append("unknown_event")
        spec = self.plan["events"].get(name, {"properties": {}})

        for k, c in self.plan["context"].items():
            v = ev.get(k)
            if v is None:
                if c["required"]:
                    reasons.append(f"missing_context:{k}")
            elif not type_ok(v, c["type"]) or ("enum" in c and v not in c["enum"]):
                reasons.append(f"bad_context:{k}")

        props = dict(ev.get("properties") or {})
        for k in list(props):
            if k not in spec["properties"] and "unknown_event" not in reasons:
                self._log(ev, "undeclared_property", k)
                props.pop(k)
        for k, p in spec["properties"].items():
            if k not in props or props[k] is None:
                if p["required"]:
                    reasons.append(f"missing_required:{k}")
                continue
            v = props[k]
            if p.get("normalize") == "lower" and isinstance(v, str) and v != v.lower():
                self._log(ev, "normalized_enum", f"{k}:{v}")
                v = props[k] = v.lower()
            if not type_ok(v, p["type"]):
                reasons.append(f"type_error:{k}")
            elif "enum" in p and v not in p["enum"]:
                reasons.append(f"enum_violation:{k}")

        event_ts = ev.get("event_ts")
        if type_ok(event_ts, "timestamp") and type_ok(ev.get("received_ts"), "timestamp"):
            if datetime.fromisoformat(event_ts) > datetime.fromisoformat(ev["received_ts"]) + timedelta(days=1):
                self._log(ev, "clock_clamped", event_ts)
                event_ts = ev["received_ts"]

        ip = ev.get("ip") or ""
        base = {
            "event_id": ev["event_id"],
            "event_name": name,
            "user_key": pseudo(ev["user_id"], "user") if ev.get("user_id") else None,
            "device_key": pseudo(ev["device_id"], "device") if ev.get("device_id") else None,
            "platform": ev.get("platform"),
            "app_version": ev.get("app_version"),
            "event_ts": event_ts,
            "received_ts": ev.get("received_ts"),
            "geo_country": IP_PREFIX_COUNTRY.get(ip.split(".")[0]),
        }
        if reasons:
            # quarantine keeps the evidence needed to fix and replay, never raw identifiers or contact details
            safe = {k: v for k, v in props.items() if spec["properties"].get(k, {}).get("pii") != "location"}
            safe = self.treat_props(ev, {"properties": {k: spec["properties"].get(k, {"pii": "free_text"}) for k in safe}}, safe)
            return "quarantined", {**base, "raw_event_name": ev["event"], "reason": ";".join(reasons),
                                   "payload": json.dumps(safe, ensure_ascii=False)}

        self._email = props.get("email") if name == "user_signed_up" else None
        return "accepted", {**base, "props": self.treat_props(ev, spec, props)}


def apply_location_policy(accepted: list[dict], gate: Gate) -> None:
    """Batch pass over step_added. A trip's first and last step mark where a traveler starts and ends,
    which is usually home. Every step within HOME_RADIUS_KM of any such point is suppressed; the rest
    are rounded to ~1 km. Step rows are kept so counts and countries stay exact."""
    trips: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in accepted:
        if r["event_name"] == "step_added":
            trips[(r["user_key"], r["props"]["trip_id"])].append(r)
    zones: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (user, _), steps in trips.items():
        steps.sort(key=lambda r: r["event_ts"])
        for r in (steps[0], steps[-1]):
            zones[user].append((r["props"]["lat"], r["props"]["lon"]))
    for (user, _), steps in trips.items():
        for r in steps:
            pt = (r["props"]["lat"], r["props"]["lon"])
            if any(haversine_km(pt, z) <= HOME_RADIUS_KM for z in zones[user]):
                r["props"]["lat"] = r["props"]["lon"] = None
                r["props"]["location_suppressed"] = True
                gate.log.append({"event_id": r["event_id"], "action": "location_suppressed", "detail": "home_zone"})
            else:
                r["props"]["lat"] = round(pt[0], GRID_DECIMALS)
                r["props"]["lon"] = round(pt[1], GRID_DECIMALS)
                r["props"]["location_suppressed"] = False


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    duckdb.sql(f"copy (select * from df) to '{path.as_posix()}' (format parquet)")


def main() -> dict:
    if PSEUDO_KEY == DEMO_KEY.encode():
        print("gate: using the demo pseudonymization key; set PSEUDO_KEY in any real environment")
    GOV.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    plan = yaml.safe_load(PLAN_PATH.read_text(encoding="utf-8"))
    gate = Gate(plan)

    accepted, quarantined, consented_email = [], [], {}
    counts = defaultdict(int)
    with open(RAW / "app_events.jsonl", encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            status, rec = gate.process(ev)
            counts[status] += 1
            if status == "accepted":
                accepted.append(rec)
                if rec["event_name"] == "user_signed_up" and rec["props"].get("marketing_consent") and gate._email:
                    # ad platforms match on normalized SHA256 email, consented users only
                    consented_email[rec["user_key"]] = hashlib.sha256(gate._email.strip().lower().encode()).hexdigest()
            elif status == "quarantined":
                quarantined.append(rec)
    counts["raw"] = sum(counts.values())

    apply_location_policy(accepted, gate)

    events = pd.DataFrame([{**{k: v for k, v in r.items() if k != "props"}, "properties": json.dumps(r["props"], ensure_ascii=False)}
                           for r in accepted])
    write_parquet(events, GOV / "events.parquet")
    write_parquet(pd.DataFrame(quarantined), GOV / "quarantine.parquet")
    write_parquet(pd.DataFrame(gate.log), GOV / "gate_log.parquet")

    # third party exports: only events whose plan lists the destination, only non sensitive properties
    with open(EXP / "mixpanel_events.jsonl", "w", encoding="utf-8") as f:
        for r in accepted:
            spec = plan["events"][r["event_name"]]
            if "mixpanel" not in spec["destinations"]:
                continue
            props = {k: v for k, v in r["props"].items()
                     if k in spec["properties"] and spec["properties"][k]["pii"] not in NEVER_EXPORTED}
            props.update({"distinct_id": r["user_key"], "time": r["event_ts"], "$insert_id": r["event_id"],
                          "platform": r["platform"], "app_version": r["app_version"], "mp_country_code": r["geo_country"]})
            f.write(json.dumps({"event": r["event_name"], "properties": props}) + "\n")
    conv = []
    for r in accepted:
        if "ad_platforms" in plan["events"][r["event_name"]]["destinations"] and r["user_key"] in consented_email:
            conv.append({"event_name": r["event_name"], "event_time": r["event_ts"], "event_id": r["event_id"],
                         "em_sha256": consented_email[r["user_key"]], "value_eur": r["props"].get("price_eur", 0)})
    pd.DataFrame(conv).to_csv(EXP / "ad_platform_conversions.csv", index=False)

    inst = pd.read_csv(RAW / "installs.csv", dtype=str, keep_default_na=False)
    inst["install_key"] = inst.pop("appsflyer_id").map(lambda v: pseudo(v, "install"))
    inst["user_key"] = inst.pop("customer_user_id").map(lambda v: pseudo(v, "user") if v else None)
    write_parquet(inst, GOV / "installs.parquet")
    for name in ("skan_postbacks", "ad_spend"):
        write_parquet(pd.read_csv(RAW / f"{name}.csv"), GOV / f"{name}.parquet")

    counts["quarantined"] = len(quarantined)
    counts["accepted"] = len(accepted)
    print(f"gate: {counts['raw']:,} raw -> {counts['accepted']:,} accepted, {counts['quarantined']:,} quarantined, "
          f"{counts['duplicate']:,} duplicates; {len(gate.log):,} logged actions")
    return dict(counts)


if __name__ == "__main__":
    main()
