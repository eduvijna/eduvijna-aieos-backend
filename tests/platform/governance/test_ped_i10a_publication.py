"""PED-I10A BaselinePublicationGovernanceV1 unit/architecture tests."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import uuid4, uuid7

import pytest

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.platform.governance.publication import (
    PUBLICATION_GOVERNANCE_V1,
    BaselinePublicationGovernanceV1,
)
from tests.dbutil import REPO_ROOT

pytestmark = pytest.mark.ped_i10a

PUB_SRC = (
    REPO_ROOT
    / "src"
    / "aieos"
    / "platform"
    / "governance"
    / "publication.py"
)


class TestPublicationGovernanceV1:
    def test_production_identity_and_success(self) -> None:
        policy = BaselinePublicationGovernanceV1()
        assert not policy.__class__.__name__.startswith(
            ("Allow", "Stub", "Fake", "Test", "NoOp")
        )
        assert policy.policy_id == PUBLICATION_GOVERNANCE_V1
        assert policy.policy_id == "publication_governance.v1"
        policy.evaluate(
            tenant_id=uuid4(),
            content_id=ContentId(uuid7()),
            version_id=ContentVersionId(uuid7()),
        )

    def test_no_authz_network_db_or_asset_logic(self) -> None:
        source = PUB_SRC.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        blob = "\n".join(imports).lower()
        for needle in (
            "sqlalchemy",
            "httpx",
            "requests",
            "urllib",
            "authorization",
            "security.authorization",
            "asset_use",
            "opa",
            "cerbos",
            "casbin",
            "openfga",
            "cedar",
        ):
            assert needle not in blob, needle
        assert "CONTENT_PUBLISH" not in source
        assert "decide_capability" not in source
        assert "socket" not in source
        assert "connect(" not in source
        evaluate_src = inspect.getsource(BaselinePublicationGovernanceV1.evaluate)
        assert "capability" not in evaluate_src
        assert "jwt" not in evaluate_src.lower()
        assert "role" not in evaluate_src.lower()
