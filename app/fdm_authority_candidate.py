"""Candidate-only policy wrapper around the reusable FDM Authority v2 pipeline.

The shared pipeline owns CP2..CP7 retained-evidence orchestration. This module
adds only the controlled candidate policy used by the separate integration API:
RatRig-only routing, unpublished CP5 provenance, no physical qualification, and
an exact expected blocker set. It never grants production or fulfilment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fdm_authority import evaluate_fdm_authority
from .fdm_authority_pipeline import FdmAuthorityPipelineError, build_fdm_authority_pipeline
from .fdm_toolchain_provenance import build_toolchain_provenance

FDM_AUTHORITY_CANDIDATE_API_VERSION = "fdm-authority-candidate/1.0.0"
RATRIG_PRINTER_KEY = "ratrig_vcore3_300"
_EXPECTED_CANDIDATE_ISSUES = frozenset({"machine_not_production_ready", "missing_immutable_toolchain"})


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


def _request(generation_receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    value = generation_receipt.get("request")
    if not isinstance(value, Mapping):
        raise ValueError("Authority v2 generation receipt must retain the exact manufacturing request.")
    return value


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
    base_env: Mapping[str, str],
) -> FdmAuthorityCandidateResult:
    """Build one exact RatRig evidence candidate and authoritative item price."""

    if not source_path.is_file() or source_path.stat().st_size < 1:
        raise ValueError("Authority v2 requires the exact immutable source STL bytes.")
    if not project_path.is_file() or project_path.stat().st_size < 1:
        raise ValueError("Authority v2 requires the exact retained production 3MF bytes.")
    if not isinstance(generation_receipt, Mapping):
        raise ValueError("Authority v2 requires the exact project generation receipt.")

    printer = generation_receipt.get("printer")
    if not isinstance(printer, Mapping) or str(printer.get("key", "")) != RATRIG_PRINTER_KEY:
        raise ValueError("Authority v2 candidate orchestration currently supports the RatRig profile only.")
    if printer.get("temporary_generic") is not False:
        raise ValueError("Authority v2 candidate orchestration rejects temporary/generic printer profiles.")

    request = _request(generation_receipt)
    quantity = request.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("Authority v2 generation receipt has an invalid quantity.")
    material = str(request.get("material") or "").strip().lower()
    quality = str(request.get("quality") or "").strip().lower()
    strength = str(request.get("strength") or "").strip().lower()
    if not material or not quality or not strength:
        raise ValueError("Authority v2 generation receipt lacks material, quality, or strength.")

    toolchain = build_toolchain_provenance(
        lock_bytes=toolchain_lock_bytes,
        manifest_bytes=toolchain_manifest_bytes,
        orca_runtime_bytes=orca_runtime_bytes,
        service_commit=service_commit,
        runtime_image_ref=runtime_image_ref,
        require_published=False,
        candidate_toolchain_image_ref=candidate_toolchain_image_ref,
    )
    if toolchain.get("authorityState") != "evidence_candidate" or toolchain.get("authorityCriticalComplete") is not False:
        raise ValueError("The candidate API must not manufacture production-authoritative CP5 provenance.")

    try:
        pipeline = build_fdm_authority_pipeline(
            orca_bin=orca_bin,
            source_bytes=source_path.read_bytes(),
            source_filename=source_path.name,
            project_path=project_path,
            generation_receipt=generation_receipt,
            profile_bytes={
                "machine": machine_profile_bytes,
                "process": process_profile_bytes,
                "filament": filament_profile_bytes,
            },
            toolchain_receipt=toolchain,
            toolchain_lock_bytes=toolchain_lock_bytes,
            toolchain_manifest_bytes=toolchain_manifest_bytes,
            package_inventory_bytes=package_inventory_bytes,
            output_dir=project_path.parent / "authority-v2-exact-gcode",
            material=material,
            quality=quality,
            strength=strength,
            quantity=quantity,
            printer_key=RATRIG_PRINTER_KEY,
            machine_production_ready=False,
            machine_qualification_evidence_id="authority-v2-candidate-no-physical-qualification",
            validator_service_commit=service_commit,
            pricing_service_commit=service_commit,
            base_env=base_env,
            timeout_seconds=timeout_seconds,
            review_status="pending",
            require_production_authority=False,
        )
    except FdmAuthorityPipelineError as exc:
        raise ValueError(str(exc)) from exc

    evaluation = evaluate_fdm_authority(pipeline.manifest)
    issue_codes = tuple(sorted({issue.code for issue in evaluation.issues}))
    if evaluation.state != "evidence_candidate" or set(issue_codes) != _EXPECTED_CANDIDATE_ISSUES:
        raise ValueError(
            "Authority v2 candidate contains unexpected authority blockers; "
            f"issues={list(issue_codes)}."
        )

    pricing = pipeline.pricing_receipt
    if pricing.get("priceAuthoritative") is not True:
        raise ValueError("CP7 did not produce an authoritative server price.")
    if pricing.get("productionOrderEligible") is not False or pricing.get("productionEnablementPerformed") is not False:
        raise ValueError("The candidate API must never grant production permission.")

    bundle = pipeline.bundle
    return FdmAuthorityCandidateResult(
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
