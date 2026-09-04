"""TOS-DEV08-I02R1 — Assessment current capability + replay authority."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.assessment.application.errors import AssessmentCapabilityForbidden
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_CLASSROOM_CORRECT,
    ASSESSMENT_CLASSROOM_LIST,
    ASSESSMENT_CLASSROOM_READ,
    ASSESSMENT_CLASSROOM_RECORD,
    ASSESSMENT_CLASSROOM_VOID,
    AIEOS_ASSESSMENT_CAPABILITIES,
)
from aieos.domains.education.schema import QUIZ_CONTENT_TYPE
from aieos.platform.security.authorization import (
    AIEOS_ASSESSMENT_CAPABILITIES as ADAPTER_ASSESSMENT_CAPABILITIES,
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    KernelClassroomAssessmentAuthorization,
)
from aieos.platform.security.authorization.decisions import (
    GrantStatus,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.domains.assessment.helpers_dev08_i02 import (
    RECORD_PATH,
    MutableSchoolContextClassReader,
    build_assessment_client,
    headers,
    seed_published_learner_content,
)
from tests.platform.security.authorization.helpers import (
    seed_active_authority,
    seed_grant,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.tos_dev08_i02


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


def _kernel(engine: Engine) -> AuthorizationKernel:
    return AuthorizationKernel(
        engine,
        known_capabilities=AIEOS_CONTENT_CAPABILITIES | ADAPTER_ASSESSMENT_CAPABILITIES,
    )


def _auth(engine: Engine) -> KernelClassroomAssessmentAuthorization:
    return KernelClassroomAssessmentAuthorization(_kernel(engine))


def _seed_content(bootstrap_engine, tenant_id, principal_id):
    return seed_published_learner_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        owner_id=principal_id,
        content_type=QUIZ_CONTENT_TYPE,
    )


class TestAssessmentCapabilityCatalog:
    def test_auth15_exact_five_capabilities(self) -> None:
        assert AIEOS_ASSESSMENT_CAPABILITIES == ADAPTER_ASSESSMENT_CAPABILITIES
        assert AIEOS_ASSESSMENT_CAPABILITIES == frozenset(
            {
                ASSESSMENT_CLASSROOM_RECORD,
                ASSESSMENT_CLASSROOM_CORRECT,
                ASSESSMENT_CLASSROOM_VOID,
                ASSESSMENT_CLASSROOM_READ,
                ASSESSMENT_CLASSROOM_LIST,
            }
        )
        assert "assessment.*" not in AIEOS_ASSESSMENT_CAPABILITIES
        assert "assessment.classroom.*" not in AIEOS_ASSESSMENT_CAPABILITIES

    def test_auth16_content_capabilities_unchanged(self) -> None:
        assert "content.publish" in AIEOS_CONTENT_CAPABILITIES
        assert ASSESSMENT_CLASSROOM_RECORD not in AIEOS_CONTENT_CAPABILITIES


class TestKernelAssessmentAuthorization:
    def test_auth01_record_grant_allows(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        _auth(runtime_engine).authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
        )

    def test_auth02_no_grant_denies(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth03_record_does_not_authorize_correct(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_CORRECT,
            )

    def test_auth04_correct_does_not_authorize_void(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_CORRECT,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_VOID,
            )

    def test_auth05_read_does_not_authorize_list(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_READ,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_LIST,
            )

    def test_auth06_list_does_not_authorize_read(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_LIST,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_READ,
            )

    def test_auth07_unknown_capability_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="assessment.classroom.unknown",
            )

    def test_auth08_wildcard_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="assessment.classroom.*",
            )

    def test_auth09_revoked_grant_effective_immediately(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        auth = _auth(runtime_engine)
        auth.authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
        )
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
            status=GrantStatus.REVOKED,
            revoked_at=datetime.now(UTC),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth10_expired_grant_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
            status=GrantStatus.ACTIVE,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    @pytest.mark.parametrize(
        "setup",
        ["suspended_principal", "suspended_tenant", "disabled_principal"],
    )
    def test_auth11_inactive_or_suspended_denies(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        principal_id,
        setup: str,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        if setup == "suspended_principal":
            seed_principal(
                bootstrap_engine,
                principal_id,
                status=PrincipalStatus.SUSPENDED,
            )
        elif setup == "disabled_principal":
            seed_principal(
                bootstrap_engine,
                principal_id,
                status=PrincipalStatus.DISABLED,
            )
        else:
            seed_tenant(
                bootstrap_engine,
                tenant_id,
                status=TenantStatus.SUSPENDED,
            )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth12_authorization_unavailable_propagates(
        self, tenant_id, principal_id
    ) -> None:
        class _UnavailableKernel:
            def decide_capability(self, **_kwargs):
                raise AuthorizationUnavailableError("authorization unavailable")

        auth = KernelClassroomAssessmentAuthorization(_UnavailableKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth12_unexpected_failure_sanitized(
        self, tenant_id, principal_id
    ) -> None:
        class _BrokenKernel:
            def decide_capability(self, **_kwargs):
                raise RuntimeError("db exploded")

        auth = KernelClassroomAssessmentAuthorization(_BrokenKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth13_jwt_roles_scopes_are_not_authority(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        """Membership alone (no grant) DENY — JWT roles/scopes never consulted."""
        from pathlib import Path

        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        adapter_src = Path(
            "src/aieos/platform/security/authorization/assessment_adapters.py"
        ).read_text(encoding="utf-8")
        lower = adapter_src.lower()
        assert "jwt" not in lower
        assert "roles" not in lower
        assert "scope" not in lower
        assert "is_admin" not in lower
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )

    def test_auth14_audit_records_do_not_authorize(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO security.audit_records (
                        audit_record_id, tenant_id, action,
                        primary_resource_type, primary_resource_id,
                        primary_resource_revision,
                        resource_revision_before, resource_revision_after,
                        related_resource_refs,
                        initiating_principal_id, effective_actor_id,
                        executing_principal_id,
                        delegation_id, execution_channel,
                        correlation_id, causation_id, trace_id, occurred_at
                    ) VALUES (
                        :aid, :tid, 'assessment.classroom.record',
                        'assessment.classroom', :rid, 0,
                        NULL, 0,
                        CAST('[]' AS jsonb),
                        :pid, :pid, :pid,
                        NULL, 'API',
                        :corr, :caus, NULL, clock_timestamp()
                    )
                    """
                ),
                {
                    "aid": uuid.uuid7(),
                    "tid": tenant_id,
                    "pid": principal_id,
                    "rid": uuid.uuid7(),
                    "corr": uuid.uuid7(),
                    "caus": uuid.uuid7(),
                },
            )
        with pytest.raises(AssessmentCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=ASSESSMENT_CLASSROOM_RECORD,
            )


class TestHttpCapabilityAndReplay:
    def test_auth02_http_record_without_grant_403(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_auth(runtime_engine),
        )
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="auth02"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 403, response.text

    def test_auth01_http_record_with_grant(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_auth(runtime_engine),
        )
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="auth01"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 201, response.text

    def test_ra01_ra02_record_replay_class_ref_gate(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id, teacher_principal_id=principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
            assessment_authorization=_auth(runtime_engine),
        )
        body = {
            "class_ref": "class-5a",
            "content_id": str(content_id),
            "content_version_id": str(version_id),
            "class_result_level": "DEMONSTRATED",
        }
        first = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra-rec"),
            json=body,
        )
        assert first.status_code == 201, first.text
        assessment_id = first.json()["assessment_id"]

        reader.class_refs = []
        denied = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra-rec"),
            json=body,
        )
        assert denied.status_code == 403, denied.text
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM assessment.classroom_assessments
                    WHERE tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert count == 1
            audits = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'assessment.classroom.record'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert audits == 1

        reader.class_refs = ["class-5a"]
        replayed = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra-rec"),
            json=body,
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.json()["assessment_id"] == assessment_id
        with bootstrap_engine.connect() as conn:
            audits = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'assessment.classroom.record'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert audits == 1

    def test_ra03_revoke_record_capability_blocks_replay(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(ASSESSMENT_CLASSROOM_RECORD,),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_auth(runtime_engine),
        )
        body = {
            "class_ref": "class-5a",
            "content_id": str(content_id),
            "content_version_id": str(version_id),
            "class_result_level": "DEMONSTRATED",
        }
        first = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra03"),
            json=body,
        )
        assert first.status_code == 201
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
            status=GrantStatus.REVOKED,
            revoked_at=datetime.now(UTC),
        )
        denied = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra03"),
            json=body,
        )
        assert denied.status_code == 403

    def test_ra04_correct_replay_requires_class_ref(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(
                ASSESSMENT_CLASSROOM_RECORD,
                ASSESSMENT_CLASSROOM_CORRECT,
            ),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id, teacher_principal_id=principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
            assessment_authorization=_auth(runtime_engine),
        )
        created = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra04-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "NOT_YET_DEMONSTRATED",
            },
        )
        assert created.status_code == 201
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]
        body = {"class_result_level": "MIXED", "class_result_note": None}
        first = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra04-corr", if_match=etag),
            json=body,
        )
        assert first.status_code == 200
        rev = first.json()["aggregate_revision"]
        reader.class_refs = []
        denied = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra04-corr", if_match=etag),
            json=body,
        )
        assert denied.status_code == 403
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT aggregate_revision FROM assessment.classroom_assessments
                    WHERE assessment_id = :aid
                    """
                ),
                {"aid": assessment_id},
            ).scalar_one()
            assert int(row) == rev

    def test_ra05_void_replay_requires_class_ref(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(
                ASSESSMENT_CLASSROOM_RECORD,
                ASSESSMENT_CLASSROOM_VOID,
            ),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id, teacher_principal_id=principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
            assessment_authorization=_auth(runtime_engine),
        )
        created = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra05-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]
        first = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(tenant_id, idempotency_key="ra05-void", if_match=etag),
        )
        assert first.status_code == 200
        rev = first.json()["aggregate_revision"]
        reader.class_refs = []
        denied = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(tenant_id, idempotency_key="ra05-void", if_match=etag),
        )
        assert denied.status_code == 403
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT aggregate_revision, lifecycle_state
                    FROM assessment.classroom_assessments
                    WHERE assessment_id = :aid
                    """
                ),
                {"aid": assessment_id},
            ).one()
            assert int(row.aggregate_revision) == rev
            assert row.lifecycle_state == "VOIDED"

    def test_ra06_revoke_correct_capability_blocks_replay(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(
                ASSESSMENT_CLASSROOM_RECORD,
                ASSESSMENT_CLASSROOM_CORRECT,
            ),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_auth(runtime_engine),
        )
        created = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra06-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "MIXED",
            },
        )
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]
        body = {"class_result_level": "DEMONSTRATED", "class_result_note": None}
        first = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra06-corr", if_match=etag),
            json=body,
        )
        assert first.status_code == 200
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_CORRECT,
            status=GrantStatus.REVOKED,
            revoked_at=datetime.now(UTC),
        )
        denied = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra06-corr", if_match=etag),
            json=body,
        )
        assert denied.status_code == 403


    def test_auth12_http_authorization_unavailable_503(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        class _UnavailableAuth:
            def authorize(self, **_kwargs):
                raise AuthorizationUnavailableError("authorization unavailable")

        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_UnavailableAuth(),
        )
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="auth12"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "authorization_unavailable"

    def test_ra07_corrupt_correct_idempotency_outcome_fails_closed(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(
                ASSESSMENT_CLASSROOM_RECORD,
                ASSESSMENT_CLASSROOM_CORRECT,
            ),
        )
        content_id, version_id = _seed_content(
            bootstrap_engine, tenant_id, principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            assessment_authorization=_auth(runtime_engine),
        )
        first = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra07-a"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "NOT_YET_DEMONSTRATED",
            },
        )
        assert first.status_code == 201, first.text
        assessment_a = first.json()["assessment_id"]
        etag_a = first.headers["etag"]
        second = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="ra07-b"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "MIXED",
            },
        )
        assert second.status_code == 201, second.text
        assessment_b = second.json()["assessment_id"]
        body = {"class_result_level": "DEMONSTRATED", "class_result_note": None}
        corrected = client.post(
            f"{RECORD_PATH}/{assessment_a}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra07-corr", if_match=etag_a),
            json=body,
        )
        assert corrected.status_code == 200, corrected.text
        with bootstrap_engine.begin() as conn:
            updated = conn.execute(
                text(
                    """
                    UPDATE api.idempotency_records
                    SET result_content_id = :other
                    WHERE tenant_id = :tid
                      AND actor_principal_id = :pid
                      AND operation = 'assessment_classroom_correct.v1'
                    """
                ),
                {
                    "other": assessment_b,
                    "tid": tenant_id,
                    "pid": principal_id,
                },
            )
            assert updated.rowcount == 1
        denied = client.post(
            f"{RECORD_PATH}/{assessment_a}/actions/correct",
            headers=headers(tenant_id, idempotency_key="ra07-corr", if_match=etag_a),
            json=body,
        )
        assert denied.status_code == 500, denied.text
        assert denied.json()["code"] == "persistence_invariant_violation"


class TestProductionCatalogComposition:
    def test_auth15_production_known_catalog_includes_assessment(self) -> None:
        from pathlib import Path

        from aieos.platform.runtime import compose_api_dependencies as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "AIEOS_ASSESSMENT_CAPABILITIES" in src
        assert "KernelClassroomAssessmentAuthorization" in src
        assert "AIEOS_CONTENT_CAPABILITIES | AIEOS_ASSESSMENT_CAPABILITIES" in src
