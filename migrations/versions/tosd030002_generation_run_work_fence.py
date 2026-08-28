"""TOS-DEV03R1 GenerationRun work fence, lease, and AI materialization uniqueness.

Adds:
  * partial unique work fence on RUNNING|SUCCEEDED (FAILED releases the fence)
  * lease_expires_at for stale RUNNING recovery
  * unique binding of AI ContentVersion to generation_run_id via provenance JSON

VALIDATED remains in the status CHECK for compatibility; ordinary paths must not
leave durable VALIDATED rows (RUNNING → SUCCEEDED | FAILED only).

Revision ID: tosd030002
Revises: tosd030001
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "tosd030002"
down_revision: str | None = "tosd030001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE ai.generation_runs
        ADD COLUMN lease_expires_at TIMESTAMPTZ NULL
    """,
    """
    UPDATE ai.generation_runs
       SET lease_expires_at = COALESCE(updated_at, created_at) + INTERVAL '120 seconds'
     WHERE status = 'RUNNING'
       AND lease_expires_at IS NULL
    """,
    """
    ALTER TABLE ai.generation_runs
        ADD CONSTRAINT ck_ai_generation_runs_running_requires_lease
        CHECK (status <> 'RUNNING' OR lease_expires_at IS NOT NULL)
    """,
    """
    CREATE UNIQUE INDEX uq_ai_generation_runs_work_active_or_succeeded
        ON ai.generation_runs (tenant_id, work_resource_id)
        WHERE status IN ('RUNNING', 'SUCCEEDED')
    """,
    """
    CREATE UNIQUE INDEX uq_content_versions_ai_generation_run_id
        ON content.content_versions (
            tenant_id,
            (provenance #>> '{generation_run_ref,resource_id}')
        )
        WHERE origin = 'AI'
          AND provenance IS NOT NULL
          AND provenance ? 'generation_run_ref'
    """,
    """
    COMMENT ON COLUMN ai.generation_runs.status IS
        'Durable statuses are RUNNING, SUCCEEDED, FAILED. VALIDATED remains '
        'allowed by CHECK for compatibility but ordinary paths must not leave '
        'durable VALIDATED rows.'
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS content.uq_content_versions_ai_generation_run_id")
    op.execute("DROP INDEX IF EXISTS ai.uq_ai_generation_runs_work_active_or_succeeded")
    op.execute(
        """
        ALTER TABLE ai.generation_runs
            DROP CONSTRAINT IF EXISTS ck_ai_generation_runs_running_requires_lease
        """
    )
    op.execute(
        "ALTER TABLE ai.generation_runs DROP COLUMN IF EXISTS lease_expires_at"
    )
    op.execute("COMMENT ON COLUMN ai.generation_runs.status IS NULL")
