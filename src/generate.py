"""Synthetic Polarsteps-like raw data with known, labelled problems.

Writes to data/raw/:
  app_events.jsonl     raw app events as the SDK would send them
  installs.csv         AppsFlyer-style install report (user level)
  skan_postbacks.csv   SKAdNetwork postbacks (aggregated, iOS without ATT consent)
  ad_spend.csv         daily spend per network and campaign
  homes.csv            ground truth home location per user (for the privacy test only)
  truth.csv            ground truth label per planted problem event
"""
from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw"
RNG = random.Random(20261116)

START = datetime(2026, 1, 1)
END = datetime(2026, 10, 31, 23, 59)
N_INSTALLS = 20_000

HOME_CITIES = {
    "NL": [("Amsterdam", 52.3676, 4.9041), ("Rotterdam", 51.9244, 4.4777), ("Utrecht", 52.0907, 5.1214)],
    "DE": [("Berlin", 52.52, 13.405), ("Munich", 48.1351, 11.582), ("Hamburg", 53.5511, 9.9937)],
    "GB": [("London", 51.5072, -0.1276), ("Manchester", 53.4808, -2.2426)],
    "FR": [("Paris", 48.8566, 2.3522), ("Lyon", 45.764, 4.8357)],
    "ES": [("Madrid", 40.4168, -3.7038), ("Barcelona", 41.3874, 2.1686)],
    "BE": [("Brussels", 50.8503, 4.3517)],
    "DK": [("Copenhagen", 55.6761, 12.5683)],
    "IT": [("Milan", 45.4642, 9.19)],
    "US": [("New York", 40.7128, -74.006)],
}
COUNTRY_W = {"NL": 0.24, "DE": 0.2, "GB": 0.14, "FR": 0.1, "ES": 0.08, "BE": 0.07, "DK": 0.05, "IT": 0.06, "US": 0.06}
IP_PREFIX = {c: str(10 + i) for i, c in enumerate(HOME_CITIES)}  # fake IP ranges per country

DESTS = [("PT", 38.7223, -9.1393), ("TH", 13.7563, 100.5018), ("TH", 18.7883, 98.9853), ("VN", 21.0278, 105.8342),
         ("IS", 64.1466, -21.9426), ("JP", 35.6762, 139.6503), ("PE", -13.532, -71.9675), ("NO", 69.6492, 18.9553),
         ("IT", 41.9028, 12.4964), ("GR", 37.9838, 23.7275), ("MA", 31.6295, -7.9811), ("NZ", -45.0312, 168.6626),
         ("ID", -8.5069, 115.2625), ("MX", 17.0732, -96.7266), ("HR", 43.5081, 16.4402), ("AU", -33.8688, 151.2093)]

# Channel mix and true cost per install. Polarsteps grows mostly by word of mouth.
CHANNELS = {
    "organic":          {"w": 0.52, "cpi": 0.0, "activate": 0.56},
    "google_ads":       {"w": 0.10, "cpi": 2.10, "activate": 0.40},
    "meta_ads":         {"w": 0.13, "cpi": 1.60, "activate": 0.31},
    "tiktok_ads":       {"w": 0.07, "cpi": 1.20, "activate": 0.22},
    "apple_search_ads": {"w": 0.08, "cpi": 2.80, "activate": 0.46},
    "influencer":       {"w": 0.10, "cpi": 1.00, "activate": 0.45},
}
CAMPAIGNS = {"google_ads": ["brand", "trip_planner"], "meta_ads": ["summer_trips", "travel_book"],
             "tiktok_ads": ["roadtrip_ugc"], "apple_search_ads": ["brand"], "influencer": ["backpackers_q2", "vanlife_q3"]}

# App versions over time. Android 5.12.0 (4 Aug to 18 Aug) ships a bug that drops trip_id on step_added.
ANDROID_RELEASES = [(datetime(2025, 9, 1), "5.8.2"), (datetime(2026, 2, 10), "5.9.0"), (datetime(2026, 4, 2), "5.10.1"),
                    (datetime(2026, 6, 9), "5.11.3"), (datetime(2026, 8, 4), "5.12.0"), (datetime(2026, 8, 18), "5.12.1"),
                    (datetime(2026, 10, 1), "5.13.0")]
IOS_RELEASES = [(datetime(2025, 9, 1), "7.0.4"), (datetime(2026, 3, 3), "7.1.0"), (datetime(2026, 5, 19), "7.2.0"),
                (datetime(2026, 7, 14), "7.2.3"), (datetime(2026, 9, 8), "7.3.0")]

truth: list[dict] = []


def app_version(platform: str, ts: datetime, user_lag_days: int) -> str:
    """Users update with a lag, so old versions linger."""
    releases = ANDROID_RELEASES if platform == "android" else IOS_RELEASES
    seen = ts - timedelta(days=user_lag_days)
    v = releases[0][1]
    for d, ver in releases:
        if d <= seen:
            v = ver
    return v


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("-")[0].split("."))


def jitter(lat: float, lon: float, km: float) -> tuple[float, float]:
    r = km * math.sqrt(RNG.random())
    a = RNG.random() * 2 * math.pi
    return (lat + (r / 111.0) * math.cos(a), lon + (r / (111.0 * math.cos(math.radians(lat)))) * math.sin(a))


def rand_ts(lo: datetime, hi: datetime) -> datetime:
    return lo + timedelta(seconds=RNG.random() * max(1.0, (hi - lo).total_seconds()))


def install_time() -> datetime:
    # installs grow through the year, with a summer peak
    while True:
        t = rand_ts(START, END)
        doy = (t - START).days
        w = 0.6 + 0.4 * doy / 300 + 0.35 * math.exp(-((doy - 180) / 45) ** 2)
        if RNG.random() < w / 1.4:
            return t


def phone() -> str:
    return "+31 6 " + "".join(str(RNG.randint(0, 9)) for _ in range(8))


FIRST = ["anna", "lars", "sophie", "tom", "eva", "daan", "julia", "noah", "mila", "sem", "lucas", "emma"]
NOTE_OK = ["Sunrise hike, worth the 5am alarm", "Best noodles of the trip", "Rain all day, museums instead",
           "Ferry to the islands", "Met two Dutch travellers at the hostel", "Night train, slept surprisingly well",
           "Rented scooters", "Market day", "Camping by the lake", "Day 3 and still jetlagged"]


def step_text(uid: str) -> tuple[str | None, str | None]:
    """Free text note. A few leak contact details, which is what real users do."""
    r = RNG.random()
    name = RNG.choice(FIRST)
    if r < 0.004:
        return f"Lost my charger, if found mail {name}.{RNG.randint(10,99)}@gmail.com", "pii_email"
    if r < 0.006:
        return f"Guide Ketut, book him on WhatsApp {phone()}", "pii_phone"
    if r < 0.007:  # obfuscated: a regex scanner is not expected to catch these
        return f"Text me on whatsapp plus three one six {' '.join(RNG.choice(['two','four','seven','one']) for _ in range(4))}", "pii_obfuscated"
    if r < 0.45:
        return RNG.choice(NOTE_OK), None
    return None, None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    installs, homes = [], []
    skan: dict[tuple, int] = {}
    spend_installs: dict[tuple, int] = {}
    n_trip = n_step = n_book = 0

    def emit(name: str, u: dict, ts: datetime, props: dict, issue: str | None = None) -> dict:
        lag = RNG.random() ** 2 * 30
        ev = {"event": name, "event_id": f"e{len(events):08d}", "user_id": u["user_id"], "device_id": u["device_id"],
              "ip": f"{IP_PREFIX[u['country']]}.{RNG.randint(0,255)}.{RNG.randint(0,255)}.{RNG.randint(1,254)}",
              "platform": u["platform"], "app_version": app_version(u["platform"], ts, u["lag"]),
              "event_ts": ts.isoformat(timespec="seconds"),
              "received_ts": (ts + timedelta(minutes=lag)).isoformat(timespec="seconds"), "properties": props}
        events.append(ev)
        if issue:
            truth.append({"event_id": ev["event_id"], "issue": issue})
        return ev

    for i in range(N_INSTALLS):
        platform = "ios" if RNG.random() < 0.55 else "android"
        chans = {k: v for k, v in CHANNELS.items() if not (k == "apple_search_ads" and platform == "android")}
        channel = RNG.choices(list(chans), weights=[v["w"] for v in chans.values()])[0]
        campaign = RNG.choice(CAMPAIGNS[channel]) if channel != "organic" else None
        country = RNG.choices(list(COUNTRY_W), weights=list(COUNTRY_W.values()))[0]
        city, clat, clon = RNG.choice(HOME_CITIES[country])
        hlat, hlon = jitter(clat, clon, 9)
        t_install = install_time()
        # Apple Search Ads attributes through AdServices, which does not need ATT consent
        att = platform == "android" or channel == "apple_search_ads" or RNG.random() < 0.35
        u = {"user_id": f"u_{i+1:06d}", "device_id": f"d-{RNG.getrandbits(64):016x}", "platform": platform,
             "country": country, "lag": int(RNG.random() ** 3 * 60)}
        signed_up = RNG.random() < 0.72
        net_campaign = f"{channel}_{platform}_{campaign}" if campaign else None
        if channel != "organic":
            spend_installs[(t_install.date(), channel, net_campaign)] = spend_installs.get((t_install.date(), channel, net_campaign), 0) + 1
        # what the attribution provider reports at user level
        reported_source = channel if att else "organic"   # iOS without ATT consent shows up as organic
        installs.append({"appsflyer_id": f"af-{RNG.getrandbits(48):012x}", "customer_user_id": u["user_id"] if signed_up else "",
                         "platform": platform, "install_ts": t_install.isoformat(timespec="seconds"),
                         "media_source": reported_source, "campaign": net_campaign if att else "",
                         "att_status": "n/a" if platform == "android" else ("authorized" if att else "denied"),
                         "country_code": country})
        homes.append({"user_id": u["user_id"], "home_lat": hlat, "home_lon": hlon, "true_channel": channel})

        cv = 0
        if signed_up:
            t_signup = t_install + timedelta(minutes=RNG.expovariate(1 / 30))
            method = RNG.choices(["email", "apple", "google", "facebook"], weights=[0.35, 0.3 if platform == "ios" else 0.02, 0.3, 0.05])[0]
            issue = None
            if method == "apple" and platform == "ios" and app_version("ios", t_signup, u["lag"]).startswith("7.2"):
                method, issue = "Apple", "enum_case"
            emit("user_signed_up", u, t_signup, {"signup_method": method, "email": f"{RNG.choice(FIRST)}{i}@example.com",
                                                 "marketing_consent": RNG.random() < 0.58}, issue)
            cv = 1
            activates = RNG.random() < CHANNELS[channel]["activate"]
            n_trips = (1 + int(RNG.expovariate(0.9))) if activates else (1 if RNG.random() < 0.12 else 0)
            t = t_signup + timedelta(hours=RNG.uniform(0.2, 48) if activates else RNG.uniform(200, 2000))
            for k in range(n_trips):
                if t > END:
                    break
                if t - t_signup < timedelta(hours=24):
                    cv = 2
                n_trip += 1
                trip_id = f"t{n_trip:07d}"
                name_ev = "trip_created"
                issue = None
                if platform == "android" and vtuple(app_version("android", t, u["lag"])) < (5, 10):
                    name_ev, issue = "TripCreated", "legacy_name"
                dest = RNG.choice(DESTS)
                emit(name_ev, u, t, {"trip_id": trip_id, "planned_duration_days": RNG.choice([3, 5, 7, 10, 14, 21, 30]),
                                     "is_public": RNG.random() < 0.4, "trip_name": f"{dest[0]} {t.year}"}, issue)
                # steps: start at home (80%) or the airport, travel, maybe come home
                n_steps = RNG.randint(3, 14)
                ts = t + timedelta(hours=RNG.uniform(1, 72))
                pts = [(*jitter(hlat, hlon, 0.05), u["country"]) if RNG.random() < 0.8 else (*jitter(hlat, hlon, 15), u["country"])]
                dlat, dlon = jitter(dest[1], dest[2], 20)
                for _ in range(n_steps):
                    dlat, dlon = jitter(dlat, dlon, 60)
                    pts.append((dlat, dlon, dest[0]))
                if RNG.random() < 0.6:
                    pts.append((*jitter(hlat, hlon, 0.05), u["country"]))
                for lat, lon, cc in pts:
                    if ts > END:
                        break
                    n_step += 1
                    step_id = f"s{n_step:08d}"
                    has_photo = RNG.random() < 0.55
                    txt, txt_issue = step_text(u["user_id"])
                    props = {"trip_id": trip_id, "step_id": step_id, "lat": round(lat, 6), "lon": round(lon, 6),
                             "country_code": cc, "has_photo": has_photo}
                    if txt:
                        props["step_text"] = txt
                    issue = txt_issue
                    ver = app_version(platform, ts, u["lag"])
                    if platform == "android" and ver == "5.12.0":
                        props.pop("trip_id")
                        issue = "missing_required"
                    if platform == "ios" and ver == "7.3.0" and RNG.random() < 0.3:
                        props["ai_caption"] = "Golden hour over the old town"
                        issue = issue or "undeclared_property"
                    emit("step_added", u, ts, props, issue)
                    if has_photo:
                        emit("photo_uploaded", u, ts + timedelta(seconds=RNG.randint(5, 600)),
                             {"trip_id": trip_id, "step_id": step_id, "photo_count": RNG.randint(1, 12)})
                    ts += timedelta(hours=RNG.uniform(6, 60))
                if RNG.random() < 0.18:
                    emit("trip_shared", u, ts, {"trip_id": trip_id, "channel": RNG.choice(["whatsapp", "instagram", "link", "email"])})
                if RNG.random() < 0.07 and ts + timedelta(days=10) < END:
                    n_book += 1
                    price = RNG.choice([39.95, 49.95, 64.95, 89.95])
                    t_book = ts + timedelta(days=RNG.uniform(1, 10))
                    ver = app_version(platform, t_book, u["lag"])
                    issue = None
                    if platform == "android" and ver.startswith("5.11") and u["country"] in ("NL", "DE", "FR", "ES", "IT", "BE", "DK"):
                        price, issue = f"{price:.2f}".replace(".", ","), "type_error"
                    emit("travel_book_ordered", u, t_book, {"book_id": f"b{n_book:06d}", "trip_id": trip_id,
                                                            "price_eur": price, "pages": RNG.choice([24, 48, 96, 150])}, issue)
                t = ts + timedelta(days=RNG.uniform(40, 200))

        if platform == "ios" and not att and channel != "organic":
            d = (t_install + timedelta(hours=RNG.uniform(24, 60))).date()
            key = (d, channel, net_campaign, cv)
            skan[key] = skan.get(key, 0) + 1

    # QA build leaking a test event into production
    qa_user = {"user_id": "u_qa_0001", "device_id": "d-qa", "platform": "android", "country": "NL", "lag": 0}
    for _ in range(320):
        ev = emit("trip_create_v2_test", qa_user, rand_ts(datetime(2026, 9, 20), datetime(2026, 9, 27)), {"trip_id": "t_qa"}, "unknown_event")
        ev["app_version"] = "0.0.0-qa"

    # device clocks set in the future
    labelled = {t["event_id"] for t in truth}
    for ev in RNG.sample(events, 600):
        if ev["event_id"] in labelled:
            continue
        ev["event_ts"] = (datetime.fromisoformat(ev["event_ts"]) + timedelta(days=RNG.randint(30, 400))).isoformat(timespec="seconds")
        truth.append({"event_id": ev["event_id"], "issue": "future_timestamp"})

    # offline retries: same event sent twice
    dups = [dict(e) for e in RNG.sample(events, int(len(events) * 0.015))]
    for d in dups:
        d["received_ts"] = (datetime.fromisoformat(d["received_ts"]) + timedelta(minutes=RNG.randint(1, 900))).isoformat(timespec="seconds")
        truth.append({"event_id": d["event_id"], "issue": "duplicate"})
    events.extend(dups)
    events.sort(key=lambda e: e["received_ts"])

    with open(OUT / "app_events.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def write_csv(name: str, rows: list[dict]) -> None:
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    write_csv("installs.csv", installs)
    write_csv("homes.csv", homes)
    write_csv("truth.csv", truth)
    write_csv("skan_postbacks.csv", [{"postback_date": d.isoformat(), "network": n, "campaign": c, "conversion_value": cv, "installs": k}
                                     for (d, n, c, cv), k in sorted(skan.items(), key=lambda x: (x[0][0], x[0][1], x[0][2], x[0][3]))])
    spend = []
    for (d, n, c), k in sorted(spend_installs.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
        spend.append({"date": d.isoformat(), "network": n, "campaign": c,
                      "spend_eur": round(k * CHANNELS[n]["cpi"] * RNG.uniform(0.85, 1.15), 2), "impressions": k * RNG.randint(250, 900)})
    write_csv("ad_spend.csv", spend)
    print(f"raw: {len(events):,} events, {len(installs):,} installs, {n_trip:,} trips, {len(truth):,} planted problems")


if __name__ == "__main__":
    main()
