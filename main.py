from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "dependencies"))

import pandas as pd

from vg2c.runtime import JobRuntime

# Keep AED enabled in test mode; lot_status is read-only.
TEST_MODE_EXCLUDED = {"email", "mms"}
PRODUCT_CONFIG_ROOT = Path(
    r"\\AZatshfs.intel.com\AZatanalysis$\MAOATM\Config\VF_POR_Cfg"
    r"\ICM_PCS\NGICMPCS_JDSC_BAR_SKIP"
)


def load_target_products(job):
    if job.site is None:
        raise ValueError("A site is required to load prod.txt")
    root = Path(job.environ.get("PRODUCT_CONFIG_ROOT", PRODUCT_CONFIG_ROOT))
    config = root / job.site / "prod.txt"
    products = list(
        dict.fromkeys(
            line.strip().upper()
            for line in config.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        )
    )
    if not products:
        raise ValueError(f"Product allowlist is empty: {config}")
    job.write_file(
        "target_products.csv",
        pd.DataFrame({"prodgroup3": products}).to_csv(index=False),
    )


def run():
    job = JobRuntime.from_script(
        __file__, site="KM", test_mode_excluded=TEST_MODE_EXCLUDED
    )
    # job.environ["NAME"] = ""
    # job.environ["PW"] = ""
    load_target_products(job)
    interventions = job.sql(
        "sql/query_001_first_lot_after_ept_lots.sql",
        output="ept_interventions.csv",
        database="MARS",
    )
    event_ends = pd.to_datetime(
        interventions["event_end"], format="%Y-%m-%d %H:%M:%S"
    ).dropna()
    job.macros["LOT_HISTORY_START"] = min(
        event_ends.dt.strftime("%Y%m%d%H%M%S"), default=None
    )
    job.sql(
        "sql/query_002_lot_history.sql",
        output="lot_history.csv",
        database="MARS",
    )
    job.sql(
        "sql/query_003_match_first_lots.sql",
        output="first_lot_after_ept_lots.csv",
        inputs=[
            ("ept_interventions.csv", "interventions"),
            ("lot_history.csv", "lot_history"),
        ],
    )

    first_lot_after_ept_df = job.read_csv("first_lot_after_ept_lots.csv")["lot"]
    for lot_num in first_lot_after_ept_df:
        print(job.aed.lot_status(lot_num))


def handle(job_id=None, context=None, header=None, body=None):
    run()
    return True, "Function executed successfully", "Function executed successfully"


if __name__ == "__main__":
    run()
