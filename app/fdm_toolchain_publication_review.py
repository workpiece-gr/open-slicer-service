"""Non-publishing CP5 publication-review evidence for FDM Authority v2.

This module deliberately cannot publish, pull, deploy, or authorize an image.
It turns an unpublished reviewed toolchain lock plus exact local candidate-build
bytes into a deterministic handoff packet that can be inspected before any
explicit registry publication is approved.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.fdm_toolchain_provenance import validate_toolchain_lock, validate_toolchain_manifest

FDM_TOOLCHAIN_PUBLICATION_REVIEW_VERSION = "fdm-toolchain-publication-review/1.0.0"

_SHA256_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _local_image_id(value: str, label: str) -> str:
    text = value.strip().lower()
    if not _SHA256_DIGEST.fullmatch(text):
        raise ValueError(f"{label} must be a local Docker image id in sha256:<64 hex> form.")
    return text


def _commit(value: str) -> str:
    text = value.strip().lower()
    if not _COMMIT.fullmatch(text):
        raise ValueError("Publication review requires an exact 40-character service commit SHA.")
    return text


def build_toolchain_publication_review_packet(
    *,
    lock_bytes: bytes,
    manifest_bytes: bytes,
    package_inventory_bytes: bytes,
    orca_runtime_bytes: bytes,
    toolchain_recipe_bytes: bytes,
    service_recipe_bytes: bytes,
    service_commit: str,
    toolchain_local_image_id: str,
    service_local_image_id: str,
) -> dict[str, Any]:
    """Build deterministic pre-publication evidence from exact candidate inputs.

    Local Docker image IDs are retained only as candidate-build evidence. They
    are explicitly *not* represented as registry manifest digests and therefore
    cannot satisfy CP5 production authority.
    """

    lock = validate_toolchain_lock(lock_bytes, require_published=False)
    if lock["status"] != "unpublished":
        raise ValueError("Publication review packets may only be created from the unpublished CP5 lock.")

    if not toolchain_recipe_bytes:
        raise ValueError("Publication review requires the exact non-empty toolchain recipe bytes.")
    if not service_recipe_bytes:
        raise ValueError("Publication review requires the exact non-empty service recipe bytes.")
    if not package_inventory_bytes:
        raise ValueError("Publication review requires the exact non-empty installed package inventory bytes.")

    manifest = validate_toolchain_manifest(
        lock_bytes=lock_bytes,
        manifest_bytes=manifest_bytes,
        orca_runtime_bytes=orca_runtime_bytes,
    )
    package_inventory_sha256 = _digest(package_inventory_bytes)
    if manifest.get("packages_sha256") != package_inventory_sha256:
        raise ValueError("Exact installed package inventory bytes do not match the toolchain manifest SHA-256.")

    commit = _commit(service_commit)
    toolchain_id = _local_image_id(toolchain_local_image_id, "Toolchain candidate image")
    service_id = _local_image_id(service_local_image_id, "Service candidate image")

    image = str(lock["image"]).strip()
    tag = str(lock["tag"]).strip()
    platform = str(lock["platform"]).strip()
    orca = lock["orca"]

    return {
        "contractVersion": FDM_TOOLCHAIN_PUBLICATION_REVIEW_VERSION,
        "authorityState": "publication_review_candidate",
        "authorityCriticalComplete": False,
        "serviceCommit": commit,
        "reviewTarget": {
            "imageRepository": image,
            "reviewTag": tag,
            "tagReference": f"{image}:{tag}",
            "platform": platform,
        },
        "sourceInputs": {
            "toolchainLockSha256": _digest(lock_bytes),
            "toolchainRecipe": {
                "path": str(lock["build_recipe"]),
                "sha256": _digest(toolchain_recipe_bytes),
            },
            "serviceRecipe": {
                "path": str(lock["service_recipe"]),
                "sha256": _digest(service_recipe_bytes),
            },
        },
        "pinnedUpstream": {
            "baseImageReference": str(lock["base_image"]["reference"]).strip().lower(),
            "orcaVersion": str(orca["version"]).strip(),
            "orcaAsset": str(orca["asset"]).strip(),
            "orcaAssetSha256": str(orca["asset_sha256"]).strip().lower(),
            "orcaReleaseTag": str(orca["release_tag"]).strip(),
            "orcaReleaseAssetId": orca["release_asset_id"],
        },
        "retainedCandidateEvidence": {
            "toolchainManifestSha256": _digest(manifest_bytes),
            "packageInventorySha256": package_inventory_sha256,
            "orcaRuntimeSha256": _digest(orca_runtime_bytes),
            "toolchainLocalImageId": toolchain_id,
            "serviceLocalImageId": service_id,
            "localImageIdsAreRegistryDigests": False,
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
        },
        "remainingAuthorityRequirements": [
            "explicitly approve publication of the reviewed toolchain candidate",
            "record and review the exact published registry sha256 digest in the committed lock",
            "build and supply the exact final service execution image by digest",
            "complete genuine RatRig physical-machine qualification and retain its exact evidence",
            "keep human workshop review mandatory",
        ],
    }
