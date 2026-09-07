import hashlib
import json
from pathlib import Path

import pytest

from app.fdm_service_image import (
    FDM_SERVICE_LOCK_SCHEMA,
    FDM_SERVICE_PUBLICATION_REVIEW_VERSION,
    build_service_publication_review_packet,
    validate_published_service_runtime,
    validate_service_lock,
)


ROOT = Path(__file__).parents[1]
SERVICE_LOCK_BYTES = (ROOT / "fdm-service.lock.json").read_bytes()
TOOLCHAIN_LOCK_BYTES = (ROOT / "fdm-toolchain.lock.json").read_bytes()


def service_lock_value() -> dict:
    return json.loads(SERVICE_LOCK_BYTES.decode("utf-8"))


def published_service_lock_bytes() -> bytes:
    value = service_lock_value()
    value["status"] = "published"
    value["digest"] = "sha256:" + "1" * 64
    value["source_commit"] = "a" * 40
    return (json.dumps(value, indent=2) + "\n").encode()


def build_packet(**overrides):
    values = {
        "service_lock_bytes": SERVICE_LOCK_BYTES,
        "toolchain_lock_bytes": TOOLCHAIN_LOCK_BYTES,
        "service_recipe_bytes": (ROOT / "Dockerfile.authority").read_bytes(),
        "requirements_bytes": (ROOT / "requirements.txt").read_bytes(),
        "app_tree_sha256": "2" * 64,
        "profiles_tree_sha256": "3" * 64,
        "service_commit": "a" * 40,
        "service_local_image_id": "sha256:" + "4" * 64,
        "pip_freeze_bytes": b"fastapi==0.116.1\n",
    }
    values.update(overrides)
    return build_service_publication_review_packet(**values)


def test_committed_service_lock_is_unpublished_and_bound_to_published_toolchain():
    lock = validate_service_lock(
        SERVICE_LOCK_BYTES,
        toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
    )
    toolchain = json.loads(TOOLCHAIN_LOCK_BYTES)
    assert lock["schema"] == FDM_SERVICE_LOCK_SCHEMA
    assert lock["status"] == "unpublished"
    assert lock["digest"] is None
    assert lock["source_commit"] is None
    assert lock["platform"] == "linux/amd64"
    assert lock["build_recipe"] == "Dockerfile.authority"
    assert lock["toolchain"]["image"] == toolchain["image"]
    assert lock["toolchain"]["digest"] == toolchain["digest"]


def test_unpublished_service_lock_cannot_satisfy_production_runtime_identity():
    with pytest.raises(ValueError, match="published final service image"):
        validate_published_service_runtime(
            service_lock_bytes=SERVICE_LOCK_BYTES,
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref="ghcr.io/workpiece-gr/fdm-authority-service@sha256:" + "1" * 64,
            service_commit="a" * 40,
        )


def test_published_service_lock_requires_exact_registry_reference_and_source_commit():
    lock_bytes = published_service_lock_bytes()
    expected = "ghcr.io/workpiece-gr/fdm-authority-service@sha256:" + "1" * 64
    identity = validate_published_service_runtime(
        service_lock_bytes=lock_bytes,
        toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
        runtime_image_ref=expected,
        service_commit="a" * 40,
    )
    assert identity["reference"] == expected
    assert identity["sourceCommit"] == "a" * 40

    with pytest.raises(ValueError, match="exactly match"):
        validate_published_service_runtime(
            service_lock_bytes=lock_bytes,
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref="workpiece-fdm-authority:candidate@sha256:" + "1" * 64,
            service_commit="a" * 40,
        )
    with pytest.raises(ValueError, match="service commit differs"):
        validate_published_service_runtime(
            service_lock_bytes=lock_bytes,
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref=expected,
            service_commit="b" * 40,
        )


def test_service_lock_rejects_toolchain_drift():
    value = service_lock_value()
    value["toolchain"]["digest"] = "sha256:" + "f" * 64
    payload = (json.dumps(value, indent=2) + "\n").encode()
    with pytest.raises(ValueError, match="toolchain digest differs"):
        validate_service_lock(payload, toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES)


def test_review_packet_is_non_authoritative_and_binds_exact_candidate_inputs():
    packet = build_packet()
    assert packet["contractVersion"] == FDM_SERVICE_PUBLICATION_REVIEW_VERSION
    assert packet["authorityState"] == "publication_review_candidate"
    assert packet["authorityCriticalComplete"] is False
    assert packet["publication"]["performed"] is False
    assert packet["publication"]["registryDigest"] is None
    assert packet["publication"]["explicitApprovalRequired"] is True
    assert packet["production"]["productionAuthorityEligible"] is False
    assert packet["production"]["productionEnablementPerformed"] is False
    assert packet["production"]["humanReviewStillRequired"] is True
    assert packet["retainedCandidateEvidence"]["localImageIdIsRegistryDigest"] is False
    assert packet["reviewTarget"]["imageRepository"] == "ghcr.io/workpiece-gr/fdm-authority-service"
    assert packet["reviewTarget"]["reviewTag"] == "authority-v2-aaaaaaaaaaaa-amd64"
    assert packet["sourceInputs"]["serviceLockSha256"] == hashlib.sha256(SERVICE_LOCK_BYTES).hexdigest()
    assert packet["retainedCandidateEvidence"]["pipFreezeSha256"] == hashlib.sha256(
        b"fastapi==0.116.1\n"
    ).hexdigest()


def test_published_service_lock_cannot_be_represented_as_prepublication_review():
    with pytest.raises(ValueError, match="only be created from an unpublished"):
        build_packet(service_lock_bytes=published_service_lock_bytes())


def test_review_packet_rejects_mutable_local_tag_as_image_identity():
    with pytest.raises(ValueError, match="local Docker image id"):
        build_packet(service_local_image_id="workpiece-fdm-authority:review")
