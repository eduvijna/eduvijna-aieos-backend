"""Exact Asset lifecycle / quarantine / safety vocabularies (ADR-AIEOS-033).

ACTIVE does not itself mean usable. Usability is evaluated later by
AssetUseAuthority over current revision safety, quarantine, and physical bytes.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.asset.domain.errors import InvalidAssetStateError


class AssetLifecycle(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    DELETED = "deleted"


class AssetQuarantineState(StrEnum):
    CLEAR = "clear"
    QUARANTINED = "quarantined"


class AssetRevisionSafetyState(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


FROZEN_ASSET_LIFECYCLES: frozenset[AssetLifecycle] = frozenset(AssetLifecycle)
FROZEN_ASSET_QUARANTINE_STATES: frozenset[AssetQuarantineState] = frozenset(
    AssetQuarantineState
)
FROZEN_ASSET_REVISION_SAFETY_STATES: frozenset[AssetRevisionSafetyState] = frozenset(
    AssetRevisionSafetyState
)


def parse_asset_lifecycle(value: str | AssetLifecycle) -> AssetLifecycle:
    if isinstance(value, AssetLifecycle):
        return value
    try:
        return AssetLifecycle(value)
    except ValueError as exc:
        raise InvalidAssetStateError(
            f"unknown asset lifecycle {value!r}; "
            f"allowed={sorted(s.value for s in AssetLifecycle)}"
        ) from exc


def parse_asset_quarantine_state(
    value: str | AssetQuarantineState,
) -> AssetQuarantineState:
    if isinstance(value, AssetQuarantineState):
        return value
    try:
        return AssetQuarantineState(value)
    except ValueError as exc:
        raise InvalidAssetStateError(
            f"unknown asset quarantine state {value!r}; "
            f"allowed={sorted(s.value for s in AssetQuarantineState)}"
        ) from exc


def parse_asset_revision_safety_state(
    value: str | AssetRevisionSafetyState,
) -> AssetRevisionSafetyState:
    if isinstance(value, AssetRevisionSafetyState):
        return value
    try:
        return AssetRevisionSafetyState(value)
    except ValueError as exc:
        raise InvalidAssetStateError(
            f"unknown asset revision safety state {value!r}; "
            f"allowed={sorted(s.value for s in AssetRevisionSafetyState)}"
        ) from exc
