import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ask import ANALYST_DB, Rejected, run_sql  # noqa: E402

pytestmark = pytest.mark.skipif(not ANALYST_DB.exists(), reason="run run_all.py first")


def test_aggregate_ok():
    r = run_sql("select platform, count(*) as n_travelers from dim_travelers group by 1")
    assert len(r["rows"]) == 2


@pytest.mark.parametrize("sql", [
    "drop table dim_travelers",
    "select 1; select 2",
    "select user_key from dim_travelers",
    "select trip_id, n_steps from fct_trips",
])
def test_rejected(sql):
    with pytest.raises(Rejected):
        run_sql(sql)


def test_no_file_access():
    with pytest.raises(Exception, match="disabled"):
        run_sql("select * from read_csv('data/raw/homes.csv')")


def test_small_cells_masked():
    r = run_sql("select geo_country, count(*) as n_travelers from dim_travelers group by 1 "
                "union all select 'tiny', 3")
    assert {"geo_country": "tiny", "n_travelers": "<10"} in r["rows"]
