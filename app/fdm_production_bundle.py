"""Deterministic production bundle for FDM Authority v2 CP6.

CP6 is a pure retained-artifact packaging stage. It never calls OrcaSlicer,
changes a printer/profile, publishes an image, deploys a service, or grants
manufacturing authority by itself.

By default the builder requires the supplied FDM v2 production manifest to
already be production-authoritative under ``evaluate_fdm_authority``. CI may
explicitly build an ``evidence_candidate`` bundle when the *only* unresolved
authority gates are immutable-toolchain publication and/or physical machine
qualification. This allows the bundle implementation to be proven before those
separate operational approvals exist without weakening either gate.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fdm_authority import AUTHORITY_EVIDENCE_CANDIDATE, AUTHORITY_PRODUCTION, evaluate_fdm_authority
from .fdm_toolchain_provenance import FDM_TOOLCHAIN_LOCK_SCHEMA, FDM_TOOLCHAIN_MANIFEST_SCHEMA, validate_toolchain_lock

FDM_PRODUCTION_BUNDLE_VERSION = "fdm-production-bundle/1.0.0"
FDM_PRODUCTION_BUNDLE_LAYOUT = "workpiece-fdm-production-bundle-v1"

_PROFILE_KINDS = ("machine", "process", "filament")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_CANDIDATE_AUTHORITY_ISSUES = frozenset({"missing_immutable_toolchain", "machine_not_production_ready"})


class FdmProductionBundleError(ValueError):
    pass


@dataclass(frozen=True)
class FdmProductionBundleResult:
    bytes: bytes
    filename: str
    sha256: str
    manifest_bytes: bytes
    manifest_sha256: str
    index_bytes: bytes
    index_sha256: str
    authority_state: str
    member_count: int


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256_RE.fullmatch(text) else ""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _exact_bytes(label: str, payload: bytes, expected_sha256: Any, expected_bytes: Any | None = None) -> None:
    if not isinstance(payload, bytes) or not payload:
        raise FdmProductionBundleError(f"{label} must contain exact non-empty bytes.")
    expected = _sha(expected_sha256)
    if not expected or _digest(payload) != expected:
        raise FdmProductionBundleError(f"{label} bytes do not match their exact SHA-256 receipt.")
    if expected_bytes is not None:
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 1 or len(payload) != expected_bytes:
            raise FdmProductionBundleError(f"{label} byte count does not match its retained receipt.")


def _simple_filename(value: Any, label: str) -> str:
    name = _text(value)
    if not name or name in {".", ".."} or "/" in name or "\\" in name or Path(name).name != name:
        raise FdmProductionBundleError(f"{label} must be a simple retained filename without a path.")
    return name


def _safe_stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip("-._")
    return stem or "workpiece"


def _zip_write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    # Store rather than deflate. With fixed metadata and exact bytes this avoids
    # zlib-version differences and makes the archive byte-for-byte reproducible
    # across otherwise equivalent Python runtimes.
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits = 0
    archive.writestr(info, payload)


def _authority_state(manifest: Mapping[str, Any], *, require_production_authority: bool) -> str:
    evaluation = evaluate_fdm_authority(manifest)
    if require_production_authority:
        if not evaluation.production_authoritative or evaluation.state != AUTHORITY_PRODUCTION:
            codes = sorted({issue.code for issue in evaluation.issues})
            raise FdmProductionBundleError(
                f"Production bundle requires a production-authoritative FDM manifest; unresolved={codes}."
            )
        return AUTHORITY_PRODUCTION

    issue_codes = {issue.code for issue in evaluation.issues}
    unexpected = issue_codes - _ALLOWED_CANDIDATE_AUTHORITY_ISSUES
    if unexpected:
        raise FdmProductionBundleError(
            "Candidate CP6 bundle has unresolved authority failures outside the explicitly deferred "
            f"machine/toolchain gates: {sorted(unexpected)}."
        )
    return AUTHORITY_EVIDENCE_CANDIDATE if issue_codes else AUTHORITY_PRODUCTION


def _plate_bytes_map(value: Mapping[str | int, bytes] | Any) -> dict[str, bytes]:
    if not isinstance(value, Mapping):
        raise FdmProductionBundleError("CP6 requires an exact per-plate G-code byte mapping.")
    result: dict[str, bytes] = {}
    for raw_key, raw_payload in value.items():
        key = str(raw_key).strip()
        if not key or key in result or not isinstance(raw_payload, bytes) or not raw_payload:
            raise FdmProductionBundleError("CP6 G-code mapping contains a duplicate, empty, or malformed plate artifact.")
        result[key] = raw_payload
    return result


def _resolve_plate_gcode(
    supplied: Mapping[str, bytes],
    *,
    plate_id: str,
    plate_index: int,
) -> tuple[bytes, str]:
    """Resolve one exact plate payload without accepting ambiguous aliases."""

    aliases = {plate_id, str(plate_index)}
    present = sorted(alias for alias in aliases if alias in supplied)
    if not present:
        raise FdmProductionBundleError(f"CP6 is missing exact G-code bytes for physical plate {plate_id}.")
    if len(present) > 1:
        raise FdmProductionBundleError(
            f"CP6 received ambiguous G-code aliases for physical plate {plate_id}: {present}."
        )
    key = present[0]
    return supplied[key], key


def _verify_toolchain_retained_bytes(
    *,
    toolchain: Mapping[str, Any],
    lock_bytes: bytes,
    manifest_bytes: bytes,
    package_inventory_bytes: bytes,
) -> None:
    _exact_bytes("FDM toolchain lock", lock_bytes, toolchain.get("lockSha256"))
    _exact_bytes("FDM toolchain manifest", manifest_bytes, toolchain.get("toolchainManifestSha256"))
    _exact_bytes("FDM package inventory", package_inventory_bytes, toolchain.get("packageInventorySha256"))

    try:
        lock = validate_toolchain_lock(lock_bytes, require_published=False)
    except ValueError as exc:
        raise FdmProductionBundleError(str(exc)) from exc
    if lock.get("schema") != FDM_TOOLCHAIN_LOCK_SCHEMA or _text(lock.get("status")) != _text(toolchain.get("lockStatus")):
        raise FdmProductionBundleError("Retained FDM toolchain lock schema/status differs from the CP5 receipt.")

    try:
        retained_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FdmProductionBundleError("Retained FDM toolchain manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(retained_manifest, dict) or retained_manifest.get("schema") != FDM_TOOLCHAIN_MANIFEST_SCHEMA:
        raise FdmProductionBundleError(
            f"Retained FDM toolchain manifest must declare schema={FDM_TOOLCHAIN_MANIFEST_SCHEMA}."
        )


def build_fdm_production_bundle(
    *,
    manifest: Mapping[str, Any] | Any,
    source_bytes: bytes,
    project_bytes: bytes,
    profile_bytes: Mapping[str, bytes] | Any,
    gcode_bytes_by_plate: Mapping[str | int, bytes] | Any,
    toolchain_lock_bytes: bytes,
    toolchain_manifest_bytes: bytes,
    package_inventory_bytes: bytes,
    require_production_authority: bool = True,
) -> FdmProductionBundleResult:
    """Hash-verify retained FDM evidence and create one deterministic ZIP.

    The exact Orca runtime binary is deliberately not duplicated into every
    production bundle. CP5 binds it by SHA-256 to a digest-pinned external
    runtime image; CP6 retains and verifies the lock, toolchain manifest, package
    inventory, and immutable references that describe that runtime.
    """

    if not isinstance(manifest, Mapping):
        raise FdmProductionBundleError("CP6 requires an FDM production manifest object.")
    authority_state = _authority_state(manifest, require_production_authority=require_production_authority)

    source = _record(manifest.get("source"))
    source_name = _simple_filename(source.get("filename"), "Production source filename")
    _exact_bytes("immutable source STL", source_bytes, source.get("sha256"), source.get("bytes"))
    if source.get("immutable") is not True:
        raise FdmProductionBundleError("Production bundle source must remain explicitly immutable.")

    project = _record(manifest.get("project"))
    project_name = _simple_filename(project.get("filename"), "Production project filename")
    if not project_name.lower().endswith(".3mf"):
        raise FdmProductionBundleError("Production project retained filename must end in .3mf.")
    _exact_bytes("production 3MF", project_bytes, project.get("sha256"), project.get("bytes"))
    if _sha(project.get("sourceSha256")) != _digest(source_bytes):
        raise FdmProductionBundleError("Production 3MF receipt is not bound to the exact source bytes.")

    profiles = _record(manifest.get("profiles"))
    if not isinstance(profile_bytes, Mapping):
        raise FdmProductionBundleError("CP6 requires exact machine/process/filament profile bytes.")
    exact_profiles: dict[str, bytes] = {}
    for kind in _PROFILE_KINDS:
        payload = profile_bytes.get(kind)
        if not isinstance(payload, bytes):
            raise FdmProductionBundleError(f"CP6 is missing exact {kind} profile bytes.")
        _exact_bytes(f"exact {kind} profile", payload, _record(profiles.get(kind)).get("sha256"))
        exact_profiles[kind] = payload

    toolchain = _record(manifest.get("toolchain"))
    _verify_toolchain_retained_bytes(
        toolchain=toolchain,
        lock_bytes=toolchain_lock_bytes,
        manifest_bytes=toolchain_manifest_bytes,
        package_inventory_bytes=package_inventory_bytes,
    )

    supplied_gcode = _plate_bytes_map(gcode_bytes_by_plate)
    plate_items = manifest.get("plates")
    if not isinstance(plate_items, list) or not plate_items:
        raise FdmProductionBundleError("CP6 requires at least one physical plate receipt.")

    plates: list[tuple[int, str, Mapping[str, Any], bytes, str]] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    consumed_gcode_keys: set[str] = set()
    for raw in plate_items:
        plate = _record(raw)
        plate_id = _text(plate.get("id"))
        index = plate.get("index")
        if not plate_id or plate_id in seen_ids or isinstance(index, bool) or not isinstance(index, int) or index < 1 or index in seen_indexes:
            raise FdmProductionBundleError("CP6 plate receipts require unique ids and unique positive indexes.")
        payload, payload_key = _resolve_plate_gcode(supplied_gcode, plate_id=plate_id, plate_index=index)
        if payload_key in consumed_gcode_keys:
            raise FdmProductionBundleError(
                f"CP6 G-code key {payload_key!r} would be consumed by more than one physical plate."
            )
        consumed_gcode_keys.add(payload_key)
        gcode = _record(plate.get("gcode"))
        original_gcode_name = _simple_filename(gcode.get("filename"), f"Plate {plate_id} G-code filename")
        if not original_gcode_name.lower().endswith(".gcode"):
            raise FdmProductionBundleError(f"Plate {plate_id} retained G-code filename must end in .gcode.")
        _exact_bytes(f"plate {plate_id} G-code", payload, gcode.get("sha256"), gcode.get("bytes"))
        if _sha(plate.get("projectSha256")) != _digest(project_bytes):
            raise FdmProductionBundleError(f"Plate {plate_id} receipt is not bound to the exact production 3MF bytes.")
        seen_ids.add(plate_id)
        seen_indexes.add(index)
        plates.append((index, plate_id, plate, payload, original_gcode_name))

    if set(supplied_gcode) != consumed_gcode_keys:
        raise FdmProductionBundleError("CP6 received G-code bytes for a plate or alias not uniquely consumed by the production manifest.")
    plates.sort(key=lambda item: (item[0], item[1]))

    retained: list[tuple[str, bytes]] = [
        (f"source/{source_name}", source_bytes),
        (f"project/{project_name}", project_bytes),
        ("profiles/machine.json", exact_profiles["machine"]),
        ("profiles/process.json", exact_profiles["process"]),
        ("profiles/filament.json", exact_profiles["filament"]),
        ("toolchain/lock.json", toolchain_lock_bytes),
        ("toolchain/manifest.json", toolchain_manifest_bytes),
        ("toolchain/packages.txt", package_inventory_bytes),
        ("evidence/toolchain-receipt.json", _canonical_json(toolchain)),
        ("evidence/instance-plate.json", _canonical_json(_record(manifest.get("instancePlateEvidence")))),
    ]

    plate_file_map: dict[str, dict[str, str]] = {}
    for index, plate_id, plate, payload, original_gcode_name in plates:
        archive_gcode = f"plates/plate-{index:03d}.gcode"
        validation_path = f"evidence/plates/plate-{index:03d}-validation.json"
        retained.append((archive_gcode, payload))
        retained.append((validation_path, _canonical_json(_record(plate.get("validation")))))
        plate_file_map[plate_id] = {
            "gcode": archive_gcode,
            "validation": validation_path,
            "originalGcodeFilename": original_gcode_name,
        }

    names = [name for name, _ in retained]
    if len(names) != len(set(names)):
        raise FdmProductionBundleError("CP6 deterministic bundle member paths are not unique.")

    bundle_manifest = dict(manifest)
    bundle_manifest["bundle"] = {
        "contractVersion": FDM_PRODUCTION_BUNDLE_VERSION,
        "layout": FDM_PRODUCTION_BUNDLE_LAYOUT,
        "authorityState": authority_state,
        "deterministic": True,
        "zipCompression": "stored",
        "manifestPath": "manifest.json",
        "indexPath": "bundle-index.json",
        "sourcePath": f"source/{source_name}",
        "projectPath": f"project/{project_name}",
        "profilePaths": {kind: f"profiles/{kind}.json" for kind in _PROFILE_KINDS},
        "toolchainPaths": {
            "lock": "toolchain/lock.json",
            "manifest": "toolchain/manifest.json",
            "packageInventory": "toolchain/packages.txt",
            "receipt": "evidence/toolchain-receipt.json",
        },
        "instancePlateEvidencePath": "evidence/instance-plate.json",
        "plateFiles": plate_file_map,
        "retainedMemberCount": len(retained) + 2,
        "productionEnablementPerformed": False,
    }
    manifest_payload = _canonical_json(bundle_manifest)
    retained.append(("manifest.json", manifest_payload))

    index = {
        "contractVersion": FDM_PRODUCTION_BUNDLE_VERSION,
        "layout": FDM_PRODUCTION_BUNDLE_LAYOUT,
        "authorityState": authority_state,
        "members": [
            {"path": name, "bytes": len(payload), "sha256": _digest(payload)}
            for name, payload in retained
        ],
    }
    index_payload = _canonical_json(index)
    retained.append(("bundle-index.json", index_payload))

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, payload in retained:
            _zip_write(archive, name, payload)
    archive_bytes = output.getvalue()
    filename = f"{_safe_stem(source_name)}-workpiece-fdm-production.zip"
    return FdmProductionBundleResult(
        bytes=archive_bytes,
        filename=filename,
        sha256=_digest(archive_bytes),
        manifest_bytes=manifest_payload,
        manifest_sha256=_digest(manifest_payload),
        index_bytes=index_payload,
        index_sha256=_digest(index_payload),
        authority_state=authority_state,
        member_count=len(retained),
    )