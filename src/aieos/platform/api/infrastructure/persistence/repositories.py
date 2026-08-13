"""Idempotency repository. Never commits or rolls back."""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.types import BigInteger

from aieos.domains.content.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.platform.api.infrastructure.persistence.models import idempotency_records_table
from aieos.platform.idempotency.hashing import advisory_lock_key
from aieos.platform.idempotency.models import IdempotencyOutcome, IdempotencyScope


class SqlAlchemyIdempotencyRepository:
    def __init__(self, connection: Connection, execution_tenant_id) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def acquire_scope(self, scope: IdempotencyScope) -> None:
        lock_id = advisory_lock_key(scope)
        try:
            self._connection.execute(
                text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                    bindparam("k", type_=BigInteger)
                ),
                {"k": lock_id},
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, scope: IdempotencyScope) -> IdempotencyOutcome | None:
        try:
            row = (
                self._connection.execute(
                    select(idempotency_records_table).where(
                        idempotency_records_table.c.tenant_id == scope.tenant_id,
                        idempotency_records_table.c.actor_principal_id
                        == scope.principal_id,
                        idempotency_records_table.c.operation == scope.operation,
                        idempotency_records_table.c.idempotency_key_sha256
                        == scope.key_sha256,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return IdempotencyOutcome(
            tenant_id=row["tenant_id"],
            principal_id=row["actor_principal_id"],
            operation=row["operation"],
            key_sha256=row["idempotency_key_sha256"].strip(),
            request_fingerprint_sha256=row["request_fingerprint_sha256"].strip(),
            result_content_id=row["result_content_id"],
            result_version_id=row["result_version_id"],
            result_aggregate_revision=int(row["result_aggregate_revision"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def insert(self, outcome: IdempotencyOutcome) -> None:
        try:
            self._connection.execute(
                idempotency_records_table.insert().values(
                    idempotency_record_id=uuid.uuid7(),
                    tenant_id=outcome.tenant_id,
                    actor_principal_id=outcome.principal_id,
                    operation=outcome.operation,
                    idempotency_key_sha256=outcome.key_sha256,
                    request_fingerprint_sha256=outcome.request_fingerprint_sha256,
                    result_content_id=outcome.result_content_id,
                    result_version_id=outcome.result_version_id,
                    result_aggregate_revision=outcome.result_aggregate_revision,
                    created_at=outcome.created_at,
                    expires_at=outcome.expires_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
