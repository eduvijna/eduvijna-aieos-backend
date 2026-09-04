"""PED-I10B6 PostgreSQL migration, kernel authority, and transactional audit proofs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID, uuid7

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import (
    AssetForbidden,
    AssetPersistenceFailed,
)
from aieos.domains.asset.application.mutations import AssetMutationService
from aieos.domains.asset.application.ports import ASSET_CREATE
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
)
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.domains.asset.infrastructure.persistence.uow import (
    SqlAlchemyAssetUnitOfWorkFactory,
)
from aieos.platform.security.authorization import (
    AIEOS_ASSET_CAPABILITIES,
    AuthorizationKernel,
    KernelAssetMutationAuthorization,
)
from aieos.platform.security.authorization.decisions import (
    MembershipStatus,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade, set_tenant
from tests.domains.asset.application.fakes import InMemoryBlobStore
from tests.domains.asset.application.mutation_fakes import (
    AllowAssetMutationAuthorization,
    asset_audit_kwargs,
)
from tests.domains.asset.application.test_ped_i10b5_mutations import InspectProbe
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    revoke_membership,
    seed_active_authority,
    seed_grant,
    seed_membership,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.ped_i10b6

FIXED = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
PAYLOAD = b"asset-bytes-v1"
ZERO = AssetAggregateRevision(0)


def _clock() -> datetime:
    return FIXED


def _service(runtime_engine: Engine, blobs=None, auth=None) -> AssetMutationService:
    return AssetMutationService(
        SqlAlchemyAssetUnitOfWorkFactory(runtime_engine),
        blobs if blobs is not None else InMemoryBlobStore(),
        auth if auth is not None else AllowAssetMutationAuthorization(),
        clock=_clock,
    )


def _prepared(blobs: InMemoryBlobStore) -> PreparedBlob:
    info = blobs.create(storage_key=uuid7().hex, source=BytesIO(PAYLOAD), byte_size=len(PAYLOAD))
    return PreparedBlob(
        storage_key=info.storage_key,
        byte_size=info.byte_size,
        sha256=info.sha256,
    )


def _count_assets(bootstrap_engine, asset_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM asset.assets WHERE asset_id = :id"),
                {"id": asset_id},
            ).scalar_one()
        )


def _count_revisions(bootstrap_engine, asset_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM asset.asset_revisions WHERE asset_id = :id"
                ),
                {"id": asset_id},
            ).scalar_one()
        )


def _count_asset_audits(bootstrap_engine, asset_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records "
                    "WHERE primary_resource_id = :id AND action LIKE 'asset.%'"
                ),
                {"id": asset_id},
            ).scalar_one()
        )


def _fetch_asset_audits(bootstrap_engine, asset_id: UUID) -> list[dict]:
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM security.audit_records "
                "WHERE primary_resource_id = :id AND action LIKE 'asset.%' "
                "ORDER BY occurred_at, action"
            ),
            {"id": asset_id},
        ).mappings()
        return [dict(row) for row in rows]


def _insert_raw(conn, **overrides):
    base = {
        "audit_record_id": uuid7(),
        "tenant_id": uuid7(),
        "action": "content.create",
        "primary_resource_type": "content.content",
        "primary_resource_id": uuid7(),
        "primary_resource_revision": 0,
        "resource_revision_before": None,
        "resource_revision_after": 0,
        "related_resource_refs": [],
        "initiating_principal_id": uuid7(),
        "effective_actor_id": uuid7(),
        "executing_principal_id": uuid7(),
        "delegation_id": None,
        "execution_channel": "API",
        "correlation_id": uuid7(),
        "causation_id": uuid7(),
        "trace_id": None,
        "occurred_at": FIXED,
    }
    base.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO security.audit_records (
                audit_record_id, tenant_id, action,
                primary_resource_type, primary_resource_id, primary_resource_revision,
                resource_revision_before, resource_revision_after,
                related_resource_refs,
                initiating_principal_id, effective_actor_id, executing_principal_id,
                delegation_id, execution_channel,
                correlation_id, causation_id, trace_id, occurred_at
            ) VALUES (
                :audit_record_id, :tenant_id, :action,
                :primary_resource_type, :primary_resource_id, :primary_resource_revision,
                :resource_revision_before, :resource_revision_after,
                CAST(:related_resource_refs AS jsonb),
                :initiating_principal_id, :effective_actor_id, :executing_principal_id,
                :delegation_id, :execution_channel,
                :correlation_id, :causation_id, :trace_id, :occurred_at
            )
            """
        ),
        {
            **base,
            "related_resource_refs": json.dumps(base["related_resource_refs"]),
        },
    )


def _expect_raw_failure(conn, **overrides) -> None:
    with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
        _insert_raw(conn, **overrides)
    conn.execute(text("ROLLBACK TO SAVEPOINT ped_i10b6_attempt"))
    conn.execute(text("SAVEPOINT ped_i10b6_attempt"))


def _asset_raw(**overrides):
    values = {
        "action": "asset.create",
        "primary_resource_type": "asset.image",
        "primary_resource_id": uuid7(),
        "primary_resource_revision": None,
        "resource_revision_before": None,
        "resource_revision_after": 0,
    }
    values.update(overrides)
    return values


class _FailingAudit:
    def insert(self, record) -> None:
        raise AssetPersistenceFailed("asset persistence operation failed")


class _FailingAuditUnitOfWork:
    def __init__(self, inner) -> None:
        self._inner = inner

    def __enter__(self):
        uow = self._inner.__enter__()
        uow.audit = _FailingAudit()
        return uow

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)


class _FailingAuditFactory:
    def __init__(self, engine: Engine) -> None:
        self._inner = SqlAlchemyAssetUnitOfWorkFactory(engine)

    def __call__(self, execution_tenant_id: UUID):
        return _FailingAuditUnitOfWork(self._inner(execution_tenant_id))


def _kernel(engine) -> AuthorizationKernel:
    return AuthorizationKernel(engine, known_capabilities=AIEOS_ASSET_CAPABILITIES)


class TestMigrationHeadAndContentCompatibility:
    def test_alembic_head_is_tosd060001(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )

    def test_existing_content_audit_row_still_accepted(self, bootstrap_engine) -> None:
        record_id = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(conn, audit_record_id=record_id)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": record_id},
                ).scalar_one()
                == 1
            )


class TestDatabaseConstraints:
    def test_content_primary_revision_null_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT ped_i10b6_attempt"))
                _expect_raw_failure(conn, primary_resource_revision=None)

    def test_asset_primary_revision_non_null_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT ped_i10b6_attempt"))
                _expect_raw_failure(
                    conn,
                    **_asset_raw(primary_resource_revision=0),
                )

    def test_asset_primary_revision_null_accepted(self, bootstrap_engine) -> None:
        record_id = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(conn, audit_record_id=record_id, **_asset_raw())
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT primary_resource_revision, resource_revision_before, "
                    "resource_revision_after FROM security.audit_records "
                    "WHERE audit_record_id = :id"
                ),
                {"id": record_id},
            ).one()
        assert row == (None, None, 0)

    def test_asset_create_bad_revision_pair_rejected(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SAVEPOINT ped_i10b6_attempt"))
                _expect_raw_failure(
                    conn,
                    **_asset_raw(
                        resource_revision_before=0,
                        resource_revision_after=0,
                    ),
                )
                _expect_raw_failure(
                    conn,
                    **_asset_raw(
                        resource_revision_before=None,
                        resource_revision_after=1,
                    ),
                )

    def test_asset_register_n_to_n_accepted_n_plus_one_rejected(
        self, bootstrap_engine
    ) -> None:
        accepted_id = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(
                    conn,
                    audit_record_id=accepted_id,
                    **_asset_raw(
                        action="asset.revision.register",
                        resource_revision_before=4,
                        resource_revision_after=4,
                    ),
                )
                conn.execute(text("SAVEPOINT ped_i10b6_attempt"))
                _expect_raw_failure(
                    conn,
                    **_asset_raw(
                        action="asset.revision.register",
                        resource_revision_before=4,
                        resource_revision_after=5,
                    ),
                )
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": accepted_id},
                ).scalar_one()
                == 1
            )

    def test_asset_increment_n_to_n_plus_one_accepted_n_to_n_rejected(
        self, bootstrap_engine
    ) -> None:
        accepted_id = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(
                    conn,
                    audit_record_id=accepted_id,
                    **_asset_raw(
                        action="asset.lifecycle.withdraw",
                        resource_revision_before=2,
                        resource_revision_after=3,
                    ),
                )
                conn.execute(text("SAVEPOINT ped_i10b6_attempt"))
                _expect_raw_failure(
                    conn,
                    **_asset_raw(
                        action="asset.lifecycle.withdraw",
                        resource_revision_before=2,
                        resource_revision_after=2,
                    ),
                )
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": accepted_id},
                ).scalar_one()
                == 1
            )


class TestImmutabilityAndRls:
    def test_runtime_update_and_delete_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        record_id = uuid7()
        tenant = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(
                    conn,
                    audit_record_id=record_id,
                    tenant_id=tenant,
                    **_asset_raw(),
                )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant)
                with pytest.raises(ProgrammingError):
                    conn.execute(
                        text(
                            "UPDATE security.audit_records SET action = 'asset.create'"
                        )
                    )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant)
                with pytest.raises(ProgrammingError):
                    conn.execute(text("DELETE FROM security.audit_records"))
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": record_id},
                ).scalar_one()
                == 1
            )

    def test_cross_tenant_insert_denied(self, runtime_engine, bootstrap_engine) -> None:
        record_id = uuid7()
        tenant = uuid7()
        other = uuid7()
        with runtime_engine.connect() as conn:
            trans = conn.begin()
            set_tenant(conn, other)
            with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
                _insert_raw(
                    conn,
                    audit_record_id=record_id,
                    tenant_id=tenant,
                    **_asset_raw(),
                )
            trans.rollback()
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": record_id},
                ).scalar_one()
                == 0
            )


class TestKernelAuthorityMatrix:
    def test_exact_grant_allows_create(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant, principal = uuid7(), uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(ASSET_CREATE,),
        )
        auth = KernelAssetMutationAuthorization(_kernel(runtime_engine))
        service = _service(runtime_engine, auth=auth)
        asset_id = AssetId.generate()
        asset = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        assert asset.asset_id == asset_id
        assert _count_assets(bootstrap_engine, asset_id.value) == 1
        assert _count_asset_audits(bootstrap_engine, asset_id.value) == 1

    def test_missing_and_wrong_capability_deny_zero_uow(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant, principal = uuid7(), uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        auth = KernelAssetMutationAuthorization(_kernel(runtime_engine))
        service = _service(runtime_engine, auth=auth)
        missing_id = AssetId.generate()
        with pytest.raises(AssetForbidden):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=missing_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capability="asset.lifecycle.manage",
        )
        wrong_id = AssetId.generate()
        with pytest.raises(AssetForbidden):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=wrong_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert _count_assets(bootstrap_engine, missing_id.value) == 0
        assert _count_assets(bootstrap_engine, wrong_id.value) == 0
        assert _count_asset_audits(bootstrap_engine, missing_id.value) == 0
        assert _count_asset_audits(bootstrap_engine, wrong_id.value) == 0

    @pytest.mark.parametrize(
        "setup",
        [
            "suspended_principal",
            "suspended_tenant",
            "inactive_membership",
            "revoked_membership",
            "expired_grant",
            "revoked_grant",
        ],
    )
    def test_current_authority_deny_matrix(
        self, bootstrap_engine, runtime_engine, setup: str
    ) -> None:
        tenant, principal = uuid7(), uuid7()
        now = datetime.now(UTC)
        if setup == "suspended_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.SUSPENDED
            )
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=ASSET_CREATE,
            )
        elif setup == "suspended_tenant":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(
                bootstrap_engine, tenant, status=TenantStatus.SUSPENDED
            )
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=ASSET_CREATE,
            )
        elif setup == "inactive_membership":
            seed_active_authority(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capabilities=(ASSET_CREATE,),
            )
            seed_membership(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                status=MembershipStatus.SUSPENDED,
            )
        elif setup == "revoked_membership":
            seed_active_authority(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capabilities=(ASSET_CREATE,),
            )
            revoke_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "expired_grant":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=ASSET_CREATE,
                expires_at=now - timedelta(hours=1),
            )
        else:
            seed_active_authority(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capabilities=(ASSET_CREATE,),
            )
            revoke_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=ASSET_CREATE,
            )
        blobs = InMemoryBlobStore()
        probe = InspectProbe(blobs)
        auth = KernelAssetMutationAuthorization(_kernel(runtime_engine))
        service = _service(runtime_engine, blobs=probe, auth=auth)
        asset_id = AssetId.generate()
        with pytest.raises(AssetForbidden):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert _count_assets(bootstrap_engine, asset_id.value) == 0
        assert _count_asset_audits(bootstrap_engine, asset_id.value) == 0
        assert probe.calls == []

    def test_repository_unavailable_is_not_allow(
        self, runtime_engine
    ) -> None:
        from sqlalchemy import create_engine

        engine = create_engine(
            "postgresql+psycopg://nobody:bad@127.0.0.1:1/none",
            pool_pre_ping=False,
            connect_args={"connect_timeout": 1},
        )
        auth = KernelAssetMutationAuthorization(_kernel(engine))
        factory = SqlAlchemyAssetUnitOfWorkFactory(runtime_engine)
        service = AssetMutationService(
            factory, InMemoryBlobStore(), auth, clock=_clock
        )
        tenant, principal, asset_id = uuid7(), uuid7(), AssetId.generate()
        with pytest.raises(AuthorizationUnavailableError):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )


class TestTransactionalAudit:
    def test_successful_create_register_activate_persist_one_row_each(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        blobs = InMemoryBlobStore()
        service = _service(runtime_engine, blobs=blobs)
        tenant, principal = uuid7(), uuid7()
        asset = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=AssetId.generate(),
            resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        registered = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=_prepared(blobs),
            media_type="image/png",
            **asset_audit_kwargs(principal),
        )
        service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        rows = _fetch_asset_audits(bootstrap_engine, asset.asset_id.value)
        by_action = {row["action"]: row for row in rows}
        assert set(by_action) == {
            "asset.create",
            "asset.revision.register",
            "asset.safety.pass",
            "asset.revision.activate",
        }
        create = by_action["asset.create"]
        assert create["tenant_id"] == tenant
        assert create["primary_resource_type"] == "asset.image"
        assert create["primary_resource_id"] == asset.asset_id.value
        assert create["primary_resource_revision"] is None
        assert create["resource_revision_before"] is None
        assert create["resource_revision_after"] == 0
        assert create["related_resource_refs"] == []
        register = by_action["asset.revision.register"]
        assert register["resource_revision_before"] == 0
        assert register["resource_revision_after"] == 0
        assert register["primary_resource_revision"] is None
        assert register["related_resource_refs"][0]["resource_revision"] == int(
            registered.revision.revision_number
        )
        activate = by_action["asset.revision.activate"]
        assert activate["resource_revision_before"] == 1
        assert activate["resource_revision_after"] == 2
        assert activate["primary_resource_revision"] is None


class TestAuditInsertFailureRollsBackPostgres:
    def test_create_register_activate_lifecycle_safety(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        blobs = InMemoryBlobStore()
        failing = AssetMutationService(
            _FailingAuditFactory(runtime_engine),
            blobs,
            AllowAssetMutationAuthorization(),
            clock=_clock,
        )
        tenant, principal = uuid7(), uuid7()
        create_id = AssetId.generate()
        with pytest.raises(AssetPersistenceFailed):
            failing.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=create_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert _count_assets(bootstrap_engine, create_id.value) == 0
        assert _count_asset_audits(bootstrap_engine, create_id.value) == 0

        ok = _service(runtime_engine, blobs=blobs)
        asset = ok.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=AssetId.generate(),
            resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        before_create_audits = _count_asset_audits(
            bootstrap_engine, asset.asset_id.value
        )
        revision_id = AssetRevisionId.generate()
        with pytest.raises(AssetPersistenceFailed):
            failing.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=revision_id,
                prepared=_prepared(blobs),
                media_type="image/png",
                **asset_audit_kwargs(principal),
            )
        assert _count_revisions(bootstrap_engine, asset.asset_id.value) == 0
        assert (
            _count_asset_audits(bootstrap_engine, asset.asset_id.value)
            == before_create_audits
        )

        registered = ok.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=_prepared(blobs),
            media_type="image/png",
            **asset_audit_kwargs(principal),
        )
        before_safety = _count_asset_audits(bootstrap_engine, asset.asset_id.value)
        with pytest.raises(AssetPersistenceFailed):
            failing.mark_safety_passed(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=registered.revision.asset_revision_id,
                expected_aggregate_revision=ZERO,
                **asset_audit_kwargs(principal),
            )
        assert (
            _count_asset_audits(bootstrap_engine, asset.asset_id.value)
            == before_safety
        )

        ok.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        before_activate = _count_asset_audits(bootstrap_engine, asset.asset_id.value)
        with pytest.raises(AssetPersistenceFailed):
            failing.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(1),
                **asset_audit_kwargs(principal),
            )
        assert (
            _count_asset_audits(bootstrap_engine, asset.asset_id.value)
            == before_activate
        )
        with bootstrap_engine.connect() as conn:
            current = conn.execute(
                text(
                    "SELECT current_revision, aggregate_revision FROM asset.assets "
                    "WHERE asset_id = :id"
                ),
                {"id": asset.asset_id.value},
            ).one()
        assert current == (None, 1)

        ok.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **asset_audit_kwargs(principal),
        )
        before_withdraw = _count_asset_audits(bootstrap_engine, asset.asset_id.value)
        with pytest.raises(AssetPersistenceFailed):
            failing.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(2),
                **asset_audit_kwargs(principal),
            )
        assert (
            _count_asset_audits(bootstrap_engine, asset.asset_id.value)
            == before_withdraw
        )
        with bootstrap_engine.connect() as conn:
            lifecycle = conn.execute(
                text("SELECT lifecycle FROM asset.assets WHERE asset_id = :id"),
                {"id": asset.asset_id.value},
            ).scalar_one()
        assert lifecycle == "active"


class TestDowngradeGuard:
    def test_empty_downgrade_then_fail_closed_when_evidence_exists(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "pedi10b2001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "pedi10b2001"
            )
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )
        evidence_id = uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_raw(conn, audit_record_id=evidence_id, **_asset_raw())
        with pytest.raises(Exception) as exc:
            command.downgrade(cfg, "pedi10b2001")
        message = str(exc.value)
        cause = exc.value.__cause__
        if cause is not None:
            message = f"{message} {cause}"
        assert "Asset security audit evidence" in message
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE audit_record_id = :id"
                    ),
                    {"id": evidence_id},
                ).scalar_one()
                == 1
            )
        # Isolation for later session-scoped Alembic cycle tests only.
        # This is not the production downgrade path.
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
