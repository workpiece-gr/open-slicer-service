"""Exact retained G-code artifact helpers for FDM Authority v2 CP2.

This module deliberately does not execute OrcaSlicer.  It validates the boundary
between an already-retained production 3MF inspection and the exact G-code files
emitted when that 3MF is reopened and sliced.  Later CP2 wiring can use the same
collector without changing the legacy production-project path.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping


_PLATE_GCODE_RE = re.compile(r"^plate_([1-9][0-9]*)\.gcode$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def fresh_orca_env(base_env: Mapping[str, str], xdg_root: Path) -> dict[str, str]:
    """Create a clean XDG root for the exact-project downstream Orca process.

    The caller must provide a root that does not already contain state.  Existing
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


def project_plate_ids(project_inspection: Mapping[str, Any] | Any) -> tuple[int, ...]:
    """Return the exact non-empty physical plate ids recorded in the 3MF.

    CP2 fails closed if plate metadata is absent, duplicated, malformed, or
    describes an empty plate.  We do not infer physical plates from quantity or
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
    no extra G-code may exist.  The exact bytes are retained in-memory in the
    returned records so callers can persist or package the same bytes that were
    hashed.  This function does not grant production authority by itself.
    """

    digest = project_sha256.strip().lower() if isinstance(project_sha256, str) else ""
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("The retained production 3MF SHA-256 is invalid.")

    expected = set(project_plate_ids(project_inspection))
    discovered = sorted(output_dir.rglob("*.gcode"), key=lambda path: (path.name, str(path)))
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
