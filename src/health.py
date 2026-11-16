"""Stack health report: what the gate caught, what broke, what it costs, what to fix next.
Writes reports/health_report.md and reports/step_volume_android.png.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOV, RAW, REP = ROOT / "data" / "governed", ROOT / "data" / "raw", ROOT / "reports"

# what "handled correctly" means for each planted problem
EXPECTED = {
    "duplicate":           ("log", "duplicate_dropped", "dropped (same event_id)"),
    "legacy_name":         ("log", "renamed_alias", "renamed via plan alias"),
    "enum_case":           ("log", "normalized_enum", "normalized via plan rule"),
    "future_timestamp":    ("log", "clock_clamped", "clamped to received time"),
    "undeclared_property": ("log", "undeclared_property", "property dropped, flagged for a plan PR"),
    "pii_email":           ("log", "redacted_email", "redacted"),
    "pii_phone":           ("log", "redacted_phone", "redacted"),
    "pii_obfuscated":      ("log", "redacted_", "not caught: phone number spelled out in words (known regex gap)"),
    "missing_required":    ("quarantine", "missing_required", "quarantined"),
    "type_error":          ("quarantine", "type_error", "quarantined"),
    "unknown_event":       ("quarantine", "unknown_event", "quarantined"),
}


def md_table(df) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(f"{v:,}" if isinstance(v, int) else str(v) for v in row) + " |")
    return "\n".join(lines)


def main() -> None:
    REP.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"attach '{(ROOT / 'warehouse' / 'polarsteps.duckdb').as_posix()}' as w (read_only)")
    for name in ("quarantine", "gate_log"):
        con.execute(f"create view {name} as select * from '{(GOV / f'{name}.parquet').as_posix()}'")
    con.execute(f"create view truth as select * from read_csv('{(RAW / 'truth.csv').as_posix()}')")

    # 1. scorecard: planted problems vs what the gate did with them
    rows = []
    for issue, (where, key, label) in EXPECTED.items():
        planted = con.execute("select count(*) from truth where issue = ?", [issue]).fetchone()[0]
        if where == "log":
            caught = con.execute(f"""select count(*) from (select event_id from truth where issue = ?) t
                where exists (select 1 from gate_log g where g.event_id = t.event_id and g.action like '{key}%')""", [issue]).fetchone()[0]
        else:
            caught = con.execute(f"""select count(*) from (select event_id from truth where issue = ?) t
                where exists (select 1 from quarantine q where q.event_id = t.event_id and q.reason like '%{key}%')""", [issue]).fetchone()[0]
        if issue == "duplicate":  # a duplicate's first copy is kept by design; count dropped copies
            caught = min(planted, con.execute("select count(*) from gate_log where action = 'duplicate_dropped'").fetchone()[0])
        rows.append({"planted problem": issue, "events": planted, "handled": caught, "rate": f"{100 * caught / planted:.0f}%", "action": label})
    import pandas as pd
    score = pd.DataFrame(rows)

    # 2. volume anomaly: accepted step_added per day and platform vs trailing 14 day median
    vol = con.execute("""
        with d as (
            select platform, cast(stepped_at as date) as day, count(*) as n
            from w.staging.stg_step_added group by all
        ), m as (
            select *, median(n) over (partition by platform order by day rows between 14 preceding and 1 preceding) as base
            from d
        )
        select platform, day, n, base, round(n / base, 2) as ratio from m where day >= '2026-02-01' order by platform, day
    """).df()
    alerts = vol[(vol.ratio < 0.5) & (vol.base >= 30)]
    first_alert = alerts.sort_values("day").head(1)
    qd = con.execute("""select cast(cast(event_ts as timestamp) as date) as day, count(*) n from quarantine
                        where platform = 'android' and reason like 'missing_required%' group by 1 order by 1""").df()

    # quarantine rate monitor: per platform + app version per day, alert above 5% on at least 20 events
    qrate = con.execute("""
        with q as (select platform, app_version, cast(cast(event_ts as timestamp) as date) as day, count(*) as q
                   from quarantine group by all),
        a as (select platform, app_version, cast(event_ts as date) as day, count(*) as a
              from w.staging.stg_events group by all)
        , alerts as (
            select platform, app_version, day, round(q / (q + coalesce(a, 0)), 3) as rate
            from q left join a using (platform, app_version, day)
            where q + coalesce(a, 0) >= 20 and q / (q + coalesce(a, 0)) > 0.05
        )
        select platform, app_version, min(day) as first_alert, count(*) as alert_days,
               arg_min(rate, day) as rate_on_first_day
        from alerts group by all order by first_alert
    """).df()
    qrate_512 = qrate[qrate.app_version == "5.12.0"].iloc[0]

    a = vol[vol.platform == "android"]
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=130)
    ax.plot(a.day, a.n, color="#2f6db5", lw=1.4, label="step_added accepted (Android)")
    ax.plot(qd.day, qd.n, color="#d1495b", lw=1.4, label="step_added quarantined: missing trip_id")
    ax.axvspan(pd.Timestamp("2026-08-04"), pd.Timestamp("2026-08-18"), color="#d1495b", alpha=0.08, lw=0)
    if len(first_alert):
        ax.annotate("first volume alert", (first_alert.day.iloc[0], first_alert.n.iloc[0]), xytext=(-110, 40),
                    textcoords="offset points", arrowprops={"arrowstyle": "->", "color": "#444"}, fontsize=8)
    qa = qrate[qrate.app_version == "5.12.0"].first_alert.iloc[0]
    qy = qd[qd.day == qa].n.iloc[0] if (qd.day == qa).any() else 0
    ax.annotate("quarantine rate alert", (qa, qy), xytext=(-130, -10), textcoords="offset points",
                arrowprops={"arrowstyle": "->", "color": "#d1495b"}, fontsize=8, color="#d1495b")
    ax.text(pd.Timestamp("2026-08-05"), ax.get_ylim()[1] * 0.92, "5.12.0 live", fontsize=8, color="#d1495b")
    ax.set_xlim(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-31"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_ylabel("events per day", fontsize=8)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    fig.savefig(REP / "step_volume_android.png")

    by_version = con.execute("""
        with q as (select platform, app_version, reason, count(*) n from quarantine group by all)
        select platform, app_version, reason, n from q order by n desc limit 6
    """).df()
    undeclared = con.execute("""
        select g.detail as property, count(*) as events
        from gate_log g where action = 'undeclared_property' group by 1 order by 2 desc
    """).df()

    # 3. attribution: what ignoring SKAN does to iOS paid CPI
    cpi = con.execute("""
        select channel, sum(spend_eur)::int as spend_eur, sum(installs_user_level)::int as installs_reported,
               sum(installs_total)::int as installs_with_skan,
               round(sum(spend_eur) / nullif(sum(installs_user_level), 0), 2) as cpi_reported,
               round(sum(spend_eur) / nullif(sum(installs_total), 0), 2) as cpi_with_skan
        from w.marts.fct_channel_performance where platform = 'ios' group by 1 order by spend_eur desc
    """).df()

    # 4. dbt results and storage
    rr = json.loads((ROOT / "dbt" / "target" / "run_results.json").read_text())
    status = pd.Series([r["status"] for r in rr["results"]]).value_counts().to_dict()
    storage = con.execute("""
        select schema_name || '.' || table_name as "table", estimated_size::bigint as rows, column_count as columns
        from duckdb_tables() where database_name = 'w' order by estimated_size desc
    """).df()
    sizes = {p.name: round(p.stat().st_size / 1e6, 2) for p in sorted(GOV.glob("*.parquet"))}
    priv = json.loads((REP / "privacy_eval.json").read_text())

    raw_n = sum(1 for _ in open(RAW / "app_events.jsonl", encoding="utf-8"))
    acc = con.execute("select count(*) from w.staging.stg_events").fetchone()[0]
    qn = con.execute("select count(*) from quarantine").fetchone()[0]
    dups = con.execute("select count(*) from gate_log where action = 'duplicate_dropped'").fetchone()[0]

    out = [
        "# Data stack health report",
        "",
        f"Raw events **{raw_n:,}**, accepted **{acc:,}**, quarantined **{qn:,}**, duplicates dropped "
        f"**{dups:,}**.",
        f"dbt build: {', '.join(f'{k} {v}' for k, v in sorted(status.items()))}.",
        "",
        "## 1. Gate scorecard (planted problems vs what the gate did)",
        "",
        md_table(score),
        "",
        "## 2. What broke in production",
        "",
        "![Android step volume](step_volume_android.png)",
        "",
        f"Android 5.12.0 (live 4 Aug) dropped `trip_id` from `step_added`. The gate quarantined those events instead of loading "
        f"orphan steps. The quarantine rate monitor (per app version, per day) fired on **{qrate_512.first_alert:%d %b}** "
        f"({qrate_512.rate_on_first_day:.0%} of that version's events rejected). A plain volume monitor only fired on "
        f"**{first_alert.day.iloc[0]:%d %b}** (accepted volume {first_alert.ratio.iloc[0]:.0%} of the trailing 14 day median), "
        "because users roll onto a release gradually and the median drifts down with them. Volume alerts alone are too slow "
        "for staged app releases; rejection rate by app version is the signal to page on. Users who never updated past 5.12.0 keep "
        "sending broken events after the fix: the quarantine keeps them for replay once the app team backfills `trip_id` from `step_id`.",
        "",
        "Quarantine rate alerts (all versions, more than 5% rejected on a day with 20+ events):",
        "",
        md_table(qrate.assign(first_alert=qrate.first_alert.dt.strftime("%Y-%m-%d"))),
        "",
        "Top quarantine sources:",
        "",
        md_table(by_version),
        "",
        "Undeclared properties (accepted, property dropped; needs a tracking plan PR before it can reach the warehouse):",
        "",
        md_table(undeclared),
        "",
        "## 3. Privacy",
        "",
        f"Home location found within 1 km by a simple attack (most visited place, or first trip start): "
        f"**{priv['home_found_within_1km_pct']['raw']['either']}%** of {priv['travelers']:,} travelers on raw data, "
        f"**{priv['home_found_within_1km_pct']['governed']['either']}%** on the warehouse. "
        f"Cost: {priv['steps_location_suppressed_pct']}% of steps lose coordinates (rows and countries kept); trip distance "
        f"error median {priv['trip_distance_error_pct']['median']}%, p95 {priv['trip_distance_error_pct']['p95']}%. "
        "Mixpanel receives country only; ad platforms receive hashed email for consented users only.",
        "",
        "## 4. Attribution: iOS paid channels with and without SKAN",
        "",
        md_table(cpi),
        "",
        "iOS installs without ATT consent show up as organic at user level. Reading CPI from the user level report alone "
        "overstates iOS paid CPI and credits paid installs to word of mouth. `fct_channel_performance` moves SKAN installs back.",
        "",
        "## 5. Storage (cost proxy)",
        "",
        md_table(storage),
        "",
        "Governed parquet (MB): " + ", ".join(f"{k} {v}" for k, v in sizes.items()),
        "",
    ]
    (REP / "health_report.md").write_text("\n".join(out), encoding="utf-8")
    print(f"health: report written, {len(alerts)} volume alert days, scorecard {int(score.handled.sum()):,}/{int(score.events.sum()):,} handled")


if __name__ == "__main__":
    main()
