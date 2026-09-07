"""Production policy wrapper around the reusable FDM Authority v2 pipeline.

Unlike the candidate wrapper, this module requires externally-controlled
production gates to be complete before the shared CP2->CP7 pipeline may return
technical production authority:

* CP5 must be backed by the reviewed published immutable toolchain lock.
* the final service runtime must exactly match a separately reviewed published
  service-image lock and source commit.
* the selected physical RatRig/profile combination must have an immutable,
  approved qualification receipt plus the exact retained physical-evidence bytes.

This module still does not approve an order, bypass human review, publish an
image, qualify a printer, deploy a service, or start fulfilment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fdm_authority import AUTHORITY_PRODUCTION, evaluate_fdm_authority
from .fdm_authority_pipeline import FdmAuthorityPipelineError, build_fdm_authority_pipeline
from .fdm_machine_qualification import FdmMachineQualificationError, validate_machine_qualification_receipt
from .fdm_service_image import validate_published_service_runtime
from .fdm_toolchain_provenance import build_toolchain_provenance

FDM_AUTHORITY_PRODUCTION_API_VERSION = "fdm-authority-production/1.0.0"
RATRIG_PRINTER_KEY = "ratrig_vcore3_300"


@dataclass(frozen=True)
class FdmAuthorityProductionResult:
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


def _request(generation_receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    value = generation_receipt.get("request")
    if not isinstance(value, Mapping):
        raise ValueError("Authority v2 generation receipt must retain the exact manufacturing request.")
    return value


def _verify_profile_bytes(generation_receipt: Mapping[str, Any], profile_bytes: Mapping[str, bytes]) -> dict[str, str]:
    receipts = generation_receipt.get("profiles")
    if not isinstance(receipts, Mapping):
        raise ValueError("Authority v2 generation receipt lacks exact profile receipts.")
    hashes: dict[str, str] = {}
    for kind in ("machine", "process", "filament"):
        payload = profile_bytes.get(kind)
        receipt = receipts.get(kind)
        if not isinstance(payload, bytes) or not payload:
            raise ValueError(f"Authority v2 requires exact non-empty {kind} profile bytes.")
        if not isinstance(receipt, Mapping):
            raise ValueError(f"Authority v2 generation receipt lacks the exact {kind} profile receipt.")
        expected = str(receipt.get("sha256") or "").strip().lower()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError(f"Exact {kind} profile bytes do not match the project generation receipt.")
        hashes[kind] = expected
    return hashes


def build_fdm_authority_production(
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
    service_lock_bytes: bytes,
    machine_qualification_evidence_id: str,
    toolchain_lock_bytes: bytes,
    toolchain_manifest_bytes: bytes,
    package_inventory_bytes: bytes,
    orca_runtime_bytes: bytes,
    base_env: Mapping[str, str],
    machine_qualification_receipt_bytes: bytes | None = None,
    machine_qualification_evidence_bytes: bytes | None = None,
) -> FdmAuthorityProductionResult:
    qualification_id = machine_qualification_evidence_id.strip() if isinstance(machine_qualification_evidence_id, str) else ""
    if not source_path.is_file() or source_path.stat().st_size < 1:
        raise ValueError("Production FDM authority requires the exact immutable source STL bytes.")
    if not project_path.is_file() or project_path.stat().st_size < 1:
        raise ValueError("Production FDM authority requires the exact retained production 3MF bytes.")
    if not isinstance(generation_receipt, Mapping):
        raise ValueError("Production FDM authority requires the exact project generation receipt.")

    printer = generation_receipt.get("printer")
    if not isinstance(printer, Mapping) or str(printer.get("key", "")) != RATRIG_PRINTER_KEY:
        raise ValueError("Production FDM authority currently supports the RatRig profile only.")
    if printer.get("temporary_generic") is not False:
        raise ValueError("Production FDM authority rejects temporary/generic printer profiles.")

    request = _request(generation_receipt)
    quantity = request.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("Authority v2 generation receipt has an invalid quantity.")
    material = str(request.get("material") or "").strip().lower()
    quality = str(request.get("quality") or "").strip().lower()
    strength = str(request.get("strength") or "").strip().lower()
    if not material or not quality or not strength:
        raise ValueError("Authority v2 generation receipt lacks material, quality, or strength.")

    profile_bytes = {
        "machine": machine_profile_bytes,
        "process": process_profile_bytes,
        "filament": filament_profile_bytes,
    }
    profile_hashes = _verify_profile_bytes(generation_receipt, profile_bytes)
    if not isinstance(machine_qualification_receipt_bytes, bytes) or not isinstance(machine_qualification_evidence_bytes, bytes):
        raise ValueError("Production FDM authority requires exact machine qualification receipt and physical-evidence bytes.")
    try:
        qualification = validate_machine_qualification_receipt(
            receipt_bytes=machine_qualification_receipt_bytes,
            evidence_bytes=machine_qualification_evidence_bytes,
            expected_printer_key=RATRIG_PRINTER_KEY,
            expected_request={"material": material, "quality": quality, "strength": strength},
            expected_profile_sha256=profile_hashes,
        )
    except FdmMachineQualificationError as exc:
        raise ValueError(str(exc)) from exc
    if qualification_id and qualification_id != qualification["qualificationId"]:
        raise ValueError("Configured machine qualification evidence id differs from the immutable qualification receipt.")
    qualification_id = qualification["qualificationId"]

    validate_published_service_runtime(
        service_lock_bytes=service_lock_bytes,
        toolchain_lock_bytes=toolchain_lock_bytes,
        runtime_image_ref=runtime_image_ref,
        service_commit=service_commit,
    )

    toolchain = build_toolchain_provenance(
        lock_bytes=toolchain_lock_bytes,
        manifest_bytes=toolchain_manifest_bytes,
        orca_runtime_bytes=orca_runtime_bytes,
        service_commit=service_commit,
        runtime_image_ref=runtime_image_ref,
        require_published=True,
    )
    if toolchain.get("authorityState") != AUTHORITY_PRODUCTION or toolchain.get("authorityCriticalComplete") is not True:
        raise ValueError("Production FDM authority requires production-authoritative CP5 provenance.")

    try:
        pipeline = build_fdm_authority_pipeline(
            orca_bin=orca_bin,
            source_bytes=source_path.read_bytes(),
            source_filename=source_path.name,
            project_path=project_path,
            generation_receipt=generation_receipt,
            profile_bytes=profile_bytes,
            toolchain_receipt=toolchain,
            toolchain_lock_bytes=toolchain_lock_bytes,
            toolchain_manifest_bytes=toolchain_manifest_bytes,
            package_inventory_bytes=package_inventory_bytes,
            output_dir=project_path.parent / "authority-v2-production-exact-gcode",
            material=material,
            quality=quality,
            strength=strength,
            quantity=quantity,
            printer_key=RATRIG_PRINTER_KEY,
            machine_production_ready=True,
            machine_qualification_evidence_id=qualification_id,
            validator_service_commit=service_commit,
            pricing_service_commit=service_commit,
            base_env=base_env,
            timeout_seconds=timeout_seconds,
            review_status="pending",
            require_production_authority=True,
            machine_qualification_receipt_bytes=machine_qualification_receipt_bytes,
            machine_qualification_evidence_bytes=machine_qualification_evidence_bytes,
        )
    except FdmAuthorityPipelineError as exc:
        raise ValueError(str(exc)) from exc

    evaluation = evaluate_fdm_authority(pipeline.manifest)
    issue_codes = tuple(sorted({issue.code for issue in evaluation.issues}))
    if evaluation.state != AUTHORITY_PRODUCTION or evaluation.production_authoritative is not True or issue_codes:
        raise ValueError(f"Production FDM authority did not close every technical authority issue: {list(issue_codes)}.")

    pricing = pipeline.pricing_receipt
    if pricing.get("priceAuthoritative") is not True or pricing.get("technicalProductionAuthority") is not True:
        raise ValueError("CP7 did not retain technical production authority in the authoritative pricing receipt.")
    if pricing.get("productionOrderEligible") is not False or pricing.get("productionEnablementPerformed") is not False:
        raise ValueError("FDM pricing must not bypass the separate human-review/order gate.")

    bundle = pipeline.bundle
    if bundle.authority_state != AUTHORITY_PRODUCTION:
        raise ValueError("CP6 production bundle did not retain production authority.")

    return FdmAuthorityProductionResult(
        manifest=pipeline.manifest,
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
