"""Does the location policy protect home addresses, and what does it cost analytics?

Attack: someone with read access to step locations (an analyst, a leaked export, a third party tool)
guesses each traveler's home as (a) the place they log most often, or (b) where their first trip starts.
Success = guess within 1 km of the true home (homes.csv is ground truth, used only here).
Run on raw events and on the governed warehouse table, over the same travelers.
Utility: trip distance on governed coordinates vs the same steps at full precision.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pandas as pd

from gate import haversine_km, pseudo

ROOT = Path(__file__).resolve().parents[1]


def attack(points: dict[str, list[tuple[str, float, float]]], homes: dict[str, tuple[float, float]], users: list[str]) -> dict:
    hit = {"most_visited": 0, "first_step": 0, "either": 0}
    for u in users:
        pts = points.get(u) or []
        if not pts:
            continue  # nothing visible, nothing to guess from
        mode = Counter((round(la, 2), round(lo, 2)) for _, la, lo in pts).most_common(1)[0][0]
        first = min(pts)
        a = haversine_km(mode, homes[u]) <= 1.0
        b = haversine_km((first[1], first[2]), homes[u]) <= 1.0
        hit["most_visited"] += a
        hit["first_step"] += b
        hit["either"] += a or b
    return {k: round(100 * v / len(users), 1) for k, v in hit.items()}


def main() -> dict:
    homes_df = pd.read_csv(ROOT / "data" / "raw" / "homes.csv")
    key_of = {u: pseudo(u, "user") for u in homes_df.user_id}
    homes = {key_of[r.user_id]: (r.home_lat, r.home_lon) for r in homes_df.itertuples()}

    raw_pts: dict[str, list] = defaultdict(list)
    raw_coord = {}
    with open(ROOT / "data" / "raw" / "app_events.jsonl", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e["event"] == "step_added" and e["user_id"] in key_of:
                p = e["properties"]
                raw_pts[key_of[e["user_id"]]].append((e["event_ts"], p["lat"], p["lon"]))
                raw_coord[p["step_id"]] = (p["lat"], p["lon"])

    con = duckdb.connect(str(ROOT / "warehouse" / "polarsteps.duckdb"), read_only=True)
    steps = con.sql("select user_key, trip_id, step_id, cast(stepped_at as varchar) ts, lat_1km, lon_1km, location_suppressed "
                    "from staging.stg_step_added order by trip_id, stepped_at").df()
    gov_pts: dict[str, list] = defaultdict(list)
    for r in steps[~steps.location_suppressed].itertuples():
        gov_pts[r.user_key].append((r.ts, r.lat_1km, r.lon_1km))

    users = sorted(set(steps.user_key))  # travelers with at least one accepted step
    raw = attack(raw_pts, homes, users)
    gov = attack(gov_pts, homes, users)

    # utility: trip distance between visible steps, governed grid vs full precision for the same steps
    errs = []
    for _, g in steps[~steps.location_suppressed].groupby("trip_id"):
        if len(g) < 2:
            continue
        d_gov = sum(haversine_km(a, b) for a, b in zip(zip(g.lat_1km, g.lon_1km), list(zip(g.lat_1km, g.lon_1km))[1:]))
        prec = [raw_coord[s] for s in g.step_id]
        d_raw = sum(haversine_km(a, b) for a, b in zip(prec, prec[1:]))
        if d_raw > 0:
            errs.append(abs(d_gov - d_raw) / d_raw)
    errs.sort()
    res = {
        "travelers": len(users),
        "home_found_within_1km_pct": {"raw": raw, "governed": gov},
        "steps_location_suppressed_pct": round(100 * steps.location_suppressed.mean(), 1),
        "trip_distance_error_pct": {"median": round(100 * errs[len(errs) // 2], 2), "p95": round(100 * errs[int(len(errs) * 0.95)], 2),
                                    "trips": len(errs)},
        "step_and_country_counts": "unchanged: suppressed steps keep their row and country_code, only coordinates are removed",
        "mixpanel_export_location": "country_code only",
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "privacy_eval.json").write_text(json.dumps(res, indent=2))
    print(f"privacy: home found within 1 km for {raw['either']}% of travelers on raw data, {gov['either']}% on governed; "
          f"trip distance error median {res['trip_distance_error_pct']['median']}%")
    return res


if __name__ == "__main__":
    main()
