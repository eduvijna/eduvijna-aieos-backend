"""Structured CloudEvents 1.0 JSON envelope builders."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.platform.events.constants import (
    CLOUDEVENTS_DATACONTENTTYPE,
    CLOUDEVENTS_SOURCE,
    CLOUDEVENTS_SPECVERSION,
    CLOUDEVENTS_TEACHING_SOURCE,
    content_subject,
    teaching_assignment_subject,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import MutationEventContext


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CloudEvent time must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


def build_content_cloudevent(
    *,
    event_id: EventId,
    event_type: str,
    content_id: UUID,
    time: datetime,
    context: MutationEventContext,
    tenant_id: UUID,
    aggregate_revision: int,
    data: Mapping[str, object],
) -> dict[str, object]:
    return {
        "specversion": CLOUDEVENTS_SPECVERSION,
        "id": str(event_id),
        "source": CLOUDEVENTS_SOURCE,
        "type": event_type,
        "subject": content_subject(str(content_id)),
        "time": _rfc3339(time),
        "datacontenttype": CLOUDEVENTS_DATACONTENTTYPE,
        "data": dict(data),
        "tenantid": str(tenant_id),
        "correlationid": str(context.correlation_id),
        "causationid": str(context.causation_id),
        "actorid": str(context.actor_principal_id),
        "effectiveactorid": str(context.effective_actor_id),
        "aggregaterevision": int(aggregate_revision),
    }


def build_teaching_cloudevent(
    *,
    event_id: EventId,
    event_type: str,
    assignment_id: UUID,
    time: datetime,
    context: MutationEventContext,
    tenant_id: UUID,
    aggregate_revision: int,
    data: Mapping[str, object],
) -> dict[str, object]:
    return {
        "specversion": CLOUDEVENTS_SPECVERSION,
        "id": str(event_id),
        "source": CLOUDEVENTS_TEACHING_SOURCE,
        "type": event_type,
        "subject": teaching_assignment_subject(str(assignment_id)),
        "time": _rfc3339(time),
        "datacontenttype": CLOUDEVENTS_DATACONTENTTYPE,
        "data": dict(data),
        "tenantid": str(tenant_id),
        "correlationid": str(context.correlation_id),
        "causationid": str(context.causation_id),
        "actorid": str(context.actor_principal_id),
        "effectiveactorid": str(context.effective_actor_id),
        "aggregaterevision": int(aggregate_revision),
    }


def canonical_cloudevent_bytes(envelope: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(envelope),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
