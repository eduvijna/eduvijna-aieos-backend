"""WPI-OCI-I01 Backend OCI provenance unit tests (no Docker/provider)."""

from __future__ import annotations

import json
import sys
from copy import deepcopy

import pytest

from tests.dbutil import REPO_ROOT

RELEASE = REPO_ROOT / "tools" / "release"
sys.path.insert(0, str(RELEASE))

from backend_oci_common import (  # noqa: E402
    ARTIFACT_KIND,
    BASE_IMAGE_DIGEST,
    BASE_IMAGE_REFERENCE,
    CLASSIFICATION,
    EXPECTED_RUNTIME_USER,
    SOURCE_REPOSITORY,
    canonical_json,
    default_command_contract,
    require_full_git_sha,
)
from build_backend_oci_provenance import build_prepublication_receipt  # noqa: E402
from verify_backend_oci_provenance import verify_receipt  # noqa: E402

BACKEND = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ARCH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
INFRA = "cccccccccccccccccccccccccccccccccccccccc"


def _inspect(*, revision: str = BACKEND, arch: str = ARCH, infra: str = INFRA) -> dict:
    return {
        "Id": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "User": EXPECTED_RUNTIME_USER,
            "Cmd": default_command_contract(),
            "Labels": {
                "org.opencontainers.image.title": "aieos-backend",
                "org.opencontainers.image.description": "candidate",
                "org.opencontainers.image.version": "0.1.0",
                "org.opencontainers.image.source": "https://github.com/eduvijna/eduvijna-aieos-backend",
                "org.opencontainers.image.revision": revision,
                "io.eduvijna.aieos.classification": "PRODUCTION_BACKEND_RUNTIME",
                "io.eduvijna.aieos.application_version": "0.1.0",
                "io.eduvijna.aieos.git_revision": revision,
                "io.eduvijna.aieos.architecture_revision": arch,
                "io.eduvijna.aieos.infrastructure_revision": infra,
            },
            "Env": ["PATH=/opt/venv/bin", "UV_COMPILE_BYTECODE=1"],
        },
    }


def _receipt(**overrides):
    receipt = build_prepublication_receipt(
        repo_root=REPO_ROOT,
        image="unused:local",
        backend_git_sha=BACKEND,
        architecture_git_sha=ARCH,
        infrastructure_git_sha=INFRA,
        inspect_obj=_inspect(),
        require_clean_source=False,
    )
    receipt.update(overrides)
    return receipt


def test_valid_receipt_deterministic_and_verifies() -> None:
    a = _receipt()
    b = _receipt()
    assert canonical_json(a) == canonical_json(b)
    verify_receipt(a)
    assert a["artifact_kind"] == ARTIFACT_KIND
    assert a["classification"] == CLASSIFICATION
    assert a["source_repository"] == SOURCE_REPOSITORY
    assert a["publication_performed"] is False
    assert a["base_image"]["digest"] == BASE_IMAGE_DIGEST
    assert a["base_image"]["reference"] == BASE_IMAGE_REFERENCE
    # image_config_id is local config identity, not registry manifest authority
    assert "manifest_digest" not in a
    assert a["image_config_id"].startswith("sha256:")


def test_invalid_git_sha_rejected() -> None:
    with pytest.raises(ValueError):
        require_full_git_sha("not-a-sha")
    with pytest.raises(ValueError):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha="ABC",
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=_inspect(),
            require_clean_source=False,
        )


def test_dirty_source_rejected_in_authoritative_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, cwd, check, capture_output, text):  # noqa: ANN001
        class R:
            stdout = ""

        if cmd[:2] == ["git", "rev-parse"]:
            R.stdout = BACKEND
            return R()
        if cmd[:2] == ["git", "status"]:
            R.stdout = " M dirty"
            return R()
        raise AssertionError(cmd)

    monkeypatch.setattr("build_backend_oci_provenance.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="dirty"):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha=BACKEND,
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=_inspect(),
            require_clean_source=True,
        )


def test_version_mismatch_rejected() -> None:
    import build_backend_oci_provenance as mod

    original = mod.assert_version_coherence
    mod.assert_version_coherence = lambda root: (_ for _ in ()).throw(
        ValueError("VERSION (0.1.0) != pyproject.toml version (9.9.9)")
    )
    try:
        with pytest.raises(ValueError, match="VERSION"):
            build_prepublication_receipt(
                repo_root=REPO_ROOT,
                image="x",
                backend_git_sha=BACKEND,
                architecture_git_sha=ARCH,
                infrastructure_git_sha=INFRA,
                inspect_obj=_inspect(),
                require_clean_source=False,
            )
    finally:
        mod.assert_version_coherence = original


@pytest.mark.parametrize(
    "field,value",
    [
        ("python_version", "3.13.0"),
        ("uv_version", "0.11.0"),
        ("build_platform", "linux/arm64"),
        ("runtime_user", "0:0"),
        ("publication_performed", True),
        ("publication_authorized", True),
        ("deployment_authorized", True),
    ],
)
def test_wrong_scalar_fields_rejected(field: str, value: object) -> None:
    receipt = _receipt(**{field: value})
    with pytest.raises(ValueError):
        verify_receipt(receipt)


def test_missing_label_rejected() -> None:
    insp = _inspect()
    del insp["Config"]["Labels"]["org.opencontainers.image.title"]
    with pytest.raises(ValueError):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha=BACKEND,
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=insp,
            require_clean_source=False,
        )


def test_revision_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha=BACKEND,
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=_inspect(revision="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
            require_clean_source=False,
        )


def test_architecture_and_infrastructure_label_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha=BACKEND,
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=_inspect(arch="ffffffffffffffffffffffffffffffffffffffff"),
            require_clean_source=False,
        )
    with pytest.raises(ValueError):
        build_prepublication_receipt(
            repo_root=REPO_ROOT,
            image="x",
            backend_git_sha=BACKEND,
            architecture_git_sha=ARCH,
            infrastructure_git_sha=INFRA,
            inspect_obj=_inspect(infra="1111111111111111111111111111111111111111"),
            require_clean_source=False,
        )


def test_unknown_and_publication_fields_rejected() -> None:
    receipt = _receipt()
    bad = deepcopy(receipt)
    bad["extra"] = "nope"
    with pytest.raises(ValueError, match="unknown"):
        verify_receipt(bad)
    for field in ("registry", "repository", "manifest_digest"):
        bad2 = deepcopy(receipt)
        bad2[field] = "x"
        with pytest.raises(ValueError):
            verify_receipt(bad2)


def test_secret_and_ev_rejected() -> None:
    receipt = _receipt()
    bad = deepcopy(receipt)
    bad["token"] = "dop_v1_x"
    with pytest.raises(ValueError):
        verify_receipt(bad)
    bad2 = deepcopy(receipt)
    bad2["oci_labels"] = dict(receipt["oci_labels"])
    bad2["oci_labels"]["org.opencontainers.image.description"] = "EV[ABCDEFGH]"
    with pytest.raises(ValueError):
        verify_receipt(bad2)


def test_default_command_mismatch_rejected() -> None:
    receipt = _receipt()
    receipt["default_command"] = ["python", "-m", "aieos.platform.runtime.entrypoints.workflow_dispatcher_main"]
    with pytest.raises(ValueError):
        verify_receipt(receipt)


def test_malformed_base_digest_rejected() -> None:
    receipt = _receipt()
    receipt["base_image"] = {"reference": "ghcr.io/astral-sh/uv:0.12.4", "digest": "latest"}
    with pytest.raises(ValueError):
        verify_receipt(receipt)


def test_canonical_json_stable() -> None:
    payload = {"b": 1, "a": {"z": 2, "y": [3, 1]}}
    assert canonical_json(payload) == '{"a":{"y":[3,1],"z":2},"b":1}'
    assert json.loads(canonical_json(_receipt()))["schema_version"] == 1
