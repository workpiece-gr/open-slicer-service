"""Exact retained G-code execution for FDM Authority v2 CP2.

The CP2 path accepts an already-retained production 3MF as its sole manufacturing
input. It reopens that exact project in a fresh OrcaSlicer process, retains the
exact per-plate G-code bytes, hashes those same bytes, and binds them back to the
3MF plate ids and the carried generation receipt. It does not grant production
authority; CP3 validation is still required.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .project_builder import inspect_project_3mf, verify_project_command


_PLATE_GCODE_RE = re.compile(r"^plate_([1-9][0-9]*)\.gcode$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256(value: Any) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if _SHA256_RE.fullmatch(text) else ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fresh_orca_env(base_env: Mapping[str, str], xdg_root: Path) -> dict[str, str]:
    """Create a clean XDG root for the exact-project downstream Orca process.

    The caller must provide a root that does not already contain state. Existing
    generation-process XDG variables in ``base_env`` are deliberately replaced.
    """

    if xdg_root.exists() and any(xdg_root.iterdir()):
        raise ValueError("The downstream Orca XDG root must be empty before exact-project slicing.")
    config = xdg_root / "config"
    cache = xdg_root / "cache"
    data = xdg_root / "data"
    for directory in (config, cache, data):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        **dict(base_env),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "XDG_DATA_HOME": str(data),
    }


def normalize_generation_receipt(receipt_value: Mapping[str, Any] | Any, *, project_sha256: str) -> dict[str, Any]:
    """Validate and normalize the provenance carried from project generation.

    The receipt is evidence only. None of its profile paths or values are passed
    back to Orca during the exact-project slice.
    """

    receipt = _record(receipt_value)
    source = _record(receipt.get("source"))
    printer = _record(receipt.get("printer"))
    profiles = _record(receipt.get("profiles"))
    engine = _record(receipt.get("engine"))
    project = _record(receipt.get("project"))

    source_sha = _sha256(source.get("sha256"))
    printer_key = _text(printer.get("key"))
    temporary_generic = printer.get("temporary_generic")
    engine_name = _text(engine.get("name"))
    engine_version = _text(engine.get("version"))
    service_commit = _text(engine.get("service_commit")).lower()
    receipt_project_sha = _sha256(project.get("sha256"))

    if not source_sha:
        raise ValueError("The exact-project generation receipt is missing the immutable source SHA-256.")
    if not printer_key:
        raise ValueError("The exact-project generation receipt is missing the selected printer key.")
    if not isinstance(temporary_generic, bool):
        raise ValueError("The exact-project generation receipt must explicitly state the temporary-generic printer flag.")
    if engine_name != "OrcaSlicer" or not engine_version or not _COMMIT_RE.fullmatch(service_commit):
        raise ValueError("The exact-project generation receipt has incomplete Orca/service provenance.")
    if not receipt_project_sha or receipt_project_sha != project_sha256:
        raise ValueError("The exact-project generation receipt does not match the retained production 3MF SHA-256.")

    normalized_profiles: dict[str, dict[str, str]] = {}
    for kind in ("machine", "process", "filament"):
        profile = _record(profiles.get(kind))
        identity = _text(profile.get("identity"))
        digest = _sha256(profile.get("sha256"))
        if not identity or not digest:
            raise ValueError(f"The exact-project generation receipt is missing the {kind} profile identity or SHA-256.")
        normalized_profiles[kind] = {"identity": identity, "sha256": digest}

    if normalized_profiles["machine"]["identity"] != f"{printer_key}:machine":
        raise ValueError("The machine profile identity does not match the selected printer in the generation receipt.")
    for kind in ("process", "filament"):
        identity = normalized_profiles[kind]["identity"]
        if not identity.startswith(f"{printer_key}:") or not identity.endswith(f":{kind}"):
            raise ValueError(f"The {kind} profile identity does not match the selected printer in the generation receipt.")

    return {
        "source": {"sha256": source_sha},
        "printer": {
            "key": printer_key,
            "temporary_generic": temporary_generic,
        },
        "profiles": normalized_profiles,
        "engine": {
            "name": engine_name,
            "version": engine_version,
            "service_commit": service_commit,
        },
        "project": {"sha256": receipt_project_sha},
    }


def project_plate_ids(project_inspection: Mapping[str, Any] | Any) -> tuple[int, ...]:
    """Return the exact non-empty physical plate ids recorded in the 3MF.

    CP2 fails closed if plate metadata is absent, duplicated, malformed, or
    describes an empty plate. We do not infer physical plates from quantity or
    from G-code filenames.
    """

    inspection = _record(project_inspection)
    plates = inspection.get("plates")
    if not isinstance(plates, list) or not plates:
        raise ValueError("The retained production 3MF does not expose physical plate metadata.")

    result: list[int] = []
    seen: set[int] = set()
    for index, raw_plate in enumerate(plates):
        plate = _record(raw_plate)
        raw_id = plate.get("plater_id")
        try:
            plate_id = int(str(raw_id).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Production 3MF plate {index + 1} has an invalid plater_id.") from exc
        if plate_id < 1 or plate_id in seen:
            raise ValueError("Production 3MF plate ids must be positive and unique.")
        count = plate.get("model_instance_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"Production 3MF plate {plate_id} has no proven physical instances.")
        seen.add(plate_id)
        result.append(plate_id)

    return tuple(sorted(result))


def collect_exact_gcode_artifacts(
    output_dir: Path,
    *,
    project_inspection: Mapping[str, Any] | Any,
    project_sha256: str,
) -> list[dict[str, Any]]:
    """Map Orca 2.4.2 ``plate_N.gcode`` outputs to exact 3MF plate ids.

    Every expected 3MF plate must have exactly one canonical G-code output and
    no extra G-code may exist. The exact bytes are retained in-memory in the
    returned records so callers can persist or package the same bytes that were
    hashed. This function does not grant production authority by itself.
    """

    digest = project_sha256.strip().lower() if isinstance(project_sha256, str) else ""
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("The retained production 3MF SHA-256 is invalid.")

    expected = set(project_plate_ids(project_inspection))
    nested = [path for path in output_dir.rglob("*.gcode") if path.parent != output_dir]
    if nested:
        raise ValueError("Fresh-Orca slicing emitted a G-code artifact outside the exact output directory root.")
    discovered = sorted(output_dir.glob("*.gcode"), key=lambda path: path.name)
    if not discovered:
        raise ValueError("Fresh-Orca slicing produced no G-code artifacts.")

    by_plate: dict[int, dict[str, Any]] = {}
    for path in discovered:
        match = _PLATE_GCODE_RE.fullmatch(path.name)
        if not match:
            raise ValueError(f"Unexpected Orca G-code filename: {path.name}.")
        plate_id = int(match.group(1))
        if plate_id not in expected:
            raise ValueError(f"Orca emitted G-code for unexpected physical plate {plate_id}.")
        if plate_id in by_plate:
            raise ValueError(f"Orca emitted more than one G-code artifact for physical plate {plate_id}.")
        payload = path.read_bytes()
        if not payload:
            raise ValueError(f"Orca emitted an empty G-code artifact for physical plate {plate_id}.")
        by_plate[plate_id] = {
            "plate_id": plate_id,
            "filename": path.name,
            "bytes": payload,
            "byte_count": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "project_sha256": digest,
        }

    actual = set(by_plate)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"Fresh-Orca G-code plate set mismatch; missing={missing}, extra={extra}.")

    return [by_plate[plate_id] for plate_id in sorted(by_plate)]


def execute_exact_project_gcode(
    *,
    orca_bin: Path,
    project_path: Path,
    output_dir: Path,
    timeout_seconds: int,
    generation_receipt: Mapping[str, Any] | Any,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Slice the exact retained 3MF in a fresh process and retain exact outputs.

    No STL path or external profile path is accepted by this API. The returned
    artifact records contain the exact bytes that were hashed. Callers are
    responsible for durable persistence before the temporary workspace is
    removed.
    """

    if not orca_bin.is_file():
        raise ValueError("The pinned OrcaSlicer binary is not available.")
    if not project_path.is_file() or project_path.stat().st_size < 1:
        raise ValueError("The retained production 3MF is missing or empty.")
    if timeout_seconds < 1:
        raise ValueError("A positive Orca timeout is required.")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("The exact G-code output directory must be empty before slicing.")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        project_inspection = inspect_project_3mf(project_path)
    except ValueError as exc:
        raise ValueError(f"The retained production 3MF failed inspection: {exc}") from exc
    expected_plate_ids = project_plate_ids(project_inspection)
    project_sha256 = _sha256_file(project_path)
    provenance = normalize_generation_receipt(generation_receipt, project_sha256=project_sha256)

    xdg_root = output_dir.with_name(f"{output_dir.name}.xdg")
    env = fresh_orca_env(base_env or os.environ, xdg_root)
    command = verify_project_command(
        orca_bin=orca_bin,
        project_path=project_path,
        output_dir=output_dir,
    )
    completed = subprocess.run(
        command,
        cwd=project_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[-4000:]
        stdout = (completed.stdout or "").strip()[-2000:]
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Fresh Orca exact-project slicing failed: {detail}")
    if _sha256_file(project_path) != project_sha256:
        raise ValueError("The retained production 3MF changed during exact-project slicing.")

    artifacts = collect_exact_gcode_artifacts(
        output_dir,
        project_inspection=project_inspection,
        project_sha256=project_sha256,
    )
    if tuple(artifact["plate_id"] for artifact in artifacts) != expected_plate_ids:
        raise ValueError("Fresh-Orca G-code artifact order does not match the retained 3MF plate ids.")
    for artifact in artifacts:
        artifact["source_sha256"] = provenance["source"]["sha256"]
        artifact["printer_key"] = provenance["printer"]["key"]
        artifact["profile_sha256"] = {
            kind: provenance["profiles"][kind]["sha256"]
            for kind in ("machine", "process", "filament")
        }
        artifact["orca_version"] = provenance["engine"]["version"]
        artifact["service_commit"] = provenance["engine"]["service_commit"]

    return {
        "performed": True,
        "authority_state": "evidence_candidate",
        "reopened_exact_project": True,
        "fresh_runtime_state": True,
        "project_sha256": project_sha256,
        "plate_ids": list(expected_plate_ids),
        "generation_receipt": provenance,
        "plates": artifacts,
    }
