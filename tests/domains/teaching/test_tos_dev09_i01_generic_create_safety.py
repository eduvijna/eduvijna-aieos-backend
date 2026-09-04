"""TOS-DEV09-I01 — generic TeachingWork create must reject remediate_class."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid7

import pytest

from aieos.domains.teaching.application.create import CreateTeachingWorkService
from aieos.domains.teaching.application.errors import InvalidTeachingWorkRequest
from aieos.domains.teaching.application.models import CreateTeachingWorkCommand
from aieos.domains.teaching.domain.intent_type import IntentType
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.idempotency.hashing import hash_idempotency_key
from aieos.platform.idempotency.models import TEACHING_WORK_CREATE_V1, IdempotencyScope
from tests.domains.teaching.helpers import build_client, create_work

pytestmark = pytest.mark.tos_dev09_i01

TARGET_DATE = date(2026, 9, 5)
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class TestGenericCreateSafety:
    def test_prepare_tomorrow_still_succeeds(self, runtime_engine) -> None:
        tenant_id = uuid7()
        client = build_client(runtime_engine, tenant_id, uuid7())
        response = create_work(
            client,
            tenant_id,
            goal_text="Prepare fractions",
            target_date=TARGET_DATE.isoformat(),
            idempotency_key=f"dev09-prepare-{uuid7()}",
            intent_type="prepare_tomorrow",
        )
        assert response.status_code == 201, response.text
        assert response.json()["intent_type"] == "prepare_tomorrow"

    def test_generic_http_create_rejects_remediate_class(self, runtime_engine) -> None:
        tenant_id = uuid7()
        principal_id = uuid7()
        client = build_client(runtime_engine, tenant_id, principal_id)
        key = f"dev09-remediate-{uuid7()}"
        response = create_work(
            client,
            tenant_id,
            goal_text="Remediate fractions",
            target_date=TARGET_DATE.isoformat(),
            idempotency_key=key,
            intent_type="remediate_class",
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_teaching_work_request"

        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            works = uow.works.list_for_teacher(
                teacher_principal_id=principal_id, limit=20, include_archived=True
            )
            assert works == []
            scope = IdempotencyScope(
                tenant_id=tenant_id,
                principal_id=principal_id,
                operation=TEACHING_WORK_CREATE_V1,
                key_sha256=hash_idempotency_key(key),
            )
            assert uow.idempotency.get(scope) is None

    def test_application_service_rejects_before_persist(self, runtime_engine) -> None:
        tenant_id = uuid7()
        principal_id = uuid7()
        service = CreateTeachingWorkService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            idempotency_retention=timedelta(days=1),
        )
        with pytest.raises(InvalidTeachingWorkRequest):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingWorkCommand(
                    intent_type=IntentType.REMEDIATE_CLASS.value,
                    goal_text="Remediate without origin",
                    target_date=TARGET_DATE,
                    locale="en-IN",
                ),
                idempotency_key=f"dev09-svc-{uuid7()}",
                now=NOW,
            )
        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            assert (
                uow.works.list_for_teacher(
                    teacher_principal_id=principal_id, limit=20, include_archived=True
                )
                == []
            )
