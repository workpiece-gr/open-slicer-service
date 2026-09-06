"""Immutable FDM toolchain provenance for Authority v2 CP5.

This module never builds, publishes, pulls, or deploys an image. It validates
retained lock/manifest/runtime bytes and constructs an authority receipt only
when both the manufacturing toolchain image and the final service execution
image are content-addressed by SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

FDM_TOOLCHAIN_LOCK_SCHEMA = "workpiece-fdm-toolchain-lock-v1"
FDM_TOOLCHAIN_MANIFEST_SCHEMA = "workpiece-fdm-toolchain-v1"
FDM_TOOLCHAIN_PROVENANCE_VERSION = "fdm-toolchain-provenance/1.0.0"

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DIGEST = re.compile(r"^sha256:([a-f0-9]{64})$")
_DIGEST_REF = re.compile(r"^(.+)@sha256:([a-f0-9]{64})$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_EXPECTED_PLATFORM = "linux/amd64"
_EXPECTED_BUILD_RECIPE = "Dockerfile.toolchain"
_EXPECTED_SERVICE_RECIPE = "Dockerfile.authority"


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256.fullmatch(text) else ""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _digest_ref(value: Any, label: str) -> tuple[str, str]:
    text = _text(value).lower()
    match = _DIGEST_REF.fullmatch(text)
    if not match:
        raise ValueError(f"{label} must be an immutable image/reference ending in @sha256:<64 hex>.")
    return text, f"sha256:{match.group(2)}"


def validate_toolchain_lock(lock_bytes: bytes, *, require_published: bool = False) -> dict[str, Any]:
    """Validate the committed FDM toolchain lock without contacting a registry."""

    lock = _json_object(lock_bytes, "FDM toolchain lock")
    if lock.get("schema") != FDM_TOOLCHAIN_LOCK_SCHEMA:
        raise ValueError(f"FDM toolchain lock must declare schema={FDM_TOOLCHAIN_LOCK_SCHEMA}.")
    status = _text(lock.get("status"))
    if status not in {"unpublished", "published"}:
        raise ValueError("FDM toolchain lock status must be unpublished or published.")
    if require_published and status != "published":
        raise ValueError("FDM authority requires a reviewed published toolchain digest; the current lock is unpublished.")

    image = _text(lock.get("image"))
    tag = _text(lock.get("tag"))
    if not image or not tag:
        raise ValueError("FDM toolchain lock requires an image repository and review tag.")
    if _text(lock.get("build_recipe")) != _EXPECTED_BUILD_RECIPE:
        raise ValueError(f"FDM toolchain lock must use {_EXPECTED_BUILD_RECIPE}.")
    if _text(lock.get("service_recipe")) != _EXPECTED_SERVICE_RECIPE:
        raise ValueError(f"FDM toolchain lock must use {_EXPECTED_SERVICE_RECIPE}.")
    if _text(lock.get("platform")) != _EXPECTED_PLATFORM:
        raise ValueError(f"FDM toolchain lock currently supports only {_EXPECTED_PLATFORM}.")

    base_ref = _text(_record(lock.get("base_image")).get("reference")).lower()
    _digest_ref(base_ref, "Base image reference")

    orca = _record(lock.get("orca"))
    if not _text(orca.get("version")) or not _text(orca.get("asset")) or not _text(orca.get("release_tag")):
        raise ValueError("FDM toolchain lock requires exact Orca version, release tag, and asset name.")
    if not _sha(orca.get("asset_sha256")):
        raise ValueError("FDM toolchain lock requires the exact upstream Orca asset SHA-256.")
    if not isinstance(orca.get("release_asset_id"), int) or isinstance(orca.get("release_asset_id"), bool) or orca["release_asset_id"] <= 0:
        raise ValueError("FDM toolchain lock requires the exact positive GitHub release asset id.")
    if _text(orca.get("runtime_path")) != "/opt/orca/squashfs-root/AppRun":
        raise ValueError("FDM toolchain lock must identify the exact Orca runtime path used by the service.")

    raw_digest = lock.get("digest")
    if status == "unpublished":
        if raw_digest not in {None, ""}:
            raise ValueError("An unpublished FDM toolchain lock must not carry an unreviewed image digest.")
    else:
        digest_text = _text(raw_digest).lower()
        if not _DIGEST.fullmatch(digest_text):
            raise ValueError("A published FDM toolchain lock requires digest=sha256:<64 hex>.")

    return lock


def validate_toolchain_manifest(
    *,
    lock_bytes: bytes,
    manifest_bytes: bytes,
    orca_runtime_bytes: bytes,
) -> dict[str, Any]:
    """Validate exact toolchain-manifest and runtime Orca bytes against the lock."""

    lock = validate_toolchain_lock(lock_bytes, require_published=False)
    manifest = _json_object(manifest_bytes, "FDM toolchain manifest")
    if manifest.get("schema") != FDM_TOOLCHAIN_MANIFEST_SCHEMA:
        raise ValueError(f"FDM toolchain manifest must declare schema={FDM_TOOLCHAIN_MANIFEST_SCHEMA}.")
    if _text(manifest.get("platform")) != _text(lock.get("platform")):
        raise ValueError("FDM toolchain manifest platform differs from the committed lock.")
    if _text(manifest.get("base_image")).lower() != _text(_record(lock.get("base_image")).get("reference")).lower():
        raise ValueError("FDM toolchain manifest base image differs from the committed digest-pinned lock.")

    orca = _record(lock.get("orca"))
    comparisons = (
        ("orca_version", "version"),
        ("orca_asset", "asset"),
        ("orca_asset_sha256", "asset_sha256"),
        ("orca_runtime_path", "runtime_path"),
    )
    for manifest_key, lock_key in comparisons:
        if _text(manifest.get(manifest_key)).lower() != _text(orca.get(lock_key)).lower():
            raise ValueError(f"FDM toolchain manifest {manifest_key} differs from the committed lock.")

    runtime_sha = _digest(orca_runtime_bytes)
    if not orca_runtime_bytes or _sha(manifest.get("orca_runtime_sha256")) != runtime_sha:
        raise ValueError("Actual Orca runtime bytes do not match the toolchain manifest SHA-256.")
    if not _sha(manifest.get("packages_sha256")):
        raise ValueError("FDM toolchain manifest must retain a SHA-256 of the installed package inventory.")
    return manifest


def build_toolchain_provenance(
    *,
    lock_bytes: bytes,
    manifest_bytes: bytes,
    orca_runtime_bytes: bytes,
    service_commit: str,
    runtime_image_ref: str,
    require_published: bool = True,
    candidate_toolchain_image_ref: str | None = None,
) -> dict[str, Any]:
    """Build a CP5 receipt from exact retained bytes and digest-pinned images.

    With require_published=True (the production-authority default), the committed
    lock must contain the reviewed published toolchain digest. CI may set
    require_published=False and provide a content-addressed local candidate image
    reference; that receipt remains evidence_candidate and cannot grant authority.
    """

    lock = validate_toolchain_lock(lock_bytes, require_published=require_published)
    manifest = validate_toolchain_manifest(
        lock_bytes=lock_bytes,
        manifest_bytes=manifest_bytes,
        orca_runtime_bytes=orca_runtime_bytes,
    )

    commit = _text(service_commit).lower()
    if not _COMMIT.fullmatch(commit):
        raise ValueError("CP5 provenance requires an exact 40-character service commit SHA.")
    runtime_ref, runtime_digest = _digest_ref(runtime_image_ref, "Final service execution image")

    image = _text(lock.get("image"))
    if require_published:
        toolchain_digest = _text(lock.get("digest")).lower()
        toolchain_ref = f"{image}@{toolchain_digest}".lower()
        _digest_ref(toolchain_ref, "Published manufacturing toolchain image")
        authoritative = True
    else:
        if not candidate_toolchain_image_ref:
            raise ValueError("Candidate CP5 evidence requires an explicit content-addressed toolchain image reference.")
        toolchain_ref, toolchain_digest = _digest_ref(candidate_toolchain_image_ref, "Candidate manufacturing toolchain image")
        authoritative = False

    runtime_sha = _digest(orca_runtime_bytes)
    receipt = {
        "contractVersion": FDM_TOOLCHAIN_PROVENANCE_VERSION,
        "authorityState": "production_authoritative" if authoritative else "evidence_candidate",
        "authorityCriticalComplete": authoritative,
        "serviceCommit": commit,
        "orcaVersion": _text(_record(lock.get("orca")).get("version")),
        "orcaBinarySha256": runtime_sha,
        "executionEnvironment": {"reference": runtime_ref, "digest": runtime_digest},
        "toolchainImage": {"reference": toolchain_ref, "digest": toolchain_digest},
        "toolchainManifestSha256": _digest(manifest_bytes),
        "baseImage": {"reference": _text(manifest.get("base_image")).lower()},
        "upstreamOrca": {
            "asset": _text(manifest.get("orca_asset")),
            "assetSha256": _sha(manifest.get("orca_asset_sha256")),
            "releaseAssetId": _record(lock.get("orca")).get("release_asset_id"),
        },
        "packageInventorySha256": _sha(manifest.get("packages_sha256")),
        "lockSchema": FDM_TOOLCHAIN_LOCK_SCHEMA,
        "lockStatus": _text(lock.get("status")),
        "lockSha256": _digest(lock_bytes),
    }
    return receipt
