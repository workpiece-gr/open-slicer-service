import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.fdm_toolchain_provenance import (
    FDM_TOOLCHAIN_PROVENANCE_VERSION,
    build_toolchain_provenance,
    validate_toolchain_lock,
    validate_toolchain_manifest,
)


ROOT = Path(__file__).parents[1]
LOCK_BYTES = (ROOT / "fdm-toolchain.lock.json").read_bytes()
RUNTIME_BYTES = b"exact extracted Orca AppRun bytes for unit test\n"


def lock_value() -> dict:
    return json.loads(LOCK_BYTES.decode("utf-8"))


def manifest_bytes(lock: dict | None = None, runtime: bytes = RUNTIME_BYTES) -> bytes:
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
        "packages_sha256": "a" * 64,
    }
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def published_lock_bytes() -> bytes:
    value = lock_value()
    value["status"] = "published"
    value["digest"] = "sha256:" + "b" * 64
    return (json.dumps(value, indent=2) + "\n").encode()


def test_committed_lock_is_explicitly_unpublished_and_fully_pinned_upstream():
    lock = validate_toolchain_lock(LOCK_BYTES)
    assert lock["status"] == "unpublished"
    assert lock["digest"] is None
    assert lock["platform"] == "linux/amd64"
    assert "@sha256:" in lock["base_image"]["reference"]
    assert len(lock["orca"]["asset_sha256"]) == 64
    assert lock["orca"]["version"] == "2.4.2"


def test_unpublished_lock_cannot_construct_production_authority_receipt():
    with pytest.raises(ValueError, match="reviewed published toolchain digest"):
        build_toolchain_provenance(
            lock_bytes=LOCK_BYTES,
            manifest_bytes=manifest_bytes(),
            orca_runtime_bytes=RUNTIME_BYTES,
            service_commit="c" * 40,
            runtime_image_ref="ghcr.io/workpiece-gr/open-slicer-service@sha256:" + "d" * 64,
        )


def test_candidate_receipt_is_content_addressed_but_not_authority_critical_complete():
    receipt = build_toolchain_provenance(
        lock_bytes=LOCK_BYTES,
        manifest_bytes=manifest_bytes(),
        orca_runtime_bytes=RUNTIME_BYTES,
        service_commit="c" * 40,
        runtime_image_ref="workpiece-fdm-authority:candidate@sha256:" + "d" * 64,
        require_published=False,
        candidate_toolchain_image_ref="workpiece-fdm-toolchain:candidate@sha256:" + "e" * 64,
    )
    assert receipt["contractVersion"] == FDM_TOOLCHAIN_PROVENANCE_VERSION
    assert receipt["authorityState"] == "evidence_candidate"
    assert receipt["authorityCriticalComplete"] is False
    assert receipt["lockStatus"] == "unpublished"
    assert receipt["orcaBinarySha256"] == hashlib.sha256(RUNTIME_BYTES).hexdigest()


def test_published_lock_and_final_runtime_digest_construct_complete_provenance():
    lock_bytes = published_lock_bytes()
    receipt = build_toolchain_provenance(
        lock_bytes=lock_bytes,
        manifest_bytes=manifest_bytes(json.loads(lock_bytes)),
        orca_runtime_bytes=RUNTIME_BYTES,
        service_commit="c" * 40,
        runtime_image_ref="ghcr.io/workpiece-gr/open-slicer-service@sha256:" + "d" * 64,
    )
    assert receipt["authorityState"] == "production_authoritative"
    assert receipt["authorityCriticalComplete"] is True
    assert receipt["toolchainImage"]["reference"] == "ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:" + "b" * 64
    assert receipt["toolchainImage"]["digest"] == "sha256:" + "b" * 64
    assert receipt["executionEnvironment"]["digest"] == "sha256:" + "d" * 64


def test_manifest_must_bind_to_exact_digest_pinned_base_image():
    value = json.loads(manifest_bytes())
    value["base_image"] = "ubuntu:24.04"
    with pytest.raises(ValueError, match="base image differs"):
        validate_toolchain_manifest(
            lock_bytes=LOCK_BYTES,
            manifest_bytes=(json.dumps(value) + "\n").encode(),
            orca_runtime_bytes=RUNTIME_BYTES,
        )


def test_runtime_orca_bytes_must_match_manifest():
    with pytest.raises(ValueError, match="Actual Orca runtime bytes"):
        validate_toolchain_manifest(
            lock_bytes=LOCK_BYTES,
            manifest_bytes=manifest_bytes(),
            orca_runtime_bytes=RUNTIME_BYTES + b"tamper",
        )


def test_orca_asset_sha_must_match_committed_lock():
    value = json.loads(manifest_bytes())
    value["orca_asset_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="orca_asset_sha256"):
        validate_toolchain_manifest(
            lock_bytes=LOCK_BYTES,
            manifest_bytes=(json.dumps(value) + "\n").encode(),
            orca_runtime_bytes=RUNTIME_BYTES,
        )


def test_final_execution_environment_must_be_digest_pinned():
    lock_bytes = published_lock_bytes()
    with pytest.raises(ValueError, match="Final service execution image"):
        build_toolchain_provenance(
            lock_bytes=lock_bytes,
            manifest_bytes=manifest_bytes(json.loads(lock_bytes)),
            orca_runtime_bytes=RUNTIME_BYTES,
            service_commit="c" * 40,
            runtime_image_ref="ghcr.io/workpiece-gr/open-slicer-service:latest",
        )


def test_published_lock_requires_real_digest():
    value = lock_value()
    value["status"] = "published"
    value["digest"] = None
    with pytest.raises(ValueError, match="published FDM toolchain lock requires"):
        validate_toolchain_lock((json.dumps(value) + "\n").encode(), require_published=True)


def test_unpublished_lock_rejects_unreviewed_digest():
    value = copy.deepcopy(lock_value())
    value["digest"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="must not carry an unreviewed image digest"):
        validate_toolchain_lock((json.dumps(value) + "\n").encode())
