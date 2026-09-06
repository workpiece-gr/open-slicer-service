"""Disabled-by-default FDM Authority v2 candidate orchestration.

This module composes the already-validated CP2..CP7 stages around an exact
retained RatRig production project. It is deliberately candidate-only: it does
not publish CP5 images, qualify a physical printer, approve human review, or
grant production/fulfilment permission.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .fdm_authority import evaluate_fdm_authority
from .fdm_exact_gcode import execute_exact_project_gcode
from .fdm_instance_plate_evidence import build_instance_plate_evidence
from .fdm_pricing import price_exact_fdm_job
from .fdm_production_bundle import build_fdm_production_bundle
from .fdm_profile_driven_gcode import validate_profile_driven_gcode
from .fdm_toolchain_provenance import build_toolchain_provenance

FDM_AUTHORITY_CANDIDATE_API_VERSION = "fdm-authority-candidate/1.0.0"
RATRIG_PRINTER_KEY = "ratrig_vcore3_300"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _profile_hashes(generation_receipt: Mapping[str, Any]) -> dict[str, str]:
    return {
        kind: str(generation_receipt["profiles"][kind]["sha256"])
        for kind in ("machine", "process", "filament")
    }


@dataclass(frozen=True)
class FdmAuthorityCandidateResult:
    manifest: dict[str, Any]
    pricing: dict[str, Any]
    bundle_bytes: bytes
    bundle_filename: str
    bundle_sha256: str
    bundle_manifest_sha256: str
    bundle_index_sha256: str
    bundle_member_count: int
    authority_state: str
    authority_issues: tuple[str, ...]


def build_fdm_authority_candidate(
    *,
    source_path: Path,
    project_path: Path,
    machine_profile_bytes: bytes,
    process_profile_bytes: bytes,
    filament_profile_bytes: bytes,
    generation_receipt: Mapping[str, Any],
    orca_bin: Path,
    timeout_seconds: int,
    service_commit: str,
    runtime_image_ref: str,
    candidate_toolchain_image_ref: str,
    toolchain_lock_bytes: bytes,
    toolchain_manifest_bytes: bytes,
    package_inventory_bytes: bytes,
    orca_runtime_bytes: bytes,
    summarize_gcode: Callable[[Path], Mapping[str, Any]],
    base_env: Mapping[str, str],
) -> FdmAuthorityCandidateResult:
    """Build one exact Authority-v2 evidence candidate and authoritative price.

    The caller has already generated and retained ``project_path`` from the exact
    immutable source/configuration. This function starts at CP2 and composes the
    validated checkpoints without weakening any checkpoint's fail-closed rules.
    """

    if not source_path.is_file() or source_path.stat().st_size < 1:
        raise ValueError("Authority v2 requires the exact immutable source STL bytes.")
    if not project_path.is_file() or project_path.stat().st_size < 1:
        raise ValueError("Authority v2 requires the exact retained production 3MF bytes.")
    if str(generation_receipt.get("printer", {}).get("key", "")) != RATRIG_PRINTER_KEY:
        raise ValueError("Authority v2 candidate orchestration currently supports the RatRig profile only.")
    if generation_receipt.get("printer", {}).get("temporary_generic") is not False:
        raise ValueError("Authority v2 candidate orchestration rejects temporary/generic printer profiles.")

    profile_bytes = {
        "machine": machine_profile_bytes,
        "process": process_profile_bytes,
        "filament": filament_profile_bytes,
    }
    for kind, payload in profile_bytes.items():
        if not isinstance(payload, bytes) or not payload:
            raise ValueError(f"Authority v2 requires exact non-empty {kind} profile bytes.")
        expected = str(generation_receipt.get("profiles", {}).get(kind, {}).get("sha256", "")).lower()
        if _sha(payload) != expected:
            raise ValueError(f"Exact {kind} profile bytes do not match the project generation receipt.")

    toolchain = build_toolchain_provenance(
        lock_bytes=toolchain_lock_bytes,
        manifest_bytes=toolchain_manifest_bytes,
        orca_runtime_bytes=orca_runtime_bytes,
        service_commit=service_commit,
        runtime_image_ref=runtime_image_ref,
        require_published=False,
        candidate_toolchain_image_ref=candidate_toolchain_image_ref,
    )
    if toolchain["authorityState"] != "evidence_candidate" or toolchain["authorityCriticalComplete"] is not False:
        raise ValueError("The candidate API must not manufacture production-authoritative CP5 provenance.")
    runtime_ref = str(toolchain["executionEnvironment"]["reference"])

    exact_dir = project_path.parent / "authority-v2-exact-gcode"
    exact = execute_exact_project_gcode(
        orca_bin=orca_bin,
        project_path=project_path,
        output_dir=exact_dir,
        timeout_seconds=timeout_seconds,
        generation_receipt=generation_receipt,
        base_env=base_env,
    )
    if exact["generation_receipt"]["printer"]["key"] != RATRIG_PRINTER_KEY:
        raise ValueError("CP2 exact evidence does not retain the expected RatRig printer binding.")

    artifacts: dict[int, dict[str, Any]] = {}
    validations: dict[int, dict[str, Any]] = {}
    statistics: dict[int, Mapping[str, Any]] = {}
    for artifact in exact["plates"]:
        plate_id = int(artifact["plate_id"])
        validation = validate_profile_driven_gcode(
            artifact=artifact,
            generation_receipt=exact["generation_receipt"],
            machine_profile_bytes=machine_profile_bytes,
            process_profile_bytes=process_profile_bytes,
            filament_profile_bytes=filament_profile_bytes,
            validator_service_commit=service_commit,
            toolchain_ref=runtime_ref,
        )
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            raise ValueError(f"CP3 validation failed for physical plate {plate_id}.")
        summary = summarize_gcode(exact_dir / str(artifact["filename"]))
        if not summary.get("filament_grams") or not summary.get("print_time_seconds") or not summary.get("layer_count"):
            raise ValueError(f"Exact physical plate {plate_id} has incomplete authoritative statistics.")
        artifacts[plate_id] = artifact
        validations[plate_id] = validation
        statistics[plate_id] = summary

    project_bytes = project_path.read_bytes()
    source_bytes = source_path.read_bytes()
    cp4 = build_instance_plate_evidence(
        project_bytes=project_bytes,
        exact_gcode_result=exact,
        cp3_validations=validations,
        machine_profile_bytes=machine_profile_bytes,
    )

    request = generation_receipt.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("Authority v2 generation receipt must retain the exact manufacturing request.")
    quantity = request.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("Authority v2 generation receipt has an invalid quantity.")
    if cp4["totals"]["instanceCount"] != quantity:
        raise ValueError("CP4 exact instance evidence does not match the requested quantity.")

    profile_hashes = _profile_hashes(exact["generation_receipt"])
    manifest_instances = [
        {
            "id": item["id"],
            "objectId": item["objectId"],
            "plateId": item["plateId"],
            "transform": item["transform"],
            "boundsMm": item["boundsMm"],
        }
        for item in cp4["instances"]
    ]
    manifest_plates: list[dict[str, Any]] = []
    gcode_bytes_by_plate: dict[str, bytes] = {}
    total_filament = 0.0
    total_time = 0
    for cp4_plate in cp4["plates"]:
        numeric_id = int(cp4_plate["index"])
        artifact = artifacts[numeric_id]
        summary = statistics[numeric_id]
        total_filament += float(summary["filament_grams"])
        total_time += int(summary["print_time_seconds"])
        manifest_plates.append(
            {
                "id": cp4_plate["id"],
                "index": cp4_plate["index"],
                "instanceIds": cp4_plate["instanceIds"],
                "projectSha256": exact["project_sha256"],
                "profileSha256": dict(profile_hashes),
                "toolchainRef": runtime_ref,
                "gcode": {
                    "filename": artifact["filename"],
                    "bytes": artifact["byte_count"],
                    "sha256": artifact["sha256"],
                },
                "validation": validations[numeric_id],
                "statistics": {
                    "filamentGrams": float(summary["filament_grams"]),
                    "printTimeSeconds": int(summary["print_time_seconds"]),
                    "layerCount": int(summary["layer_count"]),
                    "filamentSource": "reported_from_hashed_gcode",
                    "printTimeSource": "reported_from_hashed_gcode",
                    "layerCountSource": "reported_from_hashed_gcode",
                },
            }
        )
        gcode_bytes_by_plate[str(cp4_plate["id"])] = bytes(artifact["bytes"])

    manifest = {
        "contractVersion": "fdm-production-manifest/2.0.0",
        "job": {
            "contractVersion": "fdm-job/2.0.0",
            "request": {
                "material": request.get("material"),
                "quality": request.get("quality"),
                "strength": request.get("strength"),
                "quantity": quantity,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        },
        "source": {
            "filename": source_path.name,
            "bytes": len(source_bytes),
            "sha256": _sha(source_bytes),
            "immutable": True,
        },
        "machine": {
            "key": RATRIG_PRINTER_KEY,
            "qualification": {
                "productionReady": False,
                "evidenceId": "authority-v2-candidate-no-physical-qualification",
            },
        },
        "profiles": exact["generation_receipt"]["profiles"],
        "toolchain": toolchain,
        "project": {
            "filename": "workpiece-production.3mf",
            "bytes": len(project_bytes),
            "sha256": _sha(project_bytes),
            "sourceSha256": _sha(source_bytes),
            "profileSha256": dict(profile_hashes),
            "toolchainRef": runtime_ref,
        },
        "instances": manifest_instances,
        "plates": manifest_plates,
        "instancePlateEvidence": cp4,
        "totals": {
            "plateCount": len(manifest_plates),
            "instanceCount": len(manifest_instances),
            "filamentGrams": total_filament,
            "printTimeSeconds": total_time,
        },
        "commercial": {"priceAuthoritative": False, "authority": "preview_only"},
        "review": {"required": True, "status": "pending"},
    }

    evaluation = evaluate_fdm_authority(manifest)
    issue_codes = tuple(sorted({issue.code for issue in evaluation.issues}))
    expected_candidate_issues = {"machine_not_production_ready", "missing_immutable_toolchain"}
    if evaluation.state != "evidence_candidate" or set(issue_codes) != expected_candidate_issues:
        raise ValueError(
            "Authority v2 candidate contains unexpected authority blockers; "
            f"issues={list(issue_codes)}."
        )

    bundle = build_fdm_production_bundle(
        manifest=manifest,
        source_bytes=source_bytes,
        project_bytes=project_bytes,
        profile_bytes=profile_bytes,
        gcode_bytes_by_plate=gcode_bytes_by_plate,
        toolchain_lock_bytes=toolchain_lock_bytes,
        toolchain_manifest_bytes=toolchain_manifest_bytes,
        package_inventory_bytes=package_inventory_bytes,
        require_production_authority=False,
    )
    pricing = price_exact_fdm_job(
        manifest=manifest,
        gcode_bytes_by_plate=gcode_bytes_by_plate,
        pricing_service_commit=service_commit,
    )
    if pricing.get("priceAuthoritative") is not True:
        raise ValueError("CP7 did not produce an authoritative server price.")
    if pricing.get("productionOrderEligible") is not False or pricing.get("productionEnablementPerformed") is not False:
        raise ValueError("The candidate API must never grant production permission.")

    return FdmAuthorityCandidateResult(
        manifest=manifest,
        pricing=pricing,
        bundle_bytes=bundle.bytes,
        bundle_filename=bundle.filename,
        bundle_sha256=bundle.sha256,
        bundle_manifest_sha256=bundle.manifest_sha256,
        bundle_index_sha256=bundle.index_sha256,
        bundle_member_count=bundle.member_count,
        authority_state=evaluation.state,
        authority_issues=issue_codes,
    )
