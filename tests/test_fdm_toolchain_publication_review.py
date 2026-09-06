import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.fdm_toolchain_publication_review import (
    FDM_TOOLCHAIN_PUBLICATION_REVIEW_VERSION,
    build_toolchain_publication_review_packet,
)


ROOT = Path(__file__).parents[1]
LOCK_BYTES = (ROOT / "fdm-toolchain.lock.json").read_bytes()
TOOLCHAIN_RECIPE_BYTES = (ROOT / "Dockerfile.toolchain").read_bytes()
SERVICE_RECIPE_BYTES = (ROOT / "Dockerfile.authority").read_bytes()
RUNTIME_BYTES = b"exact extracted Orca AppRun bytes for publication review unit test\n"
PACKAGE_BYTES = b"ca-certificates\t1\npython3\t2\n"


def lock_value() -> dict:
    return json.loads(LOCK_BYTES.decode("utf-8"))


def manifest_bytes(lock: dict | None = None, runtime: bytes = RUNTIME_BYTES, packages: bytes = PACKAGE_BYTES) -> bytes:
    lock = lock or lock_value()
    value = {
        "schema": "workpiece-fdm-toolchain-v1",
        "platform": lock["platform"],
        "base_image": lock["base_image"]["reference"],
        "orca_version": lock["orca"]["version"],
        "orca_asset": lock["orca"]["asset"],
        "orca_asset_sha256": lock["orca"]["asset_sha256"],
        "orca_runtime_path": lock["orca"]["runtime_path"],
        "orca_runtime_sha256": hashlib.sha256(runtime).hexdigest(),
        "packages_sha256": hashlib.sha256(packages).hexdigest(),
    }
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def build_packet(**overrides):
    values = {
        "lock_bytes": LOCK_BYTES,
        "manifest_bytes": manifest_bytes(),
        "package_inventory_bytes": PACKAGE_BYTES,
        "orca_runtime_bytes": RUNTIME_BYTES,
        "toolchain_recipe_bytes": TOOLCHAIN_RECIPE_BYTES,
        "service_recipe_bytes": SERVICE_RECIPE_BYTES,
        "service_commit": "c" * 40,
        "toolchain_local_image_id": "sha256:" + "d" * 64,
        "service_local_image_id": "sha256:" + "e" * 64,
    }
    values.update(overrides)
    return build_toolchain_publication_review_packet(**values)


def test_committed_unpublished_lock_builds_non_authoritative_review_packet():
    packet = build_packet()
    assert packet["contractVersion"] == FDM_TOOLCHAIN_PUBLICATION_REVIEW_VERSION
    assert packet["authorityState"] == "publication_review_candidate"
    assert packet["authorityCriticalComplete"] is False
    assert packet["publication"]["performed"] is False
    assert packet["publication"]["registryDigest"] is None
    assert packet["publication"]["explicitApprovalRequired"] is True
    assert packet["production"]["productionAuthorityEligible"] is False
    assert packet["production"]["productionEnablementPerformed"] is False
    assert packet["retainedCandidateEvidence"]["localImageIdsAreRegistryDigests"] is False
    assert packet["reviewTarget"]["tagReference"] == (
        "ghcr.io/workpiece-gr/fdm-slicer-toolchain:orca-2.4.2-noble-20260810-amd64"
    )


def test_packet_binds_exact_recipe_and_evidence_bytes():
    packet = build_packet()
    assert packet["sourceInputs"]["toolchainLockSha256"] == hashlib.sha256(LOCK_BYTES).hexdigest()
    assert packet["sourceInputs"]["toolchainRecipe"]["sha256"] == hashlib.sha256(TOOLCHAIN_RECIPE_BYTES).hexdigest()
    assert packet["sourceInputs"]["serviceRecipe"]["sha256"] == hashlib.sha256(SERVICE_RECIPE_BYTES).hexdigest()
    assert packet["retainedCandidateEvidence"]["toolchainManifestSha256"] == hashlib.sha256(manifest_bytes()).hexdigest()
    assert packet["retainedCandidateEvidence"]["packageInventorySha256"] == hashlib.sha256(PACKAGE_BYTES).hexdigest()
    assert packet["retainedCandidateEvidence"]["orcaRuntimeSha256"] == hashlib.sha256(RUNTIME_BYTES).hexdigest()


def test_published_lock_cannot_be_misrepresented_as_prepublication_review():
    value = copy.deepcopy(lock_value())
    value["status"] = "published"
    value["digest"] = "sha256:" + "b" * 64
    lock_bytes = (json.dumps(value, indent=2) + "\n").encode()
    with pytest.raises(ValueError, match="only be created from the unpublished"):
        build_packet(lock_bytes=lock_bytes, manifest_bytes=manifest_bytes(value))


def test_package_inventory_bytes_must_match_manifest():
    with pytest.raises(ValueError, match="package inventory bytes"):
        build_packet(package_inventory_bytes=PACKAGE_BYTES + b"tamper")


def test_runtime_bytes_must_match_manifest():
    with pytest.raises(ValueError, match="Actual Orca runtime bytes"):
        build_packet(orca_runtime_bytes=RUNTIME_BYTES + b"tamper")


def test_local_candidate_image_ids_are_not_allowed_to_be_mutable_tags():
    with pytest.raises(ValueError, match="local Docker image id"):
        build_packet(toolchain_local_image_id="workpiece-fdm-toolchain:candidate")


def test_service_commit_must_be_exact_sha():
    with pytest.raises(ValueError, match="40-character service commit SHA"):
        build_packet(service_commit="main")


def test_exact_recipe_bytes_are_mandatory():
    with pytest.raises(ValueError, match="toolchain recipe bytes"):
        build_packet(toolchain_recipe_bytes=b"")
    with pytest.raises(ValueError, match="service recipe bytes"):
        build_packet(service_recipe_bytes=b"")
