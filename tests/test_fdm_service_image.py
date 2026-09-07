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
PUBLICATION = json.loads((ROOT / "fdm-service.publication.json").read_text(encoding="utf-8"))


def service_lock_value() -> dict:
    return json.loads(SERVICE_LOCK_BYTES.decode("utf-8"))


def unpublished_service_lock_bytes() -> bytes:
    value = service_lock_value()
    value["status"] = "unpublished"
    value["digest"] = None
    value["source_commit"] = None
    return (json.dumps(value, indent=2) + "\n").encode()


def synthetic_published_service_lock_bytes() -> bytes:
    value = service_lock_value()
    value["status"] = "published"
    value["digest"] = "sha256:" + "1" * 64
    value["source_commit"] = "a" * 40
    return (json.dumps(value, indent=2) + "\n").encode()


def build_packet(**overrides):
    candidate_lock = unpublished_service_lock_bytes()
    values = {
        "service_lock_bytes": candidate_lock,
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


def test_committed_service_lock_is_published_and_bound_to_published_toolchain():
    lock = validate_service_lock(
        SERVICE_LOCK_BYTES,
        toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
        require_published=True,
    )
    toolchain = json.loads(TOOLCHAIN_LOCK_BYTES)
    assert lock["schema"] == FDM_SERVICE_LOCK_SCHEMA
    assert lock["status"] == "published"
    assert lock["digest"] == "sha256:206058fa5d476cd3c4363b7f6b16ff68eda473deef32eef7245d9ba146ca9491"
    assert lock["source_commit"] == "dce91058be7b306eaeb3b1ab0ab2fbf5c9081f1f"
    assert lock["platform"] == "linux/amd64"
    assert lock["build_recipe"] == "Dockerfile.authority"
    assert lock["toolchain"]["image"] == toolchain["image"]
    assert lock["toolchain"]["digest"] == toolchain["digest"]


def test_committed_service_publication_receipt_matches_lock_and_preserves_safety_boundaries():
    lock = service_lock_value()
    assert PUBLICATION["schema"] == "workpiece-fdm-authority-service-publication-v1"
    assert PUBLICATION["approved_frozen_source_commit"] == lock["source_commit"]
    assert PUBLICATION["image"] == lock["image"]
    assert PUBLICATION["registry_digest"] == lock["digest"]
    assert PUBLICATION["platform"] == lock["platform"]
    assert PUBLICATION["toolchain_reference"] == (
        f"{lock['toolchain']['image']}@{lock['toolchain']['digest']}"
    )
    assert PUBLICATION["pre_push_bytes_verified"] is True
    assert PUBLICATION["push_digest_matches_independent_resolution"] is True
    assert PUBLICATION["digest_pinned_pullback_verified"] is True
    assert PUBLICATION["pulled_bytes_match_reviewed_candidate"] is True
    assert PUBLICATION["pulled_toolchain_bytes_match_published_toolchain_receipt"] is True
    assert PUBLICATION["deployment_performed"] is False
    assert PUBLICATION["machine_qualification_performed"] is False
    assert PUBLICATION["production_enablement_performed"] is False
    assert PUBLICATION["human_review_still_required"] is True


def test_committed_published_service_runtime_requires_exact_registry_reference_and_source_commit():
    expected = (
        "ghcr.io/workpiece-gr/fdm-authority-service@"
        "sha256:206058fa5d476cd3c4363b7f6b16ff68eda473deef32eef7245d9ba146ca9491"
    )
    identity = validate_published_service_runtime(
        service_lock_bytes=SERVICE_LOCK_BYTES,
        toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
        runtime_image_ref=expected,
        service_commit="dce91058be7b306eaeb3b1ab0ab2fbf5c9081f1f",
    )
    assert identity["reference"] == expected
    assert identity["sourceCommit"] == "dce91058be7b306eaeb3b1ab0ab2fbf5c9081f1f"

    with pytest.raises(ValueError, match="exactly match"):
        validate_published_service_runtime(
            service_lock_bytes=SERVICE_LOCK_BYTES,
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref="workpiece-fdm-authority:candidate@sha256:" + "1" * 64,
            service_commit="dce91058be7b306eaeb3b1ab0ab2fbf5c9081f1f",
        )
    with pytest.raises(ValueError, match="service commit differs"):
        validate_published_service_runtime(
            service_lock_bytes=SERVICE_LOCK_BYTES,
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref=expected,
            service_commit="b" * 40,
        )


def test_synthetic_unpublished_service_lock_cannot_satisfy_production_runtime_identity():
    with pytest.raises(ValueError, match="published final service image"):
        validate_published_service_runtime(
            service_lock_bytes=unpublished_service_lock_bytes(),
            toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES,
            runtime_image_ref="ghcr.io/workpiece-gr/fdm-authority-service@sha256:" + "1" * 64,
            service_commit="a" * 40,
        )


def test_service_lock_rejects_toolchain_drift():
    value = service_lock_value()
    value["toolchain"]["digest"] = "sha256:" + "f" * 64
    payload = (json.dumps(value, indent=2) + "\n").encode()
    with pytest.raises(ValueError, match="toolchain digest differs"):
        validate_service_lock(payload, toolchain_lock_bytes=TOOLCHAIN_LOCK_BYTES)


def test_review_packet_is_non_authoritative_and_binds_exact_candidate_inputs():
    candidate_lock = unpublished_service_lock_bytes()
    packet = build_packet(service_lock_bytes=candidate_lock)
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
    assert packet["sourceInputs"]["serviceLockSha256"] == hashlib.sha256(candidate_lock).hexdigest()
    assert packet["retainedCandidateEvidence"]["pipFreezeSha256"] == hashlib.sha256(
        b"fastapi==0.116.1\n"
    ).hexdigest()


def test_published_service_lock_cannot_be_represented_as_prepublication_review():
    with pytest.raises(ValueError, match="only be created from an unpublished"):
        build_packet(service_lock_bytes=synthetic_published_service_lock_bytes())


def test_review_packet_rejects_mutable_local_tag_as_image_identity():
    with pytest.raises(ValueError, match="local Docker image id"):
        build_packet(service_local_image_id="workpiece-fdm-authority:review")
