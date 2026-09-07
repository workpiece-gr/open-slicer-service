"""Final FDM Authority service-image identity and publication-review helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .fdm_toolchain_provenance import validate_toolchain_lock

FDM_SERVICE_LOCK_SCHEMA = "workpiece-fdm-authority-service-lock-v1"
FDM_SERVICE_PUBLICATION_REVIEW_VERSION = "fdm-authority-service-publication-review/1.0.0"

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DIGEST = re.compile(r"^sha256:([a-f0-9]{64})$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_LOCAL_IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_EXPECTED_PLATFORM = "linux/amd64"
_EXPECTED_BUILD_RECIPE = "Dockerfile.authority"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha(value: Any, label: str) -> str:
    text = _text(value).lower()
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{label} must be exactly 64 lowercase SHA-256 hex characters.")
    return text


def _digest(value: Any, label: str) -> str:
    text = _text(value).lower()
    if not _DIGEST.fullmatch(text):
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>.")
    return text


def _commit(value: Any, label: str) -> str:
    text = _text(value).lower()
    if not _COMMIT.fullmatch(text):
        raise ValueError(f"{label} must be an exact 40-character commit SHA.")
    return text


def validate_service_lock(
    service_lock_bytes: bytes,
    *,
    toolchain_lock_bytes: bytes | None = None,
    require_published: bool = False,
) -> dict[str, Any]:
    lock = _json_object(service_lock_bytes, "FDM Authority service lock")
    if lock.get("schema") != FDM_SERVICE_LOCK_SCHEMA:
        raise ValueError(f"FDM Authority service lock must declare schema={FDM_SERVICE_LOCK_SCHEMA}.")

    status = _text(lock.get("status"))
    if status not in {"unpublished", "published"}:
        raise ValueError("FDM Authority service lock status must be unpublished or published.")
    if require_published and status != "published":
        raise ValueError("Production FDM authority requires a reviewed published final service image.")

    image = _text(lock.get("image")).lower()
    if not image or not image.startswith("ghcr.io/workpiece-gr/"):
        raise ValueError("FDM Authority service lock requires a Workpiece GHCR image repository.")
    if _text(lock.get("platform")) != _EXPECTED_PLATFORM:
        raise ValueError(f"FDM Authority service lock currently supports only {_EXPECTED_PLATFORM}.")
    if _text(lock.get("build_recipe")) != _EXPECTED_BUILD_RECIPE:
        raise ValueError(f"FDM Authority service lock must use {_EXPECTED_BUILD_RECIPE}.")

    toolchain = _record(lock.get("toolchain"))
    toolchain_image = _text(toolchain.get("image")).lower()
    toolchain_digest = _digest(toolchain.get("digest"), "Service-lock toolchain digest")
    if not toolchain_image:
        raise ValueError("FDM Authority service lock requires the exact published toolchain image repository.")

    if toolchain_lock_bytes is not None:
        published_toolchain = validate_toolchain_lock(toolchain_lock_bytes, require_published=True)
        if toolchain_image != _text(published_toolchain.get("image")).lower():
            raise ValueError("Service lock toolchain image differs from the committed published toolchain lock.")
        if toolchain_digest != _text(published_toolchain.get("digest")).lower():
            raise ValueError("Service lock toolchain digest differs from the committed published toolchain lock.")

    raw_digest = lock.get("digest")
    raw_source_commit = lock.get("source_commit")
    if status == "unpublished":
        if raw_digest not in {None, ""}:
            raise ValueError("An unpublished FDM Authority service lock must not contain a registry digest.")
        if raw_source_commit not in {None, ""}:
            raise ValueError("An unpublished FDM Authority service lock must not claim a published source commit.")
    else:
        _digest(raw_digest, "Published FDM Authority service digest")
        _commit(raw_source_commit, "Published FDM Authority service source commit")

    return lock


def validate_published_service_runtime(
    *,
    service_lock_bytes: bytes,
    toolchain_lock_bytes: bytes,
    runtime_image_ref: str,
    service_commit: str,
) -> dict[str, str]:
    lock = validate_service_lock(
        service_lock_bytes,
        toolchain_lock_bytes=toolchain_lock_bytes,
        require_published=True,
    )
    commit = _commit(service_commit, "Runtime service commit")
    published_commit = _commit(lock.get("source_commit"), "Published FDM Authority service source commit")
    if commit != published_commit:
        raise ValueError("Runtime service commit differs from the separately reviewed published service image.")

    digest = _digest(lock.get("digest"), "Published FDM Authority service digest")
    expected_ref = f"{_text(lock.get('image')).lower()}@{digest}"
    actual_ref = _text(runtime_image_ref).lower()
    if actual_ref != expected_ref:
        raise ValueError(
            "Runtime image reference must exactly match the separately reviewed published FDM Authority service image."
        )
    return {"reference": expected_ref, "digest": digest, "sourceCommit": published_commit}


def build_service_publication_review_packet(
    *,
    service_lock_bytes: bytes,
    toolchain_lock_bytes: bytes,
    service_recipe_bytes: bytes,
    requirements_bytes: bytes,
    app_tree_sha256: str,
    profiles_tree_sha256: str,
    service_commit: str,
    service_local_image_id: str,
    pip_freeze_bytes: bytes,
) -> dict[str, Any]:
    lock = validate_service_lock(
        service_lock_bytes,
        toolchain_lock_bytes=toolchain_lock_bytes,
        require_published=False,
    )
    if lock["status"] != "unpublished":
        raise ValueError("Service publication-review packets may only be created from an unpublished service lock.")
    if not service_recipe_bytes:
        raise ValueError("Service publication review requires exact non-empty Dockerfile.authority bytes.")
    if not requirements_bytes:
        raise ValueError("Service publication review requires exact non-empty requirements.txt bytes.")
    if not pip_freeze_bytes:
        raise ValueError("Service publication review requires the exact non-empty installed Python package inventory.")

    commit = _commit(service_commit, "Service publication-review source commit")
    local_image_id = _text(service_local_image_id).lower()
    if not _LOCAL_IMAGE_ID.fullmatch(local_image_id):
        raise ValueError("Service publication review requires a local Docker image id in sha256:<64 hex> form.")

    app_hash = _sha(app_tree_sha256, "Application tree SHA-256")
    profiles_hash = _sha(profiles_tree_sha256, "Profiles tree SHA-256")
    toolchain = _record(lock["toolchain"])
    review_tag = f"authority-v2-{commit[:12]}-amd64"

    return {
        "contractVersion": FDM_SERVICE_PUBLICATION_REVIEW_VERSION,
        "authorityState": "publication_review_candidate",
        "authorityCriticalComplete": False,
        "serviceCommit": commit,
        "reviewTarget": {
            "imageRepository": _text(lock["image"]).lower(),
            "reviewTag": review_tag,
            "tagReference": f"{_text(lock['image']).lower()}:{review_tag}",
            "platform": _text(lock["platform"]),
        },
        "toolchainBase": {
            "reference": f"{_text(toolchain.get('image')).lower()}@{_text(toolchain.get('digest')).lower()}",
            "image": _text(toolchain.get("image")).lower(),
            "digest": _text(toolchain.get("digest")).lower(),
        },
        "sourceInputs": {
            "serviceLockSha256": _payload_sha256(service_lock_bytes),
            "serviceRecipe": {"path": _text(lock["build_recipe"]), "sha256": _payload_sha256(service_recipe_bytes)},
            "requirements": {"path": "requirements.txt", "sha256": _payload_sha256(requirements_bytes)},
            "appTreeSha256": app_hash,
            "profilesTreeSha256": profiles_hash,
        },
        "retainedCandidateEvidence": {
            "serviceLocalImageId": local_image_id,
            "localImageIdIsRegistryDigest": False,
            "pipFreezeSha256": _payload_sha256(pip_freeze_bytes),
        },
        "publication": {
            "performed": False,
            "registryDigest": None,
            "lockStatus": lock["status"],
            "explicitApprovalRequired": True,
        },
        "production": {
            "productionAuthorityEligible": False,
            "productionEnablementPerformed": False,
            "deploymentPerformed": False,
            "machineQualificationPerformed": False,
            "humanReviewStillRequired": True,
        },
    }
