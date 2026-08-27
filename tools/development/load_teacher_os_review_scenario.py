"""CLI: load TOS-DEV01 Teacher OS Review Queue development reference scenario.

NON_PRODUCTION. Explicit invocation only. Never runs on application startup,
migration, worker startup, or production composition.

Example:

  uv run python tools/development/load_teacher_os_review_scenario.py \\
    --database-url postgresql+psycopg://aieos_runtime:...@127.0.0.1:55432/aieos

Evidence JSON is written under tmp/ (gitignored) unless --report-path is set.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aieos.development.app_factory import build_development_review_scenario_app  # noqa: E402
from aieos.development.teacher_os_review_scenario import (  # noqa: E402
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    ensure_teacher_os_review_scenario,
    write_scenario_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "NON_PRODUCTION: seed synthetic Teacher OS Review Queue reference "
            "artifacts via existing application HTTP contracts."
        )
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy URL for the runtime PostgreSQL role (NON_PRODUCTION).",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=REPO_ROOT / "tmp" / "tos-dev01-review-scenario.json",
        help="Where to write the non-secret scenario evidence report.",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    app = build_development_review_scenario_app(
        engine,
        tenant_id=SYNTHETIC_TENANT_ID,
        principal_id=SYNTHETIC_PRINCIPAL_ID,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        report = ensure_teacher_os_review_scenario(
            client,
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=SYNTHETIC_PRINCIPAL_ID,
        )
    write_scenario_report(report, args.report_path)
    print(f"scenario_id={report.scenario_id}")
    print(f"tenant_id={report.tenant_id}")
    print(f"reused_existing={report.reused_existing}")
    print(f"artifacts={len(report.artifacts)}")
    print(f"report={args.report_path}")
    for artifact in report.artifacts:
        print(
            f"  {artifact.key}: content_id={artifact.content_id} "
            f"version_id={artifact.version_id} action={artifact.intended_action}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
