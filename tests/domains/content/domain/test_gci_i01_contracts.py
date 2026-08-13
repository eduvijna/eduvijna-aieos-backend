"""GCI-I01 Generic Content domain-contract tests."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

import aieos.domains.content.domain as content_domain
import aieos.domains.content.domain.identities as identities
import aieos.domains.content.domain.version as version_mod
from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.errors import (
    ContentVersionImmutabilityError,
    InvalidContentAggregateError,
    InvalidContentIdentityError,
    InvalidOriginError,
    InvalidPayloadError,
    InvalidReviewDecisionError,
    InvalidStewardshipStateError,
    InvalidVersionNumberError,
    ParentLineageError,
    SchemaNotFoundError,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin, parse_content_origin
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import (
    ReviewDecision,
    ReviewDecisionCode,
    parse_review_decision_code,
)
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.domain.states import StewardshipState, parse_stewardship_state
from aieos.domains.content.domain.version import (
    ContentPayload,
    ContentVersion,
    PayloadSha256,
    canonical_payload_json,
    validate_linear_parent,
)

from tests.domains.content.domain.fakes import TEST_GENERIC_V1, TEST_GENERIC_V2


def _now() -> datetime:
    return datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _foreign_uuid() -> UUID:
    return uuid.uuid7()


def _version(
    *,
    content_id: ContentId,
    tenant_id: UUID,
    version_number: int,
    parent: ContentVersionId | None,
    payload: dict[str, object] | None = None,
    version_id: ContentVersionId | None = None,
) -> ContentVersion:
    return ContentVersion(
        version_id=version_id or ContentVersionId.generate(),
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=VersionNumber(version_number),
        parent_version_id=parent,
        schema_id=SchemaId("test.generic"),
        schema_version=SchemaVersion(1),
        payload=ContentPayload.from_mapping(payload or {"marker": "v1"}),
        origin=ContentOrigin.HUMAN,
        created_at=_now(),
        created_by_principal_id=_foreign_uuid(),
    )


class StewardshipVocabularyTests(unittest.TestCase):
    def test_exact_stewardship_vocabulary(self) -> None:
        self.assertEqual(
            {member.value for member in StewardshipState},
            {"DRAFT", "GENERATED", "IN_REVIEW", "APPROVED", "ARCHIVED"},
        )
        for name in ("DRAFT", "GENERATED", "IN_REVIEW", "APPROVED", "ARCHIVED"):
            self.assertIs(parse_stewardship_state(name), StewardshipState[name])

    def test_forbidden_stewardship_states_are_absent(self) -> None:
        forbidden = {
            "PUBLISHED",
            "GENERATING",
            "REJECTED",
            "DELETED",
            "FAILED",
            "REJECT",
            "REQUEST_CHANGES",
        }
        present = {member.value for member in StewardshipState}
        self.assertTrue(forbidden.isdisjoint(present))
        for name in forbidden:
            with self.subTest(name=name):
                with self.assertRaises(InvalidStewardshipStateError):
                    parse_stewardship_state(name)
                self.assertFalse(hasattr(StewardshipState, name) and getattr(StewardshipState, name) in StewardshipState)


class OriginVocabularyTests(unittest.TestCase):
    def test_exact_origin_vocabulary(self) -> None:
        self.assertEqual(
            {member.value for member in ContentOrigin},
            {"HUMAN", "AI", "IMPORT", "SYSTEM"},
        )
        for name in ("HUMAN", "AI", "IMPORT", "SYSTEM"):
            self.assertIs(parse_content_origin(name), ContentOrigin[name])

    def test_unknown_origin_rejected(self) -> None:
        with self.assertRaises(InvalidOriginError):
            parse_content_origin("TRUSTED")


class IdentityDistinctionTests(unittest.TestCase):
    def test_content_id_and_version_id_are_distinct_types(self) -> None:
        raw = ContentId.generate().value
        content_id = ContentId(raw)
        version_id = ContentVersionId(raw)
        self.assertIsInstance(content_id, ContentId)
        self.assertIsInstance(version_id, ContentVersionId)
        self.assertNotEqual(type(content_id), type(version_id))
        self.assertNotEqual(content_id, version_id)

    def test_identities_require_uuidv7(self) -> None:
        v4 = uuid4()
        with self.assertRaises(InvalidContentIdentityError):
            ContentId(v4)
        with self.assertRaises(InvalidContentIdentityError):
            ContentVersionId(v4)

    def test_version_number_and_aggregate_revision_are_distinct(self) -> None:
        version_number = VersionNumber(3)
        revision = AggregateRevision(3)
        self.assertNotEqual(type(version_number), type(revision))
        self.assertNotEqual(version_number, revision)
        with self.assertRaises(InvalidVersionNumberError):
            VersionNumber(0)
        AggregateRevision(0)

    def test_generic_content_does_not_own_shared_platform_identities(self) -> None:
        forbidden = ("TenantId", "PrincipalId", "CorrelationId", "DelegationId")
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse(hasattr(identities, name))
                self.assertNotIn(name, content_domain.__all__)
                self.assertFalse(hasattr(content_domain, name))


class ContentVersionContractTests(unittest.TestCase):
    def test_immutability_semantics(self) -> None:
        content_id = ContentId.generate()
        tenant_id = _foreign_uuid()
        version = _version(content_id=content_id, tenant_id=tenant_id, version_number=1, parent=None)
        with self.assertRaises(FrozenInstanceError):
            version.origin = ContentOrigin.AI  # type: ignore[misc]
        mutated = replace(
            version,
            payload=ContentPayload.from_mapping({"marker": "changed"}),
        )
        with self.assertRaises(ContentVersionImmutabilityError):
            version.assert_unmutated_relative_to(mutated)
        correction = _version(
            content_id=content_id,
            tenant_id=tenant_id,
            version_number=2,
            parent=version.version_id,
            payload={"marker": "changed"},
        )
        version.assert_unmutated_relative_to(correction)
        self.assertNotEqual(correction.version_id, version.version_id)

    def test_immutability_error_is_pairwise_with_no_hidden_registry(self) -> None:
        content_id = ContentId.generate()
        tenant_id = _foreign_uuid()
        shared_version_id = ContentVersionId.generate()
        first = _version(
            content_id=content_id,
            tenant_id=tenant_id,
            version_number=1,
            parent=None,
            payload={"marker": "a"},
            version_id=shared_version_id,
        )
        second = _version(
            content_id=content_id,
            tenant_id=tenant_id,
            version_number=1,
            parent=None,
            payload={"marker": "b"},
            version_id=shared_version_id,
        )
        self.assertEqual(first.version_id, second.version_id)
        self.assertNotEqual(first.payload, second.payload)
        with self.assertRaises(ContentVersionImmutabilityError):
            first.assert_unmutated_relative_to(second)
        mutable_module_state = {
            name: value
            for name, value in vars(version_mod).items()
            if not name.startswith("_") and isinstance(value, (dict, list, set))
        }
        self.assertEqual(mutable_module_state, {})
        class_dict = getattr(ContentVersion, "__dict__", {})
        for name, value in class_dict.items():
            if name.startswith("__"):
                continue
            self.assertFalse(
                isinstance(value, (dict, list, set)),
                f"ContentVersion class attribute {name!r} looks like a registry",
            )

    def test_same_aggregate_linear_parent_lineage(self) -> None:
        content_id = ContentId.generate()
        tenant_id = _foreign_uuid()
        v1 = _version(content_id=content_id, tenant_id=tenant_id, version_number=1, parent=None)
        v2 = _version(
            content_id=content_id,
            tenant_id=tenant_id,
            version_number=2,
            parent=v1.version_id,
        )
        validate_linear_parent(v2, v1)

        other_content = _version(
            content_id=ContentId.generate(),
            tenant_id=tenant_id,
            version_number=2,
            parent=v1.version_id,
        )
        with self.assertRaises(ParentLineageError):
            validate_linear_parent(other_content, v1)

        other_tenant = _version(
            content_id=content_id,
            tenant_id=_foreign_uuid(),
            version_number=2,
            parent=v1.version_id,
        )
        with self.assertRaises(ParentLineageError):
            validate_linear_parent(other_tenant, v1)

        skip = _version(
            content_id=content_id,
            tenant_id=tenant_id,
            version_number=3,
            parent=v1.version_id,
        )
        with self.assertRaises(ParentLineageError):
            validate_linear_parent(skip, v1)

    def test_first_version_must_be_number_one_iff_no_parent(self) -> None:
        with self.assertRaises(ParentLineageError):
            _version(
                content_id=ContentId.generate(),
                tenant_id=_foreign_uuid(),
                version_number=2,
                parent=None,
            )
        with self.assertRaises(ParentLineageError):
            _version(
                content_id=ContentId.generate(),
                tenant_id=_foreign_uuid(),
                version_number=1,
                parent=ContentVersionId.generate(),
            )


class SchemaRegistryContractTests(unittest.TestCase):
    def test_multiple_schema_versions_coexist_and_historical_lookup(self) -> None:
        registry = ContentSchemaRegistry()
        registry.register(TEST_GENERIC_V1)
        registry.register(TEST_GENERIC_V2)
        self.assertEqual(registry.list_versions("test.generic"), (1, 2))
        v1 = registry.get("test.generic", 1)
        v2 = registry.get(SchemaId("test.generic"), SchemaVersion(2))
        self.assertEqual(int(v1.schema_version), 1)
        self.assertEqual(int(v2.schema_version), 2)
        v1.validate({"marker": "ok"})
        v2.validate({"marker": "ok", "extra": True})
        newer_does_not_remove_old = registry.resolve("test.generic", 1)
        self.assertEqual(int(newer_does_not_remove_old.schema_version), 1)
        with self.assertRaises(SchemaNotFoundError):
            registry.get("test.generic", 3)


class ReviewDecisionContractTests(unittest.TestCase):
    def test_exact_review_decision_vocabulary(self) -> None:
        self.assertEqual(
            {member.value for member in ReviewDecisionCode},
            {"APPROVE", "REQUEST_CHANGES", "REJECT"},
        )
        for name in ("APPROVE", "REQUEST_CHANGES", "REJECT"):
            self.assertIs(parse_review_decision_code(name), ReviewDecisionCode[name])

    def test_review_requires_exact_version_and_does_not_transfer(self) -> None:
        v1 = ContentVersionId.generate()
        v2 = ContentVersionId.generate()
        decision = ReviewDecision(
            review_decision_id=ReviewDecisionId.generate(),
            tenant_id=_foreign_uuid(),
            content_id=ContentId.generate(),
            version_id=v1,
            decision=ReviewDecisionCode.APPROVE,
            actor_principal_id=_foreign_uuid(),
            decided_at=_now(),
            correlation_id=_foreign_uuid(),
            comment="ok",
        )
        self.assertTrue(decision.applies_to(v1))
        self.assertFalse(decision.applies_to(v2))
        with self.assertRaises(FrozenInstanceError):
            decision.version_id = v2  # type: ignore[misc]

    def test_request_changes_and_reject_are_not_stewardship_states(self) -> None:
        stewardship = {member.value for member in StewardshipState}
        self.assertNotIn(ReviewDecisionCode.REQUEST_CHANGES.value, stewardship)
        self.assertNotIn(ReviewDecisionCode.REJECT.value, stewardship)
        with self.assertRaises(InvalidStewardshipStateError):
            parse_stewardship_state("REJECT")
        with self.assertRaises(InvalidStewardshipStateError):
            parse_stewardship_state("REQUEST_CHANGES")
        with self.assertRaises(InvalidReviewDecisionError):
            parse_review_decision_code("ARCHIVED")


class PublicationContractTests(unittest.TestCase):
    def test_publication_is_distinct_from_approved_and_binds_version(self) -> None:
        version_id = ContentVersionId.generate()
        publication = Publication(
            publication_id=PublicationId.generate(),
            tenant_id=_foreign_uuid(),
            content_id=ContentId.generate(),
            version_id=version_id,
            approval_decision_id=ReviewDecisionId.generate(),
            publisher_principal_id=_foreign_uuid(),
            published_at=_now(),
            correlation_id=None,
        )
        self.assertFalse(publication.is_stewardship_state())
        self.assertIsNone(publication.equivalent_stewardship_state())
        self.assertNotIn("PUBLISHED", {member.value for member in StewardshipState})
        self.assertTrue(publication.references_version(version_id))
        self.assertFalse(publication.references_version(ContentVersionId.generate()))
        self.assertIsNot(type(publication), type(StewardshipState.APPROVED))

    def test_approved_content_need_not_be_published(self) -> None:
        content = Content(
            content_id=ContentId.generate(),
            tenant_id=_foreign_uuid(),
            owner_principal_id=_foreign_uuid(),
            content_type=ContentType("test.generic"),
            title="Fixture",
            description="",
            locale="en-IN",
            stewardship_state=StewardshipState.APPROVED,
            current_version_id=ContentVersionId.generate(),
            published_version_id=None,
            aggregate_revision=AggregateRevision(1),
            created_at=_now(),
            created_by_principal_id=_foreign_uuid(),
            updated_at=_now(),
            archived_at=None,
        )
        self.assertIs(content.stewardship_state, StewardshipState.APPROVED)
        self.assertIsNone(content.published_version_id)

    def test_archived_withdraws_active_publication_pointer(self) -> None:
        with self.assertRaises(InvalidContentAggregateError):
            Content(
                content_id=ContentId.generate(),
                tenant_id=_foreign_uuid(),
                owner_principal_id=_foreign_uuid(),
                content_type=ContentType("test.generic"),
                title="Archived with pointer",
                description="",
                locale="en-IN",
                stewardship_state=StewardshipState.ARCHIVED,
                current_version_id=ContentVersionId.generate(),
                published_version_id=ContentVersionId.generate(),
                aggregate_revision=AggregateRevision(2),
                created_at=_now(),
                created_by_principal_id=_foreign_uuid(),
                updated_at=_now(),
                archived_at=_now(),
            )
        archived = Content(
            content_id=ContentId.generate(),
            tenant_id=_foreign_uuid(),
            owner_principal_id=_foreign_uuid(),
            content_type=ContentType("test.generic"),
            title="Archived",
            description="",
            locale="en-IN",
            stewardship_state=StewardshipState.ARCHIVED,
            current_version_id=ContentVersionId.generate(),
            published_version_id=None,
            aggregate_revision=AggregateRevision(2),
            created_at=_now(),
            created_by_principal_id=_foreign_uuid(),
            updated_at=_now(),
            archived_at=_now(),
        )
        self.assertIsNone(archived.published_version_id)

    def test_non_archived_may_retain_published_pointer_beside_newer_current(self) -> None:
        published = ContentVersionId.generate()
        current = ContentVersionId.generate()
        content = Content(
            content_id=ContentId.generate(),
            tenant_id=_foreign_uuid(),
            owner_principal_id=_foreign_uuid(),
            content_type=ContentType("test.generic"),
            title="Draft after publish",
            description="",
            locale="en-IN",
            stewardship_state=StewardshipState.DRAFT,
            current_version_id=current,
            published_version_id=published,
            aggregate_revision=AggregateRevision(3),
            created_at=_now(),
            created_by_principal_id=_foreign_uuid(),
            updated_at=_now(),
            archived_at=None,
        )
        self.assertEqual(content.published_version_id, published)
        self.assertEqual(content.current_version_id, current)
        self.assertNotEqual(content.current_version_id, content.published_version_id)


class ContentPayloadImmutabilityTests(unittest.TestCase):
    def test_source_and_nested_structures_are_deeply_immutable(self) -> None:
        source: dict[str, object] = {
            "nested": {"a": 1, "inner": {"b": 2}},
            "items": [1, {"k": "v"}],
        }
        payload = ContentPayload.from_mapping(source)
        original_sha = payload.sha256
        source["nested"]["a"] = 99  # type: ignore[index]
        source["nested"]["inner"]["b"] = 100  # type: ignore[index]
        source["items"].append(3)  # type: ignore[union-attr]
        source["extra"] = "nope"
        self.assertEqual(payload.body["nested"]["a"], 1)  # type: ignore[index]
        self.assertEqual(payload.body["nested"]["inner"]["b"], 2)  # type: ignore[index]
        self.assertIsInstance(payload.body["items"], tuple)
        self.assertEqual(payload.body["items"][0], 1)
        self.assertEqual(payload.body["items"][1]["k"], "v")  # type: ignore[index]
        self.assertIsInstance(payload.body["nested"], MappingProxyType)
        self.assertNotIn("extra", payload.body)
        with self.assertRaises(AttributeError):
            payload.body["items"].append(4)  # type: ignore[union-attr]
        with self.assertRaises(TypeError):
            payload.body["nested"]["a"] = 7  # type: ignore[index]
        with self.assertRaises(TypeError):
            payload.body["x"] = 1  # type: ignore[index]
        self.assertEqual(payload.sha256, original_sha)
        recomputed = PayloadSha256(
            hashlib.sha256(canonical_payload_json(payload.body).encode("utf-8")).hexdigest()
        )
        self.assertEqual(payload.sha256, recomputed)

    def test_rejects_non_string_keys_and_unsupported_values(self) -> None:
        with self.assertRaises(InvalidPayloadError):
            ContentPayload.from_mapping({1: "x"})  # type: ignore[dict-item]
        with self.assertRaises(InvalidPayloadError):
            ContentPayload.from_mapping({"when": datetime(2026, 1, 1, tzinfo=UTC)})
        with self.assertRaises(InvalidPayloadError):
            ContentPayload.from_mapping({"id": uuid4()})
        with self.assertRaises(InvalidPayloadError):
            ContentPayload.from_mapping({"tags": {1, 2}})
        with self.assertRaises(InvalidPayloadError):
            ContentPayload.from_mapping({"n": float("nan")})

    def test_nested_json_canonicalizes_deterministically(self) -> None:
        left = ContentPayload.from_mapping(
            {"b": {"z": 1, "a": [2, {"k": True, "m": None}]}, "a": "x"}
        )
        right = ContentPayload.from_mapping(
            {"a": "x", "b": {"a": [2, {"m": None, "k": True}], "z": 1}}
        )
        self.assertEqual(left.sha256, right.sha256)
        self.assertEqual(
            canonical_payload_json(left.body),
            '{"a":"x","b":{"a":[2,{"k":true,"m":null}],"z":1}}',
        )


if __name__ == "__main__":
    unittest.main()
