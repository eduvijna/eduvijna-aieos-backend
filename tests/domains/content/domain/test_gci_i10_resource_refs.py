"""GCI-I10 ResourceRef and VersionAssetRef domain contracts."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

from aieos.domains.content.domain.errors import InvalidVersionAssetRefError
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.resources import InvalidResourceRefError, ResourceRef


def _now() -> datetime:
    return datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _ref(**kw) -> ResourceRef:
    defaults = {
        "resource_type": "asset.image",
        "resource_id": uuid.uuid7(),
        "resource_revision": None,
    }
    defaults.update(kw)
    return ResourceRef(**defaults)


def _var(**kw) -> VersionAssetRef:
    defaults = {
        "tenant_id": uuid.uuid7(),
        "content_id": ContentId.generate(),
        "version_id": ContentVersionId.generate(),
        "resource_ref": _ref(),
        "role": "primary",
        "ordinal": 0,
        "required": True,
        "created_at": _now(),
    }
    defaults.update(kw)
    return VersionAssetRef(**defaults)


class ResourceRefDomainTests(unittest.TestCase):
    def test_a_valid_resource_ref(self) -> None:
        rid = uuid.uuid7()
        ref = ResourceRef("asset.image", rid, 3)
        self.assertEqual(ref.resource_type, "asset.image")
        self.assertEqual(ref.resource_id, rid)
        self.assertEqual(ref.resource_revision, 3)

    def test_b_null_revision_allowed(self) -> None:
        ref = ResourceRef("asset.image", uuid.uuid7(), None)
        self.assertIsNone(ref.resource_revision)

    def test_c_resource_type_must_match_pattern(self) -> None:
        with self.assertRaises(InvalidResourceRefError):
            ResourceRef("Asset", uuid.uuid7(), None)
        with self.assertRaises(InvalidResourceRefError):
            ResourceRef("", uuid.uuid7(), None)

    def test_d_resource_id_must_be_uuid(self) -> None:
        with self.assertRaises(InvalidResourceRefError):
            ResourceRef("asset.image", "not-a-uuid", None)  # type: ignore[arg-type]

    def test_e_revision_must_be_non_negative_int(self) -> None:
        with self.assertRaises(InvalidResourceRefError):
            ResourceRef("asset.image", uuid.uuid7(), -1)
        with self.assertRaises(InvalidResourceRefError):
            ResourceRef("asset.image", uuid.uuid7(), True)  # type: ignore[arg-type]

    def test_f_resource_ref_is_frozen(self) -> None:
        ref = _ref()
        with self.assertRaises(FrozenInstanceError):
            ref.resource_type = "other"  # type: ignore[misc]


class VersionAssetRefDomainTests(unittest.TestCase):
    def test_g_valid_version_asset_ref(self) -> None:
        var = _var(role="thumbnail", ordinal=2, required=False)
        self.assertEqual(var.role, "thumbnail")
        self.assertEqual(var.ordinal, 2)
        self.assertFalse(var.required)

    def test_h_role_must_match_pattern(self) -> None:
        with self.assertRaises(InvalidVersionAssetRefError):
            _var(role="Primary")
        with self.assertRaises(InvalidVersionAssetRefError):
            _var(role="")

    def test_i_ordinal_must_be_non_negative_int(self) -> None:
        with self.assertRaises(InvalidVersionAssetRefError):
            _var(ordinal=-1)
        with self.assertRaises(InvalidVersionAssetRefError):
            _var(ordinal=True)  # type: ignore[arg-type]

    def test_j_created_at_must_be_timezone_aware_and_frozen(self) -> None:
        with self.assertRaises(InvalidVersionAssetRefError):
            _var(created_at=datetime(2026, 8, 14, 12, 0))
        var = _var()
        with self.assertRaises(FrozenInstanceError):
            var.role = "other"  # type: ignore[misc]
        with self.assertRaises(InvalidVersionAssetRefError):
            replace(var, resource_ref="nope")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
