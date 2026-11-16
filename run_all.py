"""One command, end to end: generate -> gate -> dbt build -> privacy eval -> health report -> analyst db.
Each step is a plain function, so the same steps map one to one onto Dagster assets or an Airflow DAG."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import ask  # noqa: E402
import gate  # noqa: E402
import generate  # noqa: E402
import health  # noqa: E402
import privacy_eval  # noqa: E402


def dbt_build() -> None:
    (ROOT / "warehouse").mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-m", "dbt.cli.main", "build", "--profiles-dir", "."], cwd=ROOT / "dbt",
                       capture_output=True, text=True)
    done = [line for line in r.stdout.splitlines() if "Done." in line]
    print("dbt:", done[-1].split("Done.")[-1].strip() if done else r.stdout[-2000:])
    if r.returncode != 0:
        sys.exit("dbt build failed")


STEPS = [("generate", generate.main), ("gate", gate.main), ("dbt build", dbt_build),
         ("privacy eval", privacy_eval.main), ("health report", health.main), ("analyst db", ask.build)]

if __name__ == "__main__":
    for name, fn in STEPS:
        t = time.time()
        fn()
        print(f"  [{name}] {time.time() - t:.1f}s")
    print("done: reports/health_report.md")
