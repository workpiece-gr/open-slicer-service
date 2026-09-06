"""Reusable retained-evidence orchestration for the FDM Authority v2 chain.

This module promotes the already-proven CP2 -> CP7 CI orchestration into
application code without exposing a network endpoint or changing the live
production path.  Callers must supply the exact retained source/project/profile
and CP5 toolchain evidence bytes.  The pipeline never invents machine
qualification or published-runtime state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fdm_authority import AUTHORITY_PRODUCTION, evaluate_fdm_authority
from .fdm_exact_gcode import execute_exact_project_gcode
from .fdm_instance_plate_evidence import build_instance_plate_evidence
from .fdm_pricing import price_exact_fdm_job
from .fdm_production_bundle import FdmProductionBundleResult, build_fdm_production_bundle
from .fdm_profile_driven_gcode import validate_profile_driven_gcode


_ALLOWED_CANDIDATE_ISSUES = frozenset({"machine_not_production_ready", "missing_immutable_toolchain"})
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class FdmAuthorityPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class FdmAuthorityPipelineResult:
    manifest: dict[str, Any]
    pricing_receipt: dict[str, Any]
    bundle: FdmProductionBundleResult
    exact_gcode_result: dict[str, Any]
    instance_plate_evidence: dict[str, Any]
    validations: dict[int, dict[str, Any]]


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _receipt_sha(value: Any, label: str) -> str:
    text = _text(value).lower()
    if not _SHA256_RE.fullmatch(text):
        raise FdmAuthorityPipelineError(f"{label} is not a valid SHA-256 receipt.")
    return text


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FdmAuthorityPipelineError(f"{label} must be a positive integer.")
    return value


def _parse_duration_seconds(value: str) -> int | None:
    match = re.search(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?", value.strip(), re.I)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(item or 0) for item in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_exact_gcode_statistics(gcode_bytes: bytes) -> dict[str, int | float]:
    """Extract Orca-reported production statistics from the exact retained bytes."""

    if not isinstance(gcode_bytes, bytes) or not gcode_bytes:
        raise FdmAuthorityPipelineError("Exact G-code statistics require non-empty bytes.")
    try:
        text = gcode_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FdmAuthorityPipelineError("Exact G-code must be UTF-8 for statistics extraction.") from exc

    print_time_seconds: int | None = None
    filament_grams: float | None = None
    layer_count: int | None = None
    time_patterns = (
        re.compile(r"estimated printing time.*?=\s*(.+)$", re.I),
        re.compile(r"(?:model|total) printing time\s*:?\s*(.+)$", re.I),
    )
    filament_pattern = re.compile(r"(?:total )?filament used \[g\]\s*=\s*([\d.]+)", re.I)
    layer_pattern = re.compile(r"(?:total layer number|total_layer_count)\s*[:=]\s*(\d+)", re.I)

    for line in text.splitlines():
        for pattern in time_patterns:
            match = pattern.search(line)
            if match:
                parsed = _parse_duration_seconds(match.group(1))
                if parsed is not None:
                    print_time_seconds = parsed
        match = filament_pattern.search(line)
        if match:
            filament_grams = float(match.group(1))
        match = layer_pattern.search(line)
        if match:
            layer_count = int(match.group(1))

    if not print_time_seconds or print_time_seconds < 1:
        raise FdmAuthorityPipelineError("Exact G-code lacks a positive Orca print-time statistic.")
    if filament_grams is None or not (filament_grams > 0):
        raise FdmAuthorityPipelineError("Exact G-code lacks a positive Orca filament-mass statistic.")
    if not layer_count or layer_count < 1:
        raise FdmAuthorityPipelineError("Exact G-code lacks a positive Orca layer-count statistic.")
    return {
        "print_time_seconds": print_time_seconds,
        "filament_grams": filament_grams,
        "layer_count": layer_count,
    }


def _verify_profile_bytes(generation_receipt: Mapping[str, Any], profile_bytes: Mapping[str, bytes]) -> dict[str, str]:
    profiles = _record(generation_receipt.get("profiles"))
    hashes: dict[str, str] = {}
    for kind in ("machine", "process", "filament"):
        payload = profile_bytes.get(kind)
        if not isinstance(payload, bytes) or not payload:
            raise FdmAuthorityPipelineError(f"Exact {kind} profile bytes are required.")
        receipt = _record(profiles.get(kind))
        expected = _receipt_sha(receipt.get("sha256"), f"Generation {kind} profile SHA-256")
        actual = _sha(payload)
        if actual != expected:
            raise FdmAuthorityPipelineError(f"Exact {kind} profile bytes do not match the generation receipt.")
        hashes[kind] = actual
    return hashes


def build_fdm_authority_pipeline(
    *,
    orca_bin: Path,
    source_bytes: bytes,
    source_filename: str,
    project_path: Path,
    generation_receipt: Mapping[str, Any],
    profile_bytes: Mapping[str, bytes],
    toolchain_receipt: Mapping[str, Any],
    toolchain_lock_bytes: bytes,
    toolchain_manifest_bytes: bytes,
    package_inventory_bytes: bytes,
    output_dir: Path,
    material: str,
    quality: str,
    strength: str,
    quantity: int,
    printer_key: str,
    machine_production_ready: bool,
    machine_qualification_evidence_id: str,
    validator_service_commit: str,
    pricing_service_commit: str,
    base_env: Mapping[str, str] | None = None,
    timeout_seconds: int = 300,
    review_status: str = "pending",
    require_production_authority: bool = False,
) -> FdmAuthorityPipelineResult:
    """Build exact CP2-CP7 evidence from an already-retained production 3MF.

    The function deliberately starts from an existing exact 3MF.  Project
    generation/orientation remains a separate upstream operation so this module
    cannot accidentally change the 3MF while validating or pricing it.
    """

    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise FdmAuthorityPipelineError("The immutable source must contain exact non-empty bytes.")
    if not source_filename or Path(source_filename).name != source_filename:
        raise FdmAuthorityPipelineError("The immutable source filename must be a simple retained filename.")
    if not project_path.is_file() or project_path.stat().st_size < 1:
        raise FdmAuthorityPipelineError("The exact retained production 3MF is missing or empty.")
    if not isinstance(generation_receipt, Mapping) or not isinstance(toolchain_receipt, Mapping):
        raise FdmAuthorityPipelineError("Generation and CP5 toolchain receipts are required.")
    quantity = _positive_int(quantity, "FDM quantity")
    material = _text(material).lower()
    quality = _text(quality).lower()
    strength = _text(strength).lower()
    printer_key = _text(printer_key)
    if not material or not quality or not strength or not printer_key:
        raise FdmAuthorityPipelineError("Material, quality, strength, and printer key are required.")
    if not isinstance(machine_production_ready, bool):
        raise FdmAuthorityPipelineError("Machine production-ready state must be explicit boolean evidence.")
    qualification_evidence = _text(machine_qualification_evidence_id)
    if not qualification_evidence:
        raise FdmAuthorityPipelineError("Machine qualification state requires an explicit evidence id, including non-production-ready evidence.")
    if not isinstance(require_production_authority, bool):
        raise FdmAuthorityPipelineError("Production-authority requirement must be an explicit boolean.")
    if not _text(review_status):
        raise FdmAuthorityPipelineError("Human review status must be explicit.")

    generation_source = _record(generation_receipt.get("source"))
    expected_source_sha = _receipt_sha(generation_source.get("sha256"), "Generation source SHA-256")
    source_sha = _sha(source_bytes)
    if source_sha != expected_source_sha:
        raise FdmAuthorityPipelineError("Immutable source bytes do not match the generation receipt.")
    generation_printer = _record(generation_receipt.get("printer"))
    if _text(generation_printer.get("key")) != printer_key:
        raise FdmAuthorityPipelineError("Requested printer differs from the retained generation receipt.")

    normalized_profiles = {kind: bytes(profile_bytes[kind]) for kind in ("machine", "process", "filament")}
    profile_hashes = _verify_profile_bytes(generation_receipt, normalized_profiles)
    runtime_ref = _text(_record(toolchain_receipt.get("executionEnvironment")).get("reference"))
    if not runtime_ref:
        raise FdmAuthorityPipelineError("The CP5 toolchain receipt lacks an execution-environment reference.")

    exact = execute_exact_project_gcode(
        orca_bin=orca_bin,
        project_path=project_path,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        generation_receipt=dict(generation_receipt),
        base_env=dict(base_env) if base_env is not None else None,
    )
    exact_plates = exact.get("plates")
    if not isinstance(exact_plates, list) or not exact_plates:
        raise FdmAuthorityPipelineError("CP2 did not retain any exact physical-plate G-code artifacts.")

    artifacts: dict[int, dict[str, Any]] = {}
    validations: dict[int, dict[str, Any]] = {}
    statistics: dict[int, dict[str, int | float]] = {}
    machine_bytes = normalized_profiles["machine"]
    process_bytes = normalized_profiles["process"]
    filament_bytes = normalized_profiles["filament"]

    for raw_artifact in exact_plates:
        if not isinstance(raw_artifact, Mapping):
            raise FdmAuthorityPipelineError("CP2 returned a malformed plate artifact.")
        artifact = dict(raw_artifact)
        try:
            plate_id = int(artifact.get("plate_id"))
        except (TypeError, ValueError) as exc:
            raise FdmAuthorityPipelineError("CP2 plate id is not an integer physical-plate id.") from exc
        if plate_id < 1 or plate_id in artifacts:
            raise FdmAuthorityPipelineError("CP2 physical plate ids must be positive and unique.")
        validation = validate_profile_driven_gcode(
            artifact=artifact,
            generation_receipt=exact["generation_receipt"],
            machine_profile_bytes=machine_bytes,
            process_profile_bytes=process_bytes,
            filament_profile_bytes=filament_bytes,
            validator_service_commit=validator_service_commit,
            toolchain_ref=runtime_ref,
        )
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            raise FdmAuthorityPipelineError(f"CP3 validation failed or remained incomplete for plate {plate_id}.")
        payload = artifact.get("bytes")
        if not isinstance(payload, bytes) or not payload:
            raise FdmAuthorityPipelineError(f"CP2 plate {plate_id} lacks exact retained G-code bytes.")
        artifacts[plate_id] = artifact
        validations[plate_id] = validation
        statistics[plate_id] = parse_exact_gcode_statistics(payload)

    project_bytes = project_path.read_bytes()
    cp4 = build_instance_plate_evidence(
        project_bytes=project_bytes,
        exact_gcode_result=exact,
        cp3_validations=validations,
        machine_profile_bytes=machine_bytes,
    )
    cp4_totals = _record(cp4.get("totals"))
    if cp4_totals.get("instanceCount") != quantity:
        raise FdmAuthorityPipelineError("CP4 instance count differs from the requested quantity.")
    if cp4_totals.get("plateCount") != len(exact_plates):
        raise FdmAuthorityPipelineError("CP4 plate count differs from the exact CP2 plate set.")

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
        index = _positive_int(cp4_plate.get("index"), "CP4 plate index")
        artifact = artifacts.get(index)
        validation = validations.get(index)
        summary = statistics.get(index)
        if artifact is None or validation is None or summary is None:
            raise FdmAuthorityPipelineError(f"CP4 plate {index} is not backed by one exact CP2/CP3 artifact.")
        plate_id = _text(cp4_plate.get("id"))
        if not plate_id or plate_id in gcode_bytes_by_plate:
            raise FdmAuthorityPipelineError("CP4 physical plate ids must be non-empty and unique.")
        payload = bytes(artifact["bytes"])
        total_filament += float(summary["filament_grams"])
        total_time += int(summary["print_time_seconds"])
        manifest_plates.append(
            {
                "id": plate_id,
                "index": index,
                "instanceIds": list(cp4_plate["instanceIds"]),
                "projectSha256": exact["project_sha256"],
                "profileSha256": dict(profile_hashes),
                "toolchainRef": runtime_ref,
                "gcode": {
                    "filename": artifact["filename"],
                    "bytes": artifact["byte_count"],
                    "sha256": artifact["sha256"],
                },
                "validation": validation,
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
        gcode_bytes_by_plate[plate_id] = payload

    manifest: dict[str, Any] = {
        "contractVersion": "fdm-production-manifest/2.0.0",
        "job": {
            "contractVersion": "fdm-job/2.0.0",
            "request": {
                "material": material,
                "quality": quality,
                "strength": strength,
                "quantity": quantity,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        },
        "source": {
            "filename": source_filename,
            "bytes": len(source_bytes),
            "sha256": source_sha,
            "immutable": True,
        },
        "machine": {
            "key": printer_key,
            "qualification": {
                "productionReady": machine_production_ready,
                "evidenceId": qualification_evidence,
            },
        },
        "profiles": dict(exact["generation_receipt"]["profiles"]),
        "toolchain": dict(toolchain_receipt),
        "project": {
            "filename": project_path.name,
            "bytes": len(project_bytes),
            "sha256": _sha(project_bytes),
            "sourceSha256": source_sha,
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
        "review": {"required": True, "status": _text(review_status)},
    }

    evaluation = evaluate_fdm_authority(manifest)
    issue_codes = {issue.code for issue in evaluation.issues}
    unexpected = issue_codes - _ALLOWED_CANDIDATE_ISSUES
    if unexpected:
        raise FdmAuthorityPipelineError(f"Authority pipeline retained unresolved technical failures: {sorted(unexpected)}.")
    if require_production_authority and evaluation.state != AUTHORITY_PRODUCTION:
        raise FdmAuthorityPipelineError(
            f"Production authority was required but remains blocked by {sorted(issue_codes)}."
        )

    bundle = build_fdm_production_bundle(
        manifest=manifest,
        source_bytes=source_bytes,
        project_bytes=project_bytes,
        profile_bytes=normalized_profiles,
        gcode_bytes_by_plate=gcode_bytes_by_plate,
        toolchain_lock_bytes=toolchain_lock_bytes,
        toolchain_manifest_bytes=toolchain_manifest_bytes,
        package_inventory_bytes=package_inventory_bytes,
        require_production_authority=require_production_authority,
    )
    pricing_receipt = price_exact_fdm_job(
        manifest=manifest,
        gcode_bytes_by_plate=gcode_bytes_by_plate,
        pricing_service_commit=pricing_service_commit,
    )

    return FdmAuthorityPipelineResult(
        manifest=manifest,
        pricing_receipt=pricing_receipt,
        bundle=bundle,
        exact_gcode_result=exact,
        instance_plate_evidence=cp4,
        validations=validations,
    )
