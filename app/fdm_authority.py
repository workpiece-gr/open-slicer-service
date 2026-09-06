"""Fail-closed authority contract validation for the FDM v2 evidence chain.

CP1 intentionally contains no OrcaSlicer execution or HTTP-route integration.  It
only defines the evidence relationships that later checkpoints must satisfy.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


FDM_JOB_CONTRACT_VERSION = "fdm-job/2.0.0"
FDM_PRODUCTION_MANIFEST_VERSION = "fdm-production-manifest/2.0.0"
FDM_GCODE_VALIDATION_VERSION = "fdm-gcode-validation/1.0.0"

AUTHORITY_EVIDENCE_CANDIDATE = "evidence_candidate"
AUTHORITY_PRODUCTION = "production_authoritative"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_DIGEST_RE = re.compile(r"^sha256:([a-f0-9]{64})$")
_DIGEST_REF_RE = re.compile(r"^.+@sha256:([a-f0-9]{64})$")


@dataclass(frozen=True)
class AuthorityIssue:
    """One reason a manifest cannot be granted production authority."""

    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class AuthorityEvaluation:
    """Computed authority state. The manifest never self-grants authority."""

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


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sha256(value: Any) -> str:
    text = value.lower() if isinstance(value, str) else ""
    return text if _SHA256_RE.fullmatch(text) else ""


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _add(issues: list[AuthorityIssue], code: str, path: str, message: str) -> None:
    issues.append(AuthorityIssue(code=code, path=path, message=message))


def _validate_profile_receipts(profiles: Mapping[str, Any], issues: list[AuthorityIssue]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for kind in ("machine", "process", "filament"):
        receipt = _record(profiles.get(kind))
        identity = _text(receipt.get("identity"))
        digest = _sha256(receipt.get("sha256"))
        if not identity or not digest:
            _add(
                issues,
                "missing_profile_receipt",
                f"profiles.{kind}",
                f"The exact {kind} profile identity and SHA-256 are required.",
            )
            continue
        hashes[kind] = digest
    return hashes


def _validate_profile_links(
    value: Any,
    expected: Mapping[str, str],
    issues: list[AuthorityIssue],
    *,
    path: str,
    code: str,
) -> None:
    links = _record(value)
    for kind in ("machine", "process", "filament"):
        expected_digest = expected.get(kind, "")
        actual = _sha256(links.get(kind))
        if not expected_digest or actual != expected_digest:
            _add(issues, code, f"{path}.{kind}", f"The {kind} profile hash does not match the retained profile receipt.")


def _validate_transform(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 12:
        return False
    return all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value)


def _validate_bounds(value: Any) -> bool:
    bounds = _record(value)
    minimum = bounds.get("min")
    maximum = bounds.get("max")
    if not isinstance(minimum, list) or not isinstance(maximum, list) or len(minimum) != 3 or len(maximum) != 3:
        return False
    for low, high in zip(minimum, maximum, strict=True):
        if (
            isinstance(low, bool)
            or isinstance(high, bool)
            or not isinstance(low, (int, float))
            or not isinstance(high, (int, float))
            or not math.isfinite(float(low))
            or not math.isfinite(float(high))
            or float(low) > float(high)
        ):
            return False
    return True


def evaluate_fdm_authority(manifest_value: Mapping[str, Any] | Any) -> AuthorityEvaluation:
    """Evaluate whether an FDM v2 manifest proves complete production authority.

    Any absent, malformed, mismatched, unqualified, or unproven authority-critical
    relationship leaves the result at ``evidence_candidate``.  Human approval and
    commercial price authority are intentionally separate gates.
    """

    manifest = _record(manifest_value)
    issues: list[AuthorityIssue] = []

    manifest_version = _text(manifest.get("contractVersion")) or None
    if manifest_version != FDM_PRODUCTION_MANIFEST_VERSION:
        _add(
            issues,
            "manifest_contract_mismatch",
            "contractVersion",
            f"Expected {FDM_PRODUCTION_MANIFEST_VERSION}.",
        )

    job = _record(manifest.get("job"))
    job_contract_version = _text(job.get("contractVersion")) or None
    if job_contract_version != FDM_JOB_CONTRACT_VERSION:
        _add(issues, "job_contract_mismatch", "job.contractVersion", f"Expected {FDM_JOB_CONTRACT_VERSION}.")
    request = _record(job.get("request"))
    quantity = _positive_int(request.get("quantity"))
    if quantity is None:
        _add(issues, "invalid_requested_quantity", "job.request.quantity", "A positive integer quantity is required.")

    source = _record(manifest.get("source"))
    source_sha = _sha256(source.get("sha256"))
    if not source_sha or source.get("immutable") is not True or _positive_int(source.get("bytes")) is None:
        _add(issues, "invalid_immutable_source", "source", "The immutable source must carry bytes, SHA-256, and immutable=true.")

    profiles = _record(manifest.get("profiles"))
    profile_hashes = _validate_profile_receipts(profiles, issues)

    toolchain = _record(manifest.get("toolchain"))
    service_commit = _text(toolchain.get("serviceCommit")).lower()
    orca_version = _text(toolchain.get("orcaVersion"))
    orca_binary_sha = _sha256(toolchain.get("orcaBinarySha256"))
    environment = _record(toolchain.get("executionEnvironment"))
    runtime_ref = _text(environment.get("reference"))
    runtime_digest = _text(environment.get("digest")).lower()
    ref_match = _DIGEST_REF_RE.fullmatch(runtime_ref)
    digest_match = _DIGEST_RE.fullmatch(runtime_digest)
    toolchain_valid = bool(
        _COMMIT_RE.fullmatch(service_commit)
        and orca_version
        and orca_binary_sha
        and ref_match
        and digest_match
        and ref_match.group(1) == digest_match.group(1)
    )
    if not toolchain_valid:
        _add(
            issues,
            "missing_immutable_toolchain",
            "toolchain",
            "Service commit, Orca binary SHA-256, version, and a matching digest-pinned execution environment are required.",
        )

    machine = _record(manifest.get("machine"))
    qualification = _record(machine.get("qualification"))
    if qualification.get("productionReady") is not True or not _text(qualification.get("evidenceId")):
        _add(
            issues,
            "machine_not_production_ready",
            "machine.qualification",
            "Physical machine/profile qualification must explicitly be production-ready and evidence-backed.",
        )

    project = _record(manifest.get("project"))
    project_sha = _sha256(project.get("sha256"))
    if not project_sha or _positive_int(project.get("bytes")) is None:
        _add(issues, "invalid_project_artifact", "project", "The exact retained production 3MF needs bytes and SHA-256.")
    if not source_sha or _sha256(project.get("sourceSha256")) != source_sha:
        _add(issues, "project_source_mismatch", "project.sourceSha256", "The production 3MF must bind to the immutable source SHA-256.")
    _validate_profile_links(
        project.get("profileSha256"),
        profile_hashes,
        issues,
        path="project.profileSha256",
        code="project_profile_mismatch",
    )
    if not runtime_ref or _text(project.get("toolchainRef")) != runtime_ref:
        _add(issues, "project_toolchain_mismatch", "project.toolchainRef", "The production 3MF must bind to the immutable execution environment.")

    instances_raw = _list(manifest.get("instances"))
    instances: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(instances_raw):
        instance = _record(raw)
        instance_id = _text(instance.get("id"))
        path = f"instances[{index}]"
        if not instance_id:
            _add(issues, "invalid_instance", path, "Every physical instance requires a stable non-empty id.")
            continue
        if instance_id in instances:
            _add(issues, "duplicate_instance_id", f"{path}.id", f"Instance id {instance_id} is defined more than once.")
            continue
        instances[instance_id] = instance
        if not _text(instance.get("objectId")) or not _text(instance.get("plateId")):
            _add(issues, "invalid_instance", path, "Each instance must identify its project object and physical plate.")
        if not _validate_transform(instance.get("transform")):
            _add(issues, "invalid_instance_transform", f"{path}.transform", "The exact 3MF build-item transform must contain 12 finite numbers.")
        if not _validate_bounds(instance.get("boundsMm")):
            _add(issues, "invalid_instance_bounds", f"{path}.boundsMm", "Each instance requires finite ordered physical placement bounds.")

    if quantity is not None and len(instances) != quantity:
        _add(
            issues,
            "instance_quantity_mismatch",
            "instances",
            f"Expected {quantity} unique physical instances, found {len(instances)}.",
        )

    plates_raw = _list(manifest.get("plates"))
    plates: dict[str, Mapping[str, Any]] = {}
    plate_indexes: set[int] = set()
    membership: dict[str, list[str]] = {instance_id: [] for instance_id in instances}
    filament_total = 0.0
    time_total = 0
    statistics_complete = True

    for index, raw in enumerate(plates_raw):
        plate = _record(raw)
        plate_id = _text(plate.get("id"))
        path = f"plates[{index}]"
        if not plate_id:
            _add(issues, "invalid_plate", path, "Every physical plate requires a stable non-empty id.")
            continue
        if plate_id in plates:
            _add(issues, "duplicate_plate_id", f"{path}.id", f"Plate id {plate_id} is defined more than once.")
            continue
        plates[plate_id] = plate

        plate_index = _positive_int(plate.get("index"))
        if plate_index is None or plate_index in plate_indexes:
            _add(issues, "invalid_plate_index", f"{path}.index", "Physical plate indexes must be positive and unique.")
        else:
            plate_indexes.add(plate_index)

        if not project_sha or _sha256(plate.get("projectSha256")) != project_sha:
            _add(issues, "plate_project_mismatch", f"{path}.projectSha256", "The plate must bind to the exact production 3MF.")
        _validate_profile_links(
            plate.get("profileSha256"),
            profile_hashes,
            issues,
            path=f"{path}.profileSha256",
            code="plate_profile_mismatch",
        )
        if not runtime_ref or _text(plate.get("toolchainRef")) != runtime_ref:
            _add(issues, "plate_toolchain_mismatch", f"{path}.toolchainRef", "The plate must bind to the immutable execution environment.")

        instance_ids = plate.get("instanceIds")
        if not isinstance(instance_ids, list) or not instance_ids:
            _add(issues, "empty_plate_membership", f"{path}.instanceIds", "A production plate must contain at least one physical instance.")
            instance_ids = []
        local_seen: set[str] = set()
        for raw_instance_id in instance_ids:
            member_id = _text(raw_instance_id)
            if not member_id or member_id in local_seen:
                _add(issues, "duplicate_instance_membership", f"{path}.instanceIds", "A plate cannot list an instance more than once.")
                continue
            local_seen.add(member_id)
            if member_id not in instances:
                _add(issues, "plate_unknown_instance", f"{path}.instanceIds", f"Plate {plate_id} references unknown instance {member_id}.")
                continue
            membership[member_id].append(plate_id)

        gcode = _record(plate.get("gcode"))
        gcode_sha = _sha256(gcode.get("sha256"))
        if not gcode_sha or _positive_int(gcode.get("bytes")) is None or not _text(gcode.get("filename")).lower().endswith(".gcode"):
            _add(issues, "missing_gcode_artifact", f"{path}.gcode", "Every physical plate requires retained .gcode bytes and SHA-256.")

        validation = _record(plate.get("validation"))
        if validation.get("contractVersion") != FDM_GCODE_VALIDATION_VERSION:
            _add(issues, "invalid_validation_receipt", f"{path}.validation.contractVersion", f"Expected {FDM_GCODE_VALIDATION_VERSION}.")
        validator = _record(validation.get("validator"))
        validator_commit = _text(validator.get("serviceCommit")).lower()
        if not _text(validator.get("name")) or not _text(validator.get("version")) or not _COMMIT_RE.fullmatch(validator_commit):
            _add(issues, "invalid_validator_identity", f"{path}.validation.validator", "The independent Workpiece validator receipt must identify its name, version, and exact service commit.")
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            _add(issues, "gcode_validation_incomplete", f"{path}.validation", "Independent validation must pass and prove all authority-critical properties.")
        if not gcode_sha or _sha256(validation.get("gcodeSha256")) != gcode_sha:
            _add(issues, "validator_gcode_mismatch", f"{path}.validation.gcodeSha256", "The validator receipt must bind to the exact retained G-code.")
        if not project_sha or _sha256(validation.get("projectSha256")) != project_sha:
            _add(issues, "validator_project_mismatch", f"{path}.validation.projectSha256", "The validator receipt must bind to the exact production 3MF.")
        _validate_profile_links(
            validation.get("profileSha256"),
            profile_hashes,
            issues,
            path=f"{path}.validation.profileSha256",
            code="validator_profile_mismatch",
        )
        if not runtime_ref or _text(validation.get("toolchainRef")) != runtime_ref:
            _add(issues, "validator_toolchain_mismatch", f"{path}.validation.toolchainRef", "The validator receipt must bind to the immutable execution environment.")

        stats = _record(plate.get("statistics"))
        filament = _positive_number(stats.get("filamentGrams"))
        print_time = _positive_int(stats.get("printTimeSeconds"))
        layer_count = _positive_int(stats.get("layerCount"))
        if (
            filament is None
            or print_time is None
            or layer_count is None
            or not _text(stats.get("filamentSource"))
            or not _text(stats.get("printTimeSource"))
            or not _text(stats.get("layerCountSource"))
        ):
            statistics_complete = False
            _add(issues, "incomplete_plate_statistics", f"{path}.statistics", "Each plate requires positive statistics plus explicit evidence-source labels.")
        else:
            filament_total += filament
            time_total += print_time

    for instance_id, instance in instances.items():
        declared_plate = _text(instance.get("plateId"))
        memberships = membership.get(instance_id, [])
        if declared_plate not in plates:
            _add(issues, "orphan_instance", f"instances.{instance_id}.plateId", f"Instance {instance_id} refers to missing plate {declared_plate!r}.")
        if not memberships:
            _add(issues, "orphan_instance", f"instances.{instance_id}", f"Instance {instance_id} is not present in any plate membership list.")
        elif len(memberships) > 1:
            _add(issues, "duplicate_instance_membership", f"instances.{instance_id}", f"Instance {instance_id} appears on more than one physical plate.")
        elif declared_plate != memberships[0]:
            _add(issues, "instance_plate_mismatch", f"instances.{instance_id}.plateId", "Instance plateId disagrees with the physical plate membership receipt.")

    totals = _record(manifest.get("totals"))
    totals_plate_count = _positive_int(totals.get("plateCount"))
    totals_instance_count = _positive_int(totals.get("instanceCount"))
    totals_filament = _positive_number(totals.get("filamentGrams"))
    totals_time = _positive_int(totals.get("printTimeSeconds"))
    totals_valid = (
        totals_plate_count == len(plates)
        and totals_instance_count == len(instances)
        and statistics_complete
        and totals_filament is not None
        and math.isclose(totals_filament, filament_total, rel_tol=0.0, abs_tol=1e-6)
        and totals_time == time_total
    )
    if not totals_valid:
        _add(issues, "incomplete_or_mismatched_totals", "totals", "Manifest totals must exactly reconcile with all proven plates and instances.")

    review = _record(manifest.get("review"))
    if review.get("required") is not True:
        _add(issues, "human_review_gate_missing", "review.required", "Human workshop review must remain a required manufacturing-safety gate.")

    production_authoritative = not issues
    return AuthorityEvaluation(
        state=AUTHORITY_PRODUCTION if production_authoritative else AUTHORITY_EVIDENCE_CANDIDATE,
        production_authoritative=production_authoritative,
        manifest_version=manifest_version,
        job_contract_version=job_contract_version,
        issues=tuple(issues),
    )
