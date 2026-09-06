"""Fail-closed contract evaluator for the FDM Authority v2 evidence chain.

The evaluator computes technical manufacturing authority from retained evidence.
It never calls OrcaSlicer and it does not change HTTP/runtime behaviour.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION

FDM_JOB_CONTRACT_VERSION = "fdm-job/2.0.0"
FDM_PRODUCTION_MANIFEST_VERSION = "fdm-production-manifest/2.0.0"
FDM_GCODE_VALIDATION_VERSION = "fdm-gcode-validation/1.0.0"
AUTHORITY_EVIDENCE_CANDIDATE = "evidence_candidate"
AUTHORITY_PRODUCTION = "production_authoritative"

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_DIGEST = re.compile(r"^sha256:([a-f0-9]{64})$")
_DIGEST_REF = re.compile(r"^.+@sha256:([a-f0-9]{64})$")
_PROFILE_KINDS = ("machine", "process", "filament")


@dataclass(frozen=True)
class AuthorityIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class AuthorityEvaluation:
    state: str
    production_authoritative: bool
    manifest_version: str | None
    job_contract_version: str | None
    issues: tuple[AuthorityIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "productionAuthoritative": self.production_authoritative,
            "manifestVersion": self.manifest_version,
            "jobContractVersion": self.job_contract_version,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256.fullmatch(text) else ""


def _pos_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _pos_num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _issue(issues: list[AuthorityIssue], code: str, path: str, message: str) -> None:
    issues.append(AuthorityIssue(code, path, message))


def _profile_receipts(profiles: Mapping[str, Any], issues: list[AuthorityIssue]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for kind in _PROFILE_KINDS:
        receipt = _record(profiles.get(kind))
        digest = _sha(receipt.get("sha256"))
        if not _text(receipt.get("identity")) or not digest:
            _issue(issues, "missing_profile_receipt", f"profiles.{kind}", f"Exact {kind} profile identity and SHA-256 are required.")
        else:
            hashes[kind] = digest
    return hashes


def _profile_links(value: Any, expected: Mapping[str, str], issues: list[AuthorityIssue], path: str, code: str) -> None:
    links = _record(value)
    for kind in _PROFILE_KINDS:
        if not expected.get(kind) or _sha(links.get(kind)) != expected[kind]:
            _issue(issues, code, f"{path}.{kind}", f"{kind} profile hash does not match the retained receipt.")


def _valid_transform(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 12 and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value
    )


def _valid_bounds(value: Any) -> bool:
    bounds = _record(value)
    low, high = bounds.get("min"), bounds.get("max")
    if not isinstance(low, list) or not isinstance(high, list) or len(low) != 3 or len(high) != 3:
        return False
    return all(
        not isinstance(a, bool)
        and not isinstance(b, bool)
        and isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and math.isfinite(float(a))
        and math.isfinite(float(b))
        and float(a) <= float(b)
        for a, b in zip(low, high, strict=True)
    )


def _same_transform(left: Any, right: Any) -> bool:
    if not _valid_transform(left) or not _valid_transform(right):
        return False
    return all(math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9) for a, b in zip(left, right, strict=True))


def _same_bounds(left: Any, right: Any) -> bool:
    if not _valid_bounds(left) or not _valid_bounds(right):
        return False
    left_record, right_record = _record(left), _record(right)
    return all(
        math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)
        for key in ("min", "max")
        for a, b in zip(left_record[key], right_record[key], strict=True)
    )


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result = [_text(item) for item in value]
    return result if all(result) else None


def _evaluate_instance_plate_evidence(
    *,
    manifest: Mapping[str, Any],
    source_sha: str,
    machine_key: str,
    project_sha: str,
    profile_hashes: Mapping[str, str],
    instances: Mapping[str, Mapping[str, Any]],
    plates: Mapping[str, Mapping[str, Any]],
    issues: list[AuthorityIssue],
) -> None:
    evidence = _record(manifest.get("instancePlateEvidence"))
    path = "instancePlateEvidence"
    if evidence.get("contractVersion") != INSTANCE_PLATE_EVIDENCE_VERSION:
        _issue(
            issues,
            "missing_instance_plate_evidence",
            f"{path}.contractVersion",
            f"Exact CP4 instance/plate evidence with contract {INSTANCE_PLATE_EVIDENCE_VERSION} is required.",
        )
        return
    if evidence.get("authorityState") != AUTHORITY_EVIDENCE_CANDIDATE:
        _issue(issues, "invalid_instance_plate_evidence_state", f"{path}.authorityState", "CP4 evidence must remain evidence_candidate and cannot self-grant authority.")
    if not project_sha or _sha(evidence.get("projectSha256")) != project_sha:
        _issue(issues, "instance_plate_evidence_project_mismatch", f"{path}.projectSha256", "CP4 evidence must bind to the exact production 3MF.")
    if not source_sha or _sha(evidence.get("sourceSha256")) != source_sha:
        _issue(issues, "instance_plate_evidence_source_mismatch", f"{path}.sourceSha256", "CP4 evidence must bind to the immutable source.")
    if not machine_key or _text(evidence.get("printerKey")) != machine_key:
        _issue(issues, "instance_plate_evidence_machine_mismatch", f"{path}.printerKey", "CP4 evidence must bind to the selected machine.")
    _profile_links(evidence.get("profileSha256"), profile_hashes, issues, f"{path}.profileSha256", "instance_plate_evidence_profile_mismatch")

    evidence_instance_items = evidence.get("instances") if isinstance(evidence.get("instances"), list) else []
    evidence_instances: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(evidence_instance_items):
        item = _record(raw)
        instance_id = _text(item.get("id"))
        if not instance_id or instance_id in evidence_instances:
            _issue(issues, "invalid_instance_plate_evidence_instance", f"{path}.instances[{index}]", "CP4 instance evidence requires unique stable instance ids.")
            continue
        evidence_instances[instance_id] = item

    if set(evidence_instances) != set(instances):
        _issue(issues, "instance_plate_evidence_instance_set_mismatch", f"{path}.instances", "CP4 instance ids must exactly match manifest physical instances.")

    evidence_plate_items = evidence.get("plates") if isinstance(evidence.get("plates"), list) else []
    evidence_plates: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(evidence_plate_items):
        item = _record(raw)
        plate_id = _text(item.get("id"))
        if not plate_id or plate_id in evidence_plates:
            _issue(issues, "invalid_instance_plate_evidence_plate", f"{path}.plates[{index}]", "CP4 plate evidence requires unique stable plate ids.")
            continue
        evidence_plates[plate_id] = item

    if set(evidence_plates) != set(plates):
        _issue(issues, "instance_plate_evidence_plate_set_mismatch", f"{path}.plates", "CP4 plate ids must exactly match manifest physical plates.")

    for instance_id, manifest_instance in instances.items():
        receipt = evidence_instances.get(instance_id)
        if receipt is None:
            continue
        receipt_path = f"{path}.instances.{instance_id}"
        if _text(receipt.get("objectId")) != _text(manifest_instance.get("objectId")):
            _issue(issues, "instance_plate_evidence_instance_mismatch", f"{receipt_path}.objectId", "CP4 objectId must match the manifest instance.")
        if _text(receipt.get("plateId")) != _text(manifest_instance.get("plateId")):
            _issue(issues, "instance_plate_evidence_instance_mismatch", f"{receipt_path}.plateId", "CP4 plateId must match the manifest instance.")
        if not _same_transform(receipt.get("transform"), manifest_instance.get("transform")):
            _issue(issues, "instance_plate_evidence_transform_mismatch", f"{receipt_path}.transform", "CP4 exact 3MF transform must match the manifest instance.")
        if not _same_bounds(receipt.get("boundsMm"), manifest_instance.get("boundsMm")):
            _issue(issues, "instance_plate_evidence_bounds_mismatch", f"{receipt_path}.boundsMm", "CP4 physical bounds must match the manifest instance.")

        gcode_evidence = _record(receipt.get("gcodeEvidence"))
        object_name = _text(gcode_evidence.get("objectName"))
        extrusion_count = _pos_int(gcode_evidence.get("extrusionSegmentCount"))
        extrusion_bounds = gcode_evidence.get("extrusionBoundsMm")
        gcode_sha = _sha(gcode_evidence.get("gcodeSha256"))
        if not object_name or extrusion_count is None or not _valid_bounds(extrusion_bounds) or not gcode_sha:
            _issue(
                issues,
                "instance_gcode_evidence_incomplete",
                f"{receipt_path}.gcodeEvidence",
                "Every CP4 instance requires one matched G-code object, positive extrusion, finite extrusion bounds, and exact G-code SHA-256.",
            )
        manifest_plate = plates.get(_text(manifest_instance.get("plateId")))
        manifest_gcode_sha = _sha(_record(manifest_plate.get("gcode") if manifest_plate else {}).get("sha256"))
        if not manifest_gcode_sha or gcode_sha != manifest_gcode_sha:
            _issue(issues, "instance_gcode_evidence_mismatch", f"{receipt_path}.gcodeEvidence.gcodeSha256", "CP4 instance G-code evidence must bind to its exact manifest plate G-code.")

    for plate_id, manifest_plate in plates.items():
        receipt = evidence_plates.get(plate_id)
        if receipt is None:
            continue
        receipt_path = f"{path}.plates.{plate_id}"
        manifest_index = _pos_int(manifest_plate.get("index"))
        if _pos_int(receipt.get("index")) != manifest_index:
            _issue(issues, "instance_plate_evidence_plate_mismatch", f"{receipt_path}.index", "CP4 plate index must match the manifest plate.")
        if not project_sha or _sha(receipt.get("projectSha256")) != project_sha:
            _issue(issues, "instance_plate_evidence_plate_mismatch", f"{receipt_path}.projectSha256", "CP4 plate must bind to the exact production 3MF.")
        manifest_gcode_sha = _sha(_record(manifest_plate.get("gcode")).get("sha256"))
        if not manifest_gcode_sha or _sha(receipt.get("gcodeSha256")) != manifest_gcode_sha:
            _issue(issues, "instance_plate_evidence_plate_gcode_mismatch", f"{receipt_path}.gcodeSha256", "CP4 plate must bind to the exact retained manifest G-code.")
        if not manifest_gcode_sha or _sha(receipt.get("validationGcodeSha256")) != manifest_gcode_sha:
            _issue(issues, "instance_plate_evidence_validation_mismatch", f"{receipt_path}.validationGcodeSha256", "CP4 plate must bind to the CP3 validation of the same exact G-code.")
        receipt_members = _string_list(receipt.get("instanceIds"))
        manifest_members = _string_list(manifest_plate.get("instanceIds"))
        if receipt_members is None or manifest_members is None or receipt_members != manifest_members:
            _issue(issues, "instance_plate_evidence_membership_mismatch", f"{receipt_path}.instanceIds", "CP4 plate membership must exactly match the manifest plate membership order.")
        object_names = _string_list(receipt.get("objectNames"))
        if object_names is None or receipt_members is None or len(object_names) != len(receipt_members) or len(set(object_names)) != len(object_names):
            _issue(issues, "instance_plate_evidence_object_set_invalid", f"{receipt_path}.objectNames", "CP4 plate requires one unique G-code object name per physical instance.")

    totals = _record(evidence.get("totals"))
    if _pos_int(totals.get("instanceCount")) != len(instances) or _pos_int(totals.get("plateCount")) != len(plates):
        _issue(issues, "instance_plate_evidence_totals_mismatch", f"{path}.totals", "CP4 evidence totals must exactly reconcile with manifest instances and plates.")


def evaluate_fdm_authority(value: Mapping[str, Any] | Any) -> AuthorityEvaluation:
    """Compute technical authority; malformed or incomplete evidence always fails closed."""

    manifest = _record(value)
    issues: list[AuthorityIssue] = []

    manifest_version = _text(manifest.get("contractVersion")) or None
    if manifest_version != FDM_PRODUCTION_MANIFEST_VERSION:
        _issue(issues, "manifest_contract_mismatch", "contractVersion", f"Expected {FDM_PRODUCTION_MANIFEST_VERSION}.")

    job = _record(manifest.get("job"))
    job_version = _text(job.get("contractVersion")) or None
    if job_version != FDM_JOB_CONTRACT_VERSION:
        _issue(issues, "job_contract_mismatch", "job.contractVersion", f"Expected {FDM_JOB_CONTRACT_VERSION}.")
    request = _record(job.get("request"))
    material, quality, strength = (_text(request.get(name)).lower() for name in ("material", "quality", "strength"))
    quantity = _pos_int(request.get("quantity"))
    if not material or not quality or not strength:
        _issue(issues, "invalid_manufacturing_configuration", "job.request", "Material, quality, and strength are required authority inputs.")
    for field, expected in (("supports", "automatic"), ("orientation", "orca_auto"), ("arrangement", "orca_auto")):
        if request.get(field) != expected:
            _issue(issues, "unsupported_manufacturing_policy", f"job.request.{field}", f"FDM v2 currently requires {field}={expected}.")
    if quantity is None:
        _issue(issues, "invalid_requested_quantity", "job.request.quantity", "A positive integer quantity is required.")

    source = _record(manifest.get("source"))
    source_sha = _sha(source.get("sha256"))
    if not source_sha or source.get("immutable") is not True or _pos_int(source.get("bytes")) is None:
        _issue(issues, "invalid_immutable_source", "source", "Immutable source bytes, SHA-256, and immutable=true are required.")

    profiles = _record(manifest.get("profiles"))
    profile_hashes = _profile_receipts(profiles, issues)

    toolchain = _record(manifest.get("toolchain"))
    environment = _record(toolchain.get("executionEnvironment"))
    runtime_ref = _text(environment.get("reference"))
    runtime_digest = _text(environment.get("digest")).lower()
    ref_match, digest_match = _DIGEST_REF.fullmatch(runtime_ref), _DIGEST.fullmatch(runtime_digest)
    if not (
        _COMMIT.fullmatch(_text(toolchain.get("serviceCommit")).lower())
        and _text(toolchain.get("orcaVersion"))
        and _sha(toolchain.get("orcaBinarySha256"))
        and ref_match
        and digest_match
        and ref_match.group(1) == digest_match.group(1)
    ):
        _issue(issues, "missing_immutable_toolchain", "toolchain", "Exact service commit, Orca binary/version, and matching digest-pinned runtime are required.")

    machine = _record(manifest.get("machine"))
    machine_key = _text(machine.get("key"))
    if not machine_key:
        _issue(issues, "invalid_machine_identity", "machine.key", "Selected machine requires a stable key.")
    expected_identities = {
        "machine": f"{machine_key}:machine",
        "process": f"{machine_key}:{material}:{quality}:{strength}:process",
        "filament": f"{machine_key}:{material}:filament",
    }
    for kind, expected in expected_identities.items():
        if _text(_record(profiles.get(kind)).get("identity")) != expected:
            _issue(issues, "profile_configuration_mismatch", f"profiles.{kind}.identity", "Profile identity does not match selected machine and requested configuration.")
    qualification = _record(machine.get("qualification"))
    if qualification.get("productionReady") is not True or not _text(qualification.get("evidenceId")):
        _issue(issues, "machine_not_production_ready", "machine.qualification", "Physical machine/profile qualification must explicitly be production-ready and evidence-backed.")

    project = _record(manifest.get("project"))
    project_sha = _sha(project.get("sha256"))
    if not project_sha or _pos_int(project.get("bytes")) is None:
        _issue(issues, "invalid_project_artifact", "project", "Exact retained production 3MF bytes and SHA-256 are required.")
    if not source_sha or _sha(project.get("sourceSha256")) != source_sha:
        _issue(issues, "project_source_mismatch", "project.sourceSha256", "Production 3MF must bind to immutable source SHA-256.")
    _profile_links(project.get("profileSha256"), profile_hashes, issues, "project.profileSha256", "project_profile_mismatch")
    if not runtime_ref or _text(project.get("toolchainRef")) != runtime_ref:
        _issue(issues, "project_toolchain_mismatch", "project.toolchainRef", "Production 3MF must bind to the immutable runtime.")

    instance_items = manifest.get("instances") if isinstance(manifest.get("instances"), list) else []
    instances: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(instance_items):
        instance, path = _record(raw), f"instances[{index}]"
        instance_id = _text(instance.get("id"))
        if not instance_id:
            _issue(issues, "invalid_instance", path, "Every physical instance requires a stable id.")
            continue
        if instance_id in instances:
            _issue(issues, "duplicate_instance_id", f"{path}.id", f"Instance {instance_id} is defined more than once.")
            continue
        instances[instance_id] = instance
        if not _text(instance.get("objectId")) or not _text(instance.get("plateId")):
            _issue(issues, "invalid_instance", path, "Each instance must identify project object and physical plate.")
        if not _valid_transform(instance.get("transform")):
            _issue(issues, "invalid_instance_transform", f"{path}.transform", "Exact 3MF build-item transform must contain 12 finite numbers.")
        if not _valid_bounds(instance.get("boundsMm")):
            _issue(issues, "invalid_instance_bounds", f"{path}.boundsMm", "Each instance requires finite ordered physical bounds.")
    if quantity is not None and len(instances) != quantity:
        _issue(issues, "instance_quantity_mismatch", "instances", f"Expected {quantity} unique instances, found {len(instances)}.")

    plate_items = manifest.get("plates") if isinstance(manifest.get("plates"), list) else []
    plates: dict[str, Mapping[str, Any]] = {}
    plate_indexes: set[int] = set()
    membership: dict[str, list[str]] = {instance_id: [] for instance_id in instances}
    filament_total, time_total, statistics_complete = 0.0, 0, True

    for index, raw in enumerate(plate_items):
        plate, path = _record(raw), f"plates[{index}]"
        plate_id = _text(plate.get("id"))
        if not plate_id:
            _issue(issues, "invalid_plate", path, "Every physical plate requires a stable id.")
            continue
        if plate_id in plates:
            _issue(issues, "duplicate_plate_id", f"{path}.id", f"Plate {plate_id} is defined more than once.")
            continue
        plates[plate_id] = plate
        plate_index = _pos_int(plate.get("index"))
        if plate_index is None or plate_index in plate_indexes:
            _issue(issues, "invalid_plate_index", f"{path}.index", "Plate indexes must be positive and unique.")
        else:
            plate_indexes.add(plate_index)

        if not project_sha or _sha(plate.get("projectSha256")) != project_sha:
            _issue(issues, "plate_project_mismatch", f"{path}.projectSha256", "Plate must bind to exact production 3MF.")
        _profile_links(plate.get("profileSha256"), profile_hashes, issues, f"{path}.profileSha256", "plate_profile_mismatch")
        if not runtime_ref or _text(plate.get("toolchainRef")) != runtime_ref:
            _issue(issues, "plate_toolchain_mismatch", f"{path}.toolchainRef", "Plate must bind to immutable runtime.")

        member_ids = plate.get("instanceIds") if isinstance(plate.get("instanceIds"), list) else []
        if not member_ids:
            _issue(issues, "empty_plate_membership", f"{path}.instanceIds", "A production plate must contain at least one instance.")
        local_seen: set[str] = set()
        for raw_id in member_ids:
            instance_id = _text(raw_id)
            if not instance_id or instance_id in local_seen:
                _issue(issues, "duplicate_instance_membership", f"{path}.instanceIds", "A plate cannot list an instance more than once.")
                continue
            local_seen.add(instance_id)
            if instance_id not in instances:
                _issue(issues, "plate_unknown_instance", f"{path}.instanceIds", f"Plate {plate_id} references unknown instance {instance_id}.")
            else:
                membership[instance_id].append(plate_id)

        gcode = _record(plate.get("gcode"))
        gcode_sha = _sha(gcode.get("sha256"))
        if not gcode_sha or _pos_int(gcode.get("bytes")) is None or not _text(gcode.get("filename")).lower().endswith(".gcode"):
            _issue(issues, "missing_gcode_artifact", f"{path}.gcode", "Every plate requires retained .gcode bytes and SHA-256.")

        validation = _record(plate.get("validation"))
        if validation.get("contractVersion") != FDM_GCODE_VALIDATION_VERSION:
            _issue(issues, "invalid_validation_receipt", f"{path}.validation.contractVersion", f"Expected {FDM_GCODE_VALIDATION_VERSION}.")
        validator = _record(validation.get("validator"))
        if not _text(validator.get("name")) or not _text(validator.get("version")) or not _COMMIT.fullmatch(_text(validator.get("serviceCommit")).lower()):
            _issue(issues, "invalid_validator_identity", f"{path}.validation.validator", "Workpiece validator receipt must identify name, version, and exact service commit.")
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            _issue(issues, "gcode_validation_incomplete", f"{path}.validation", "Independent validation must pass with all authority-critical properties proven.")
        if not gcode_sha or _sha(validation.get("gcodeSha256")) != gcode_sha:
            _issue(issues, "validator_gcode_mismatch", f"{path}.validation.gcodeSha256", "Validator receipt must bind to exact retained G-code.")
        if not project_sha or _sha(validation.get("projectSha256")) != project_sha:
            _issue(issues, "validator_project_mismatch", f"{path}.validation.projectSha256", "Validator receipt must bind to exact production 3MF.")
        _profile_links(validation.get("profileSha256"), profile_hashes, issues, f"{path}.validation.profileSha256", "validator_profile_mismatch")
        if not runtime_ref or _text(validation.get("toolchainRef")) != runtime_ref:
            _issue(issues, "validator_toolchain_mismatch", f"{path}.validation.toolchainRef", "Validator receipt must bind to immutable runtime.")

        stats = _record(plate.get("statistics"))
        filament, print_time, layers = _pos_num(stats.get("filamentGrams")), _pos_int(stats.get("printTimeSeconds")), _pos_int(stats.get("layerCount"))
        sources_present = all(_text(stats.get(name)) for name in ("filamentSource", "printTimeSource", "layerCountSource"))
        if filament is None or print_time is None or layers is None or not sources_present:
            statistics_complete = False
            _issue(issues, "incomplete_plate_statistics", f"{path}.statistics", "Each plate requires positive statistics and explicit evidence-source labels.")
        else:
            filament_total += filament
            time_total += print_time

    for instance_id, instance in instances.items():
        declared_plate, memberships = _text(instance.get("plateId")), membership.get(instance_id, [])
        if declared_plate not in plates:
            _issue(issues, "orphan_instance", f"instances.{instance_id}.plateId", f"Instance {instance_id} refers to missing plate {declared_plate!r}.")
        if not memberships:
            _issue(issues, "orphan_instance", f"instances.{instance_id}", f"Instance {instance_id} is absent from all plate membership receipts.")
        elif len(memberships) > 1:
            _issue(issues, "duplicate_instance_membership", f"instances.{instance_id}", f"Instance {instance_id} appears on more than one plate.")
        elif declared_plate != memberships[0]:
            _issue(issues, "instance_plate_mismatch", f"instances.{instance_id}.plateId", "Instance plateId disagrees with physical plate membership.")

    _evaluate_instance_plate_evidence(
        manifest=manifest,
        source_sha=source_sha,
        machine_key=machine_key,
        project_sha=project_sha,
        profile_hashes=profile_hashes,
        instances=instances,
        plates=plates,
        issues=issues,
    )

    totals = _record(manifest.get("totals"))
    total_filament = _pos_num(totals.get("filamentGrams"))
    totals_match = (
        _pos_int(totals.get("plateCount")) == len(plates)
        and _pos_int(totals.get("instanceCount")) == len(instances)
        and statistics_complete
        and total_filament is not None
        and math.isclose(total_filament, filament_total, rel_tol=0.0, abs_tol=1e-6)
        and _pos_int(totals.get("printTimeSeconds")) == time_total
    )
    if not totals_match:
        _issue(issues, "incomplete_or_mismatched_totals", "totals", "Totals must exactly reconcile with all proven plates and instances.")

    if _record(manifest.get("review")).get("required") is not True:
        _issue(issues, "human_review_gate_missing", "review.required", "Human workshop review must remain a required manufacturing-safety gate.")

    authoritative = not issues
    return AuthorityEvaluation(
        state=AUTHORITY_PRODUCTION if authoritative else AUTHORITY_EVIDENCE_CANDIDATE,
        production_authoritative=authoritative,
        manifest_version=manifest_version,
        job_contract_version=job_version,
        issues=tuple(issues),
    )
