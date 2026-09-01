"""TOS-DEV04-I08 PostgreSQL / RLS / concurrency / recovery adversarial proofs.

Verification-only against the governed disposable PostgreSQL substrate.
Provider calls use FakeStructuredModelGateway — no live model providers.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from aieos.domains.content.application.preparation_recovery import (
    PreparationBindingRecoveryStatus,
    inspect_preparation_generation_bindings,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    LESSON_PLAN_SCHEMA_ID,
    LESSON_PLAN_SCHEMA_VERSION,
    PREPARATION_ARTIFACT_KINDS,
)
from aieos.domains.teaching.application.errors import (
    PreparationRecoveryInvariantError,
    WorkGenerationAlreadyExists,
    WorkGenerationInProgress,
)
from aieos.domains.teaching.domain.identities import WorkId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.domain.generation_run import (
    GenerationRun,
    GenerationRunId,
    GenerationRunStatus,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.capabilities.models import (
    CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
)
from aieos.platform.idempotency.hashing import (
    fingerprint_material,
    hash_idempotency_key,
)
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_lesson_plan_payload,
)
from tests.domains.teaching.helpers_dev03 import build_client, create_work, headers
from tests.domains.teaching.helpers_dev04_i06 import (
    FIXED_NOW,
    attach_pausing_materializer,
    build_prepare_service,
    create_teaching_work,
    event_context,
    pass_preparation_kit,
)
from tests.domains.teaching.test_tos_dev04_i02_multi_artifact_persistence import (
    _clear_i02_downgrade_blockers,
)
from tests.domains.teaching.test_tos_dev04_i06_prepare import (
    _force_running_without_finalize,
)
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.tos_dev04_i08

CANONICAL_KINDS = list(PREPARATION_ARTIFACT_KINDS)
EXPECTED_OPENAPI_SHA = (
    "CCD233062672B36A4DB6C6B60E7413AF8EEC6FDAAE9550270C6879E4C4A06D7C"
)


@pytest.fixture(autouse=True)
def _cleanup_i08_rows(postgres18: dict[str, str]) -> None:
    from sqlalchemy import create_engine

    yield
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        _clear_i02_downgrade_blockers(engine)
    finally:
        engine.dispose()


def _etag(response) -> str:
    return response.headers["ETag"]


def _prepare_gateway() -> FakeStructuredModelGateway:
    return FakeStructuredModelGateway(
        result_factory=lambda _req: pass_preparation_kit()
    )


def _count(engine: Engine, sql: str, params: dict) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _approve(client, tenant_id: uuid.UUID, content_id: str, version_id: str):
    detail = client.get(
        f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
        headers=headers(tenant_id),
    )
    assert detail.status_code == 200, detail.text
    approved = client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
        json={},
        headers={
            **headers(tenant_id, idempotency_key=f"i08-approve-{content_id}"),
            "If-Match": _etag(detail),
        },
    )
    assert approved.status_code == 200, approved.text
    return approved


def _http_prepare(client, tenant_id: uuid.UUID, *, create_key: str, prep_key: str):
    created = create_work(client, tenant_id, idempotency_key=create_key)
    assert created.status_code == 201, created.text
    work_id = created.json()["work_id"]
    prepared = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/prepare",
        headers=headers(
            tenant_id, idempotency_key=prep_key, if_match=_etag(created)
        ),
    )
    assert prepared.status_code == 200, prepared.text
    return work_id, created, prepared


class TestContractFences:
    def test_openapi_and_alembic_unchanged(self) -> None:
        from tools.release.common import EXPECTED_OPENAPI_SHA256

        assert EXPECTED_OPENAPI_SHA256 == EXPECTED_OPENAPI_SHA
        heads = (REPO_ROOT / "migrations" / "versions").glob("tosd040002*")
        assert not list(heads)
        spec_path = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
        text_blob = spec_path.read_text(encoding="utf-8")
        assert '"operationId": "teaching_work_prepare"' in text_blob
        assert '"operationId": "teaching_work_generate"' in text_blob
        assert "/api/v1/teaching/works/{work_id}/actions/prepare" in text_blob


class TestTenantRlsIsolation:
    def test_tenant_b_cannot_observe_tenant_a_preparation(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        client_a = build_client(
            runtime_engine, tenant_a, principal, model_gateway=_prepare_gateway()
        )
        work_id, _created, prepared = _http_prepare(
            client_a, tenant_a, create_key="i08-ta-c", prep_key="i08-ta-p"
        )
        body = prepared.json()
        run_id = body["generation_run_id"]
        content_ids = {a["content_id"] for a in body["artifacts"]}
        version_ids = {a["version_id"] for a in body["artifacts"]}
        assert len(version_ids) == 6

        client_b = build_client(
            runtime_engine, tenant_b, principal, model_gateway=_prepare_gateway()
        )
        assert (
            client_b.get(
                f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_b)
            ).status_code
            == 404
        )
        assert (
            client_b.get(
                f"/api/v1/teaching/works/{work_id}/artifacts",
                headers=headers(tenant_b),
            ).status_code
            == 404
        )
        denied_prep = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_b, idempotency_key="i08-tb-steal", if_match='"r0"'
            ),
        )
        assert denied_prep.status_code in {403, 404}

        queue_b = client_b.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_b)
        )
        assert queue_b.status_code == 200
        assert content_ids.isdisjoint(
            {item["content_id"] for item in queue_b.json()["items"]}
        )

        # Governed UoW / RLS: tenant B context cannot load A's aggregates.
        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_b) as tuow:
            assert tuow.works.get(WorkId(uuid.UUID(work_id))) is None
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_b) as ai_uow:
            assert ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id))) is None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_b) as cuow:
            for cid in content_ids:
                assert cuow.contents.get(ContentId(uuid.UUID(cid))) is None
            for vid in version_ids:
                assert (
                    cuow.versions.get(ContentVersionId(uuid.UUID(vid))) is None
                )
        # Bootstrap inspection still sees Tenant A rows (setup authority only).
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_a},
            )
            == 6
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_b},
            )
            == 0
        )

    def test_missing_tenant_guc_fails_closed_on_dev04_tables(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        _http_prepare(client, tenant_id, create_key="i08-miss-c", prep_key="i08-miss-p")

        for table in (
            "teaching.works",
            "ai.generation_runs",
            "content.contents",
            "content.content_versions",
        ):
            with runtime_engine.connect() as conn:
                with conn.begin():
                    # No aieos.tenant_id GUC — RLS must fail closed.
                    with pytest.raises(Exception) as excinfo:
                        conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                    message = str(excinfo.value).lower()
                    assert "aieos.tenant_id" in message or "permission" in message

    def test_invalid_tenant_header_fails_closed(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, created, _ = _http_prepare(
            client, tenant_id, create_key="i08-inv-c", prep_key="i08-inv-p"
        )
        bad = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers={
                "X-AIEOS-Tenant-ID": "not-a-uuid",
                "Idempotency-Key": "i08-inv-prep",
                "If-Match": _etag(created),
            },
        )
        assert bad.status_code == 400
        assert bad.json()["code"] == "invalid_tenant_header"


class TestSameTenantTeacherIsolation:
    def test_teacher_b_denied_before_mutation(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        gateway_a = _prepare_gateway()
        gateway_b = _prepare_gateway()
        client_a = build_client(
            runtime_engine, tenant_id, teacher_a, model_gateway=gateway_a
        )
        work_id, created, prepared = _http_prepare(
            client_a, tenant_id, create_key="i08-own-c", prep_key="i08-own-p"
        )
        content_ids = {a["content_id"] for a in prepared.json()["artifacts"]}
        runs_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )
        contents_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )
        versions_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.content_versions WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )
        provider_before = gateway_b.call_count

        client_b = build_client(
            runtime_engine, tenant_id, teacher_b, model_gateway=gateway_b
        )
        assert (
            client_b.get(
                f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_id)
            ).status_code
            == 403
        )
        assert (
            client_b.get(
                f"/api/v1/teaching/works/{work_id}/artifacts",
                headers=headers(tenant_id),
            ).status_code
            == 403
        )
        denied = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id,
                idempotency_key="i08-own-p",  # crafted reuse of A's key
                if_match=_etag(created),
            ),
        )
        assert denied.status_code == 403
        assert gateway_b.call_count == provider_before

        queue_b = client_b.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue_b.status_code == 200
        assert content_ids.isdisjoint(
            {item["content_id"] for item in queue_b.json()["items"]}
        )

        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == runs_before
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == contents_before
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.content_versions WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == versions_before
        )


class TestSameKeyConcurrency:
    _STALE_NOW = FIXED_NOW + timedelta(hours=1)

    def test_concurrent_same_key_one_execution(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service_a, gateway_a = build_prepare_service(runtime_engine)
        service_b, gateway_b = build_prepare_service(runtime_engine)
        assert isinstance(gateway_a, FakeStructuredModelGateway)
        assert isinstance(gateway_b, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="i08-sk-c"
        )
        entered = threading.Event()
        proceed = threading.Event()
        attach_pausing_materializer(service_a, entered=entered, proceed=proceed)
        key = "i08-same-key"
        a_out: dict[str, object] = {}
        b_out: dict[str, object] = {}

        def run_a() -> None:
            try:
                a_out["result"] = service_a.prepare(
                    tenant_id,
                    principal_id,
                    work_id=work_id,
                    expected_aggregate_revision=revision,
                    idempotency_key=key,
                    event_context=event_context(principal_id),
                    now=FIXED_NOW,
                )
            except Exception as exc:  # noqa: BLE001
                a_out["error"] = exc

        def run_b() -> None:
            try:
                b_out["result"] = service_b.prepare(
                    tenant_id,
                    principal_id,
                    work_id=work_id,
                    expected_aggregate_revision=revision,
                    idempotency_key=key,
                    event_context=event_context(principal_id),
                    now=self._STALE_NOW,
                )
            except Exception as exc:  # noqa: BLE001
                b_out["error"] = exc

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        assert entered.wait(timeout=30)
        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        thread_b.join(timeout=1.0)
        # B may still be blocked on GenerationRun FOR UPDATE; if the host is
        # unusually fast after A releases later, final invariants still bind.
        if thread_b.is_alive():
            proceed.set()
            thread_a.join(timeout=60)
            thread_b.join(timeout=60)
        else:
            proceed.set()
            thread_a.join(timeout=60)
            thread_b.join(timeout=60)

        assert "error" not in a_out and "error" not in b_out, (a_out, b_out)
        a_result = a_out["result"]
        b_result = b_out["result"]
        assert a_result is not None and b_result is not None
        assert b_result.generation_run_id == a_result.generation_run_id  # type: ignore[attr-defined]
        assert [a.content_id for a in a_result.artifacts] == [  # type: ignore[attr-defined]
            b.content_id for b in b_result.artifacts  # type: ignore[attr-defined]
        ]
        assert [a.version_id for a in a_result.artifacts] == [  # type: ignore[attr-defined]
            b.version_id for b in b_result.artifacts  # type: ignore[attr-defined]
        ]
        assert gateway_a.call_count + gateway_b.call_count == 1
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 1
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 6
        )
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM content.content_versions
                 WHERE tenant_id = :tid AND origin = 'AI'
                """,
                {"tid": tenant_id},
            )
            == 6
        )

        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        queue = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue.status_code == 200
        assert len(queue.json()["items"]) == 6


class TestDifferentKeyConcurrency:
    def test_concurrent_different_keys_single_winner(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service_a, gateway_a = build_prepare_service(runtime_engine)
        service_b, gateway_b = build_prepare_service(runtime_engine)
        assert isinstance(gateway_a, FakeStructuredModelGateway)
        assert isinstance(gateway_b, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="i08-dk-c"
        )
        entered = threading.Event()
        proceed = threading.Event()
        attach_pausing_materializer(service_a, entered=entered, proceed=proceed)
        a_out: dict[str, object] = {}
        b_out: dict[str, object] = {}

        def run_a() -> None:
            try:
                a_out["result"] = service_a.prepare(
                    tenant_id,
                    principal_id,
                    work_id=work_id,
                    expected_aggregate_revision=revision,
                    idempotency_key="i08-dk-a",
                    event_context=event_context(principal_id),
                    now=FIXED_NOW,
                )
            except Exception as exc:  # noqa: BLE001
                a_out["error"] = exc

        def run_b() -> None:
            try:
                b_out["result"] = service_b.prepare(
                    tenant_id,
                    principal_id,
                    work_id=work_id,
                    expected_aggregate_revision=revision,
                    idempotency_key="i08-dk-b",
                    event_context=event_context(principal_id),
                    now=FIXED_NOW + timedelta(hours=1),
                )
            except Exception as exc:  # noqa: BLE001
                b_out["error"] = exc

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        assert entered.wait(timeout=30)
        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        thread_b.join(timeout=1.0)
        assert thread_b.is_alive(), "B should block on GenerationRun FOR UPDATE"
        proceed.set()
        thread_a.join(timeout=60)
        thread_b.join(timeout=60)

        assert "error" not in a_out
        assert isinstance(b_out.get("error"), WorkGenerationAlreadyExists)
        assert gateway_a.call_count == 1
        assert gateway_b.call_count == 0

        total_runs = _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM ai.generation_runs
             WHERE tenant_id = :tid
               AND capability_id = :cap
            """,
            {
                "tid": tenant_id,
                "cap": CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
            },
        )
        if total_runs != 1:
            raise AssertionError(
                "TOS-DEV04-I08R1 DEFECT FOUND — DIFFERENT-KEY CONCURRENCY "
                f"CREATED EXTRA GENERATIONRUN (total={total_runs})"
            )
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM ai.generation_runs
                 WHERE tenant_id = :tid
                   AND capability_id = :cap
                   AND status = 'SUCCEEDED'
                """,
                {
                    "tid": tenant_id,
                    "cap": CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                },
            )
            == 1
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 6
        )
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM content.content_versions
                 WHERE tenant_id = :tid AND origin = 'AI'
                """,
                {"tid": tenant_id},
            )
            == 6
        )

        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        queue = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue.status_code == 200
        assert len(queue.json()["items"]) == 6


class TestIdempotencyFingerprintConflict:
    def test_fingerprint_conflict_no_mutation(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client_a = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=_prepare_gateway(),
            provider_id="fake-a",
            model_id="model-a",
        )
        work_id, created, first = _http_prepare(
            client_a, tenant_id, create_key="i08-fp-c", prep_key="i08-fp-key"
        )
        runs_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )
        contents_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )

        client_b = build_client(
            runtime_engine,
            tenant_id,
            principal_id,
            model_gateway=_prepare_gateway(),
            provider_id="fake-b",
            model_id="model-b",
        )
        conflict = client_b.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="i08-fp-key", if_match=_etag(created)
            ),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "generation_idempotency_conflict"
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM ai.generation_runs WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == runs_before
        )
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == contents_before
        )
        assert first.json()["generation_run_id"]


class TestRowLockOwnership:
    def test_generation_run_locked_during_materialization(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="i08-lock-c"
        )
        entered = threading.Event()
        proceed = threading.Event()
        attach_pausing_materializer(service, entered=entered, proceed=proceed)
        key = "i08-lock-key"
        outcome: dict[str, object] = {}

        def run_owner() -> None:
            try:
                outcome["result"] = service.prepare(
                    tenant_id,
                    principal_id,
                    work_id=work_id,
                    expected_aggregate_revision=revision,
                    idempotency_key=key,
                    event_context=event_context(principal_id),
                    now=FIXED_NOW,
                )
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = exc

        thread = threading.Thread(target=run_owner)
        thread.start()
        assert entered.wait(timeout=30)

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get_by_idempotency_key(
                principal_id=principal_id,
                idempotency_key_sha256=hash_idempotency_key(key),
            )
            assert run is not None
            run_id = run.generation_run_id.value

        probe_error: Exception | None = None
        try:
            with runtime_engine.connect() as conn:
                trans = conn.begin()
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                conn.execute(text("SET LOCAL lock_timeout = '250ms'"))
                conn.execute(
                    text(
                        "SELECT generation_run_id FROM ai.generation_runs "
                        "WHERE generation_run_id = :id FOR UPDATE"
                    ),
                    {"id": run_id},
                )
                trans.rollback()
        except OperationalError as exc:
            probe_error = exc

        assert probe_error is not None
        assert "lock" in str(probe_error).lower() or "timeout" in str(probe_error).lower()

        proceed.set()
        thread.join(timeout=60)
        assert "error" not in outcome
        assert outcome["result"] is not None


class TestRecoveryClean:
    def test_zero_binding_fresh_lease_blocks_without_false_six(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="i08-zero-c"
        )
        fingerprint = fingerprint_material(
            {
                "work_id": str(work_id),
                "work_revision": int(revision),
                "capability_id": CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                "provider_id": "fake",
                "model_id": "fake-model",
            }
        )
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = GenerationRun(
                generation_run_id=GenerationRunId.generate(),
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_resource_type="teaching.work",
                work_resource_id=work_id.value,
                work_resource_revision=int(revision),
                capability_id=CAPABILITY_EDUCATION_GENERATE_PREPARATION_KIT,
                provider_id="fake",
                model_id="fake-model",
                status=GenerationRunStatus.RUNNING,
                request_fingerprint_sha256=fingerprint,
                idempotency_key_sha256=hash_idempotency_key("i08-zero-seed"),
                provider_response_id=None,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                educational_quality_summary=None,
                result_content_id=None,
                result_version_id=None,
                result_content_revision=None,
                failure_code=None,
                lease_expires_at=FIXED_NOW + timedelta(seconds=600),
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
                completed_at=None,
                aggregate_revision=0,
            )
            ai_uow.generation_runs.insert(run)
            ai_uow.commit()
            run_id = run.generation_run_id

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            locked = ai_uow.generation_runs.get(run_id)
            assert locked is not None
            run_snapshot = locked
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run_snapshot)
        assert inspection.status is PreparationBindingRecoveryStatus.ZERO

        with pytest.raises(WorkGenerationInProgress):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=work_id,
                expected_aggregate_revision=revision,
                idempotency_key="i08-zero-other",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 0
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 0
        )

        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        listed = client.get(
            f"/api/v1/teaching/works/{work_id.value}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []

    def test_exact_six_commit_ambiguity_recovers(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        service, gateway = build_prepare_service(runtime_engine)
        assert isinstance(gateway, FakeStructuredModelGateway)
        work_id, revision = create_teaching_work(
            runtime_engine, tenant_id, principal_id, idempotency_key="i08-ex-c"
        )
        first = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="i08-ex-p",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        _force_running_without_finalize(
            runtime_engine,
            tenant_id=tenant_id,
            run_id=first.generation_run_id.value,
            lease_fresh=True,
        )
        second = service.prepare(
            tenant_id,
            principal_id,
            work_id=work_id,
            expected_aggregate_revision=revision,
            idempotency_key="i08-ex-p",
            event_context=event_context(principal_id),
            now=FIXED_NOW,
        )
        assert gateway.call_count == 1
        assert second.generation_run_id == first.generation_run_id
        assert [a.content_id for a in second.artifacts] == [
            a.content_id for a in first.artifacts
        ]
        assert [a.version_id for a in second.artifacts] == [
            a.version_id for a in first.artifacts
        ]
        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(first.generation_run_id)
            assert run is not None
            assert run.status is GenerationRunStatus.SUCCEEDED
            assert run.result_content_id is None
            assert run.result_version_id is None
            assert run.result_content_revision is None
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == 6
        )

    def test_partial_corrupt_fails_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, _created, prepared = _http_prepare(
            client, tenant_id, create_key="i08-cor-c", prep_key="i08-cor-p"
        )
        run_id = prepared.json()["generation_run_id"]
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    DELETE FROM content.content_versions
                     WHERE tenant_id = :tid
                       AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                       AND (provenance->>'artifact_kind') = 'teacher_notes'
                    """
                ),
                {"tid": tenant_id, "rid": run_id},
            )

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run)
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 422
        assert listed.json()["code"] == "preparation_recovery_invariant_violation"
        assert "ready" not in listed.text.lower() or listed.status_code == 422

        # Corrupt SUCCEEDED partial must not silently rematerialize.
        gateway = _prepare_gateway()
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as tuow:
            work = tuow.works.get(WorkId(uuid.UUID(work_id)))
            assert work is not None
            current_revision = work.aggregate_revision
        with pytest.raises(PreparationRecoveryInvariantError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=WorkId(uuid.UUID(work_id)),
                expected_aggregate_revision=current_revision,
                idempotency_key="i08-cor-p",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 0
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM content.content_versions
                 WHERE tenant_id = :tid AND origin = 'AI'
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                """,
                {"tid": tenant_id, "rid": run_id},
            )
            == 5
        )


class TestRemainingCorruptionFamilies:
    """I08R1: remaining distinct provenance invariant families (not I06 duplicates)."""

    @pytest.mark.parametrize(
        ("label", "sql_fragment", "sql_params"),
        [
            (
                "unknown_artifact_kind",
                """
                UPDATE content.content_versions
                   SET provenance = jsonb_set(
                     provenance,
                     '{artifact_kind}',
                     '"not_a_canonical_kind"'::jsonb,
                     true
                   )
                 WHERE tenant_id = :tid
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                   AND (provenance->>'artifact_kind') = 'quiz'
                """,
                {},
            ),
            (
                "wrong_capability_id",
                """
                UPDATE content.content_versions
                   SET provenance = jsonb_set(
                     provenance,
                     '{capability_id}',
                     '"education.generate_worksheet"'::jsonb,
                     true
                   )
                 WHERE tenant_id = :tid
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                   AND (provenance->>'artifact_kind') = 'quiz'
                """,
                {},
            ),
            (
                "wrong_work_source_revision",
                """
                UPDATE content.content_versions
                   SET provenance = jsonb_set(
                     provenance,
                     '{source_refs}',
                     (
                       SELECT jsonb_agg(
                         CASE
                           WHEN elem->>'resource_type' = 'teaching.work'
                           THEN jsonb_set(elem, '{resource_revision}', '999'::jsonb, true)
                           ELSE elem
                         END
                       )
                       FROM jsonb_array_elements(provenance->'source_refs') AS elem
                     ),
                     true
                   )
                 WHERE tenant_id = :tid
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                   AND (provenance->>'artifact_kind') = 'quiz'
                """,
                {},
            ),
        ],
        ids=["unknown_artifact_kind", "wrong_capability", "wrong_work_source_revision"],
    )
    def test_corrupt_provenance_families_fail_closed(
        self,
        runtime_engine: Engine,
        bootstrap_engine: Engine,
        label: str,
        sql_fragment: str,
        sql_params: dict,
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, _created, prepared = _http_prepare(
            client,
            tenant_id,
            create_key=f"i08r1-{label}-c",
            prep_key=f"i08r1-{label}-p",
        )
        run_id = prepared.json()["generation_run_id"]
        contents_before = _count(
            bootstrap_engine,
            "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
            {"tid": tenant_id},
        )
        versions_before = _count(
            bootstrap_engine,
            """
            SELECT count(*) FROM content.content_versions
             WHERE tenant_id = :tid AND origin = 'AI'
               AND provenance #>> '{generation_run_ref,resource_id}' = :rid
            """,
            {"tid": tenant_id, "rid": run_id},
        )
        assert contents_before == 6
        assert versions_before == 6

        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(sql_fragment),
                {"tid": tenant_id, "rid": run_id, **sql_params},
            )

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
            run_snapshot = run
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run_snapshot)
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 422, listed.text
        assert listed.json()["code"] == "preparation_recovery_invariant_violation"

        gateway = _prepare_gateway()
        service, _ = build_prepare_service(runtime_engine, model_gateway=gateway)
        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as tuow:
            work = tuow.works.get(WorkId(uuid.UUID(work_id)))
            assert work is not None
            current_revision = work.aggregate_revision
        with pytest.raises(PreparationRecoveryInvariantError):
            service.prepare(
                tenant_id,
                principal_id,
                work_id=WorkId(uuid.UUID(work_id)),
                expected_aggregate_revision=current_revision,
                idempotency_key=f"i08r1-{label}-p",
                event_context=event_context(principal_id),
                now=FIXED_NOW,
            )
        assert gateway.call_count == 0
        assert (
            _count(
                bootstrap_engine,
                "SELECT count(*) FROM content.contents WHERE tenant_id = :tid",
                {"tid": tenant_id},
            )
            == contents_before
        )
        assert (
            _count(
                bootstrap_engine,
                """
                SELECT count(*) FROM content.content_versions
                 WHERE tenant_id = :tid AND origin = 'AI'
                   AND provenance #>> '{generation_run_ref,resource_id}' = :rid
                """,
                {"tid": tenant_id, "rid": run_id},
            )
            == versions_before
        )


class TestPostReviewDurability:
    def test_approved_and_later_version_projection(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=_prepare_gateway()
        )
        work_id, _created, prepared = _http_prepare(
            client, tenant_id, create_key="i08-pr-c", prep_key="i08-pr-p"
        )
        run_id = prepared.json()["generation_run_id"]
        quiz = next(a for a in prepared.json()["artifacts"] if a["artifact_kind"] == "quiz")
        lesson = next(
            a for a in prepared.json()["artifacts"] if a["artifact_kind"] == "lesson_plan"
        )
        quiz_version = quiz["version_id"]
        lesson_version = lesson["version_id"]

        _approve(client, tenant_id, quiz["content_id"], quiz_version)
        content = client.get(
            f"/api/v1/contents/{quiz['content_id']}", headers=headers(tenant_id)
        )
        assert content.json()["stewardship_state"] == "APPROVED"

        queue = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert quiz["content_id"] not in {
            item["content_id"] for item in queue.json()["items"]
        }
        assert len(queue.json()["items"]) == 5

        listed = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 6
        assert [i["artifact_kind"] for i in items] == CANONICAL_KINDS
        quiz_item = next(i for i in items if i["artifact_kind"] == "quiz")
        assert quiz_item["version_id"] == quiz_version
        assert quiz_item["stewardship_state"] == "APPROVED"
        assert quiz_item["generation_run_id"] == run_id

        approved = _approve(client, tenant_id, lesson["content_id"], lesson_version)
        appended = client.post(
            f"/api/v1/contents/{lesson['content_id']}/versions",
            json={
                "schema_id": LESSON_PLAN_SCHEMA_ID,
                "schema_version": LESSON_PLAN_SCHEMA_VERSION,
                "payload": valid_lesson_plan_payload(title="I08 later edit"),
            },
            headers={
                **headers(tenant_id, idempotency_key="i08-append"),
                "If-Match": _etag(approved),
            },
        )
        assert appended.status_code == 201, appended.text
        later_id = appended.json()["version_id"]
        assert later_id != lesson_version

        after = client.get(
            f"/api/v1/contents/{lesson['content_id']}", headers=headers(tenant_id)
        )
        listed2 = client.get(
            f"/api/v1/teaching/works/{work_id}/artifacts",
            headers=headers(tenant_id),
        )
        assert listed2.status_code == 200
        lesson_item = next(
            i for i in listed2.json()["items"] if i["artifact_kind"] == "lesson_plan"
        )
        assert lesson_item["version_id"] == lesson_version
        assert lesson_item["version_id"] != later_id
        assert lesson_item["title"] == after.json()["title"]
        assert lesson_item["stewardship_state"] == after.json()["stewardship_state"]
        assert lesson_item["aggregate_revision"] == after.json()["aggregate_revision"]

        with SqlAlchemyAIUnitOfWorkFactory(runtime_engine)(tenant_id) as ai_uow:
            run = ai_uow.generation_runs.get(GenerationRunId(uuid.UUID(run_id)))
            assert run is not None
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as cuow:
            inspection = inspect_preparation_generation_bindings(cuow, run)
        assert inspection.status is PreparationBindingRecoveryStatus.INVALID


class TestReviewQueueExactness:
    def test_queue_counts_and_replay(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gateway = _prepare_gateway()
        client = build_client(
            runtime_engine, tenant_id, principal_id, model_gateway=gateway
        )
        work_id, created, prepared = _http_prepare(
            client, tenant_id, create_key="i08-q-c", prep_key="i08-q-p"
        )
        queue1 = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue1.status_code == 200
        assert len(queue1.json()["items"]) == 6

        replay = client.post(
            f"/api/v1/teaching/works/{work_id}/actions/prepare",
            headers=headers(
                tenant_id, idempotency_key="i08-q-p", if_match=_etag(created)
            ),
        )
        assert replay.status_code == 200
        assert gateway.call_count == 1
        queue2 = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert len(queue2.json()["items"]) == 6

        # Concurrent HTTP same-key losers must not inflate the queue.
        def _call():
            return client.post(
                f"/api/v1/teaching/works/{work_id}/actions/prepare",
                headers=headers(
                    tenant_id, idempotency_key="i08-q-p", if_match=_etag(created)
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_call), pool.submit(_call)]
            responses = [f.result(timeout=60) for f in as_completed(futures)]
        assert all(r.status_code == 200 for r in responses)
        assert gateway.call_count == 1
        queue3 = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert len(queue3.json()["items"]) == 6
