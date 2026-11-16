import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gate import Gate, apply_location_policy, redact  # noqa: E402
from plan_diff import diff  # noqa: E402

PLAN = yaml.safe_load((ROOT / "tracking_plan" / "tracking_plan.yml").read_text(encoding="utf-8"))


def ev(name="trip_created", **props):
    base = {"event": name, "event_id": "e1", "user_id": "u_000001", "device_id": "d-1", "ip": "10.1.2.3",
            "platform": "android", "app_version": "5.13.0", "event_ts": "2026-08-01T10:00:00",
            "received_ts": "2026-08-01T10:01:00",
            "properties": props or {"trip_id": "t1", "is_public": True, "trip_name": "Portugal 2026"}}
    return base


def test_accepts_valid_event_and_pseudonymizes():
    status, rec = Gate(PLAN).process(ev())
    assert status == "accepted"
    assert rec["user_key"] != "u_000001" and len(rec["user_key"]) == 20
    assert rec["geo_country"] == "NL" and "ip" not in rec


def test_alias_is_repaired_and_logged():
    g = Gate(PLAN)
    status, rec = g.process(ev("TripCreated"))
    assert status == "accepted" and rec["event_name"] == "trip_created"
    assert g.log[0]["action"] == "renamed_alias"


def test_unknown_event_is_quarantined():
    status, rec = Gate(PLAN).process(ev("trip_create_v2_test", trip_id="t"))
    assert status == "quarantined" and rec["reason"] == "unknown_event"


def test_missing_required_is_quarantined_without_location():
    status, rec = Gate(PLAN).process(ev("step_added", step_id="s1", lat=52.37, lon=4.89, country_code="NL", has_photo=False))
    assert status == "quarantined" and "missing_required:trip_id" in rec["reason"]
    assert "lat" not in rec["payload"]


def test_type_error_is_quarantined():
    status, rec = Gate(PLAN).process(ev("travel_book_ordered", book_id="b", trip_id="t", price_eur="39,95", pages=48))
    assert status == "quarantined" and rec["reason"] == "type_error:price_eur"


def test_enum_case_normalized_and_email_dropped():
    g = Gate(PLAN)
    status, rec = g.process(ev("user_signed_up", signup_method="Apple", email="a@b.com", marketing_consent=True))
    assert status == "accepted" and rec["props"]["signup_method"] == "apple"
    assert "email" not in rec["props"]


def test_free_text_redaction():
    text, hits = redact("mail anna.12@gmail.com or call +31 6 12345678")
    assert "@" not in text and "12345678" not in text and set(hits) == {"email", "phone"}
    assert redact("Day 3 and still jetlagged")[1] == []


def test_duplicate_and_clock():
    g = Gate(PLAN)
    e = ev()
    e["event_ts"] = "2027-03-01T00:00:00"
    assert g.process(e)[1]["event_ts"] == e["received_ts"]
    assert g.process(copy.deepcopy(e))[0] == "duplicate"


def test_undeclared_property_dropped():
    g = Gate(PLAN)
    status, rec = g.process(ev("trip_created", trip_id="t", is_public=False, ai_caption="x"))
    assert status == "accepted" and "ai_caption" not in rec["props"]
    assert any(l["action"] == "undeclared_property" for l in g.log)


def test_home_zone_suppressed_rest_rounded():
    g = Gate(PLAN)
    home, away = (52.370001, 4.890001), (38.722345, -9.139876)
    pts = [home, away, (38.80, -9.20), (52.3705, 4.8905)]
    acc = [{"event_id": f"e{i}", "event_name": "step_added", "user_key": "k", "event_ts": f"2026-08-0{i+1}",
            "props": {"trip_id": "t", "lat": la, "lon": lo}} for i, (la, lo) in enumerate(pts)]
    apply_location_policy(acc, g)
    assert acc[0]["props"]["location_suppressed"] and acc[0]["props"]["lat"] is None
    assert acc[3]["props"]["location_suppressed"]
    assert acc[1]["props"]["lat"] == 38.72 and not acc[1]["props"]["location_suppressed"]


def test_plan_diff_classifies():
    new = copy.deepcopy(PLAN)
    new["events"]["step_added"]["properties"]["ai_caption"] = {"type": "string", "required": False, "pii": "free_text"}
    assert [k for k, _ in diff(PLAN, new)] == ["additive"]
    new["events"]["step_added"]["properties"]["lat"]["pii"] = "none"
    new["events"]["travel_book_ordered"]["properties"].pop("pages")
    kinds = {k for k, _ in diff(PLAN, new)}
    assert {"breaking", "privacy_review"} <= kinds
