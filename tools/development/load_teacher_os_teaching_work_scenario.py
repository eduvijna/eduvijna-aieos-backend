"""CLI: load the TOS-DEV02 Teaching Work development reference scenario.

NON_PRODUCTION. Explicit invocation only. Never runs on application startup,
migration, worker startup, or production composition.

Example:

  uv run python tools/development/load_teacher_os_teaching_work_scenario.py \\
    --database-url postgresql+psycopg://aieos_runtime:...@127.0.0.1:55432/aieos

Evidence JSON is written under tmp/ (gitignored) unless --report-path is set.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aieos.development.app_factory import build_development_review_scenario_app  # noqa: E402
from aieos.development.teacher_os_teaching_work_scenario import (  # noqa: E402
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    ensure_teacher_os_teaching_work_scenario,
    write_scenario_report,
)


def _parse_scenario_date(raw: str) -> date:
    return date.fromisoformat(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "NON_PRODUCTION: seed synthetic Teaching Work reference rows via "
            "existing application HTTP contracts and read Today's Mission."
        )
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy URL for the runtime PostgreSQL role (NON_PRODUCTION).",
    )
    parser.add_argument(
        "--scenario-date",
        type=_parse_scenario_date,
        default=None,
        help=(
            "Local educational day as YYYY-MM-DD. Work target_date is the next "
            "day. Defaults to the current UTC date."
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=REPO_ROOT / "tmp" / "tos-dev02-teaching-work-scenario.json",
        help="Where to write the non-secret scenario evidence report.",
    )
    args = parser.parse_args()

    scenario_date = (
        args.scenario_date
        if args.scenario_date is not None
        else datetime.now(UTC).date()
    )

    engine = create_engine(args.database_url)
    app = build_development_review_scenario_app(
        engine,
        tenant_id=SYNTHETIC_TENANT_ID,
        principal_id=SYNTHETIC_PRINCIPAL_ID,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        report = ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=SYNTHETIC_PRINCIPAL_ID,
            scenario_date=scenario_date,
        )
    write_scenario_report(report, args.report_path)
    print(f"scenario_id={report.scenario_id}")
    print(f"tenant_id={report.tenant_id}")
    print(f"mission_date={report.mission_date}")
    print(f"target_date={report.target_date}")
    print(f"reused_existing={report.reused_existing}")
    print(f"hero_action={report.mission_hero_action_kind}")
    print(f"pending_review_count={report.mission_pending_review_count}")
    print(f"active_work_count={report.mission_active_work_count}")
    print(f"report={args.report_path}")
    for work in report.works:
        print(f"  {work.key}: work_id={work.work_id} target_date={work.target_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
