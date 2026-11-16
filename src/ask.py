"""Claude as an analyst over the governed marts, with guardrails that do not depend on the prompt.

Guardrails (enforced in code, not by asking the model nicely):
  1. Claude only ever sees warehouse/analyst.duckdb: a copy of the four marts. No staging, no quarantine,
     no gate log, no free text. It is opened read only with external access off (no read_csv of raw files).
  2. One SELECT statement per call. Anything else is rejected before it runs.
  3. Row level keys (user_key, trip_id) cannot appear in a result: aggregate first.
  4. Small cells: any count column under 10 is returned as "<10".
  5. At most 200 rows back.

Usage:
  python src/ask.py --build                      build analyst.duckdb from the warehouse
  python src/ask.py --sql "select ..."           run one query through the guard (no API call)
  python src/ask.py "Which iOS channel has the best cost per signup?"   needs ANTHROPIC_API_KEY or `ant auth login`
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "warehouse" / "polarsteps.duckdb"
ANALYST_DB = ROOT / "warehouse" / "analyst.duckdb"
MARTS = ["dim_travelers", "fct_trips", "fct_retention_weekly", "fct_channel_performance"]
ROW_KEYS = {"user_key", "trip_id", "device_key", "install_key"}
MIN_CELL = 10
MAX_ROWS = 200
MODEL = "claude-opus-5-5"


class Rejected(Exception):
    pass


def build() -> None:
    ANALYST_DB.unlink(missing_ok=True)
    con = duckdb.connect(str(ANALYST_DB))
    con.execute(f"attach '{WAREHOUSE.as_posix()}' as w (read_only)")
    for t in MARTS:
        con.execute(f"create table {t} as select * from w.marts.{t}")
    con.close()
    print(f"ask: analyst.duckdb built with {len(MARTS)} marts")


def is_count_col(name: str) -> bool:
    return name.startswith("n_") or name in {"travelers", "active_travelers", "cohort_size", "signups", "installs"} \
        or name.endswith(("_count", "_travelers", "_signups", "_installs"))


def run_sql(sql: str) -> dict:
    stmts = duckdb.extract_statements(sql)
    if len(stmts) != 1:
        raise Rejected("exactly one statement per call")
    if stmts[0].type != duckdb.StatementType.SELECT:
        raise Rejected(f"only SELECT is allowed, got {stmts[0].type.name}")
    # no file or network access: blocks read_csv('data/raw/...'), read_parquet, attach, httpfs
    con = duckdb.connect(str(ANALYST_DB), read_only=True, config={"enable_external_access": False})
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        leaked = ROW_KEYS & {c.lower() for c in cols}
        if leaked:
            raise Rejected(f"row level key in result ({', '.join(sorted(leaked))}); aggregate instead")
        rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    masked = 0
    out = []
    for r in rows[:MAX_ROWS]:
        rec = {}
        for c, v in zip(cols, r):
            if is_count_col(c.lower()) and isinstance(v, int) and 0 < v < MIN_CELL:
                v, masked = f"<{MIN_CELL}", masked + 1
            rec[c] = v if isinstance(v, (int, float, str, bool, type(None))) else str(v)
        out.append(rec)
    return {"columns": cols, "rows": out, "truncated": len(rows) > MAX_ROWS, "cells_masked": masked}


def schema_doc() -> str:
    """Model and column descriptions come from the dbt yml, so the docs Claude reads are the docs humans read."""
    docs = yaml.safe_load((ROOT / "dbt" / "models" / "marts" / "_marts.yml").read_text(encoding="utf-8"))
    desc = {m["name"]: m for m in docs["models"]}
    con = duckdb.connect(str(ANALYST_DB), read_only=True)
    parts = []
    for t in MARTS:
        col_desc = {c["name"]: c.get("description", "") for c in desc[t].get("columns", [])}
        cols = con.execute(f"describe {t}").fetchall()
        lines = [f"  {c[0]} {c[1]}" + (f"  -- {col_desc[c[0]]}" if col_desc.get(c[0]) else "") for c in cols]
        parts.append(f"{t}: {desc[t]['description']}\n" + "\n".join(lines))
    con.close()
    return "\n\n".join(parts)


SYSTEM = """You are the analyst for Polarsteps' Product and Growth teams. Answer questions with the run_sql tool
against DuckDB. Only these tables exist:

{schema}

Rules the tool enforces: one SELECT per call, no user_key or trip_id in results (aggregate), counts under 10
come back as "<10", 200 rows max. If a question needs data that is not in these tables (raw events,
locations, free text, emails), say so plainly instead of approximating it. Finish with a short answer:
the number, what it means, and the SQL you relied on."""

TOOLS = [{
    "name": "run_sql",
    "description": "Run one read only DuckDB SELECT against the governed marts and return rows as JSON.",
    "strict": True,
    "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
                     "additionalProperties": False},
}]


def ask(question: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]
    system = SYSTEM.format(schema=schema_doc())
    for _ in range(8):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, tools=TOOLS, messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return "Declined: " + (resp.stop_details.explanation if resp.stop_details else "no detail")
        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            return "".join(b.text for b in resp.content if b.type == "text")
        results = []
        for b in calls:
            sql = b.input.get("sql", "") if isinstance(b.input, dict) else ""
            print(f"  sql> {' '.join(sql.split())[:160]}")
            try:
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(run_sql(sql), default=str)})
            except (Rejected, duckdb.Error) as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"rejected: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    return "Stopped after 8 tool rounds."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--sql")
    ap.add_argument("--build", action="store_true")
    a = ap.parse_args()
    if a.build:
        build()
    if a.sql:
        try:
            print(json.dumps(run_sql(a.sql), indent=2, default=str))
        except (Rejected, duckdb.Error) as e:
            print(f"rejected: {e}")
    if a.question:
        print(ask(a.question))


if __name__ == "__main__":
    main()
