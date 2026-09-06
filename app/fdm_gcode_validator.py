"""Independent Workpiece G-code validator for FDM Authority v2 CP3.

The validator never calls OrcaSlicer. It consumes exact retained G-code bytes,
CP2 provenance receipts, and exact machine/filament profile bytes whose SHA-256
values must match those receipts. Physical limits are derived only from those
profile bytes; no calibration or thermal limits are invented here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from .fdm_authority import FDM_GCODE_VALIDATION_VERSION

VALIDATOR_NAME = "workpiece-gcode-validator"
VALIDATOR_VERSION = "1.0.0"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_AXIS_RE = re.compile(r"([XYZEFS])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", re.I)
_MACRO_ARG_RE = re.compile(r"\b([A-Z][A-Z0-9_]*)=([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\b")

_ALLOWED_G = {"G0", "G1", "G4", "G10", "G11", "G21", "G28", "G90", "G91", "G92"}
_FORBIDDEN_M = {"M112", "M206", "M500", "M501", "M502", "M524", "M997", "M999"}
_FORBIDDEN_MACROS = {"FIRMWARE_RESTART", "RESTART", "RUN_SHELL_COMMAND", "SAVE_CONFIG"}
_PROFILE_KINDS = ("machine", "process", "filament")


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256_RE.fullmatch(text) else ""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_scalar(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if value is not None else ""


def _profile_json(payload: bytes, kind: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"The exact {kind} profile bytes are not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict) or value.get("type") != kind:
        raise ValueError(f"The exact {kind} profile must declare type={kind}.")
    return value


def _parse_printable_area(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("The machine profile does not expose a usable printable_area polygon.")
    points: list[tuple[float, float]] = []
    for raw in value:
        text = str(raw)
        if "x" not in text.lower():
            raise ValueError("The machine printable_area contains a malformed point.")
        left, right = re.split("x", text, maxsplit=1, flags=re.I)
        x, y = _finite(left), _finite(right)
        if x is None or y is None:
            raise ValueError("The machine printable_area contains a non-finite point.")
        points.append((x, y))
    min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
    min_y, max_y = min(y for _, y in points), max(y for _, y in points)
    if min_x != 0 or min_y != 0 or max_x <= 0 or max_y <= 0:
        raise ValueError("CP3 currently requires an origin-based rectangular printable area.")
    return max_x, max_y


def _macro_tokens(profile: Mapping[str, Any]) -> set[str]:
    keys = (
        "machine_start_gcode",
        "machine_end_gcode",
        "before_layer_change_gcode",
        "layer_change_gcode",
        "filament_start_gcode",
        "filament_end_gcode",
    )
    result: set[str] = set()
    for key in keys:
        raw = profile.get(key)
        blocks = raw if isinstance(raw, list) else [raw]
        for block in blocks:
            if not isinstance(block, str):
                continue
            for line in block.splitlines():
                code = line.split(";", 1)[0].strip()
                if not code:
                    continue
                token = code.split(None, 1)[0].upper()
                if not token.startswith(("G", "M", "T")):
                    result.add(token)
    return result


def build_validation_policy(
    *,
    printer_key: str,
    machine_profile_bytes: bytes,
    filament_profile_bytes: bytes,
) -> dict[str, Any]:
    """Build validator policy only from exact profile bytes supplied by caller."""

    machine = _profile_json(machine_profile_bytes, "machine")
    filament = _profile_json(filament_profile_bytes, "filament")
    width, depth = _parse_printable_area(machine.get("printable_area"))
    height = _finite(machine.get("printable_height"))
    if height is None or height <= 0:
        raise ValueError("The machine profile does not expose a positive printable_height.")

    nozzle_low = _finite(_first_scalar(filament.get("nozzle_temperature_range_low")))
    nozzle_high = _finite(_first_scalar(filament.get("nozzle_temperature_range_high")))
    if nozzle_low is None or nozzle_high is None or nozzle_low > nozzle_high:
        raise ValueError("The filament profile does not expose a valid nozzle temperature range.")

    bed_values: list[float] = []
    for key, raw in filament.items():
        if not (key.endswith("_plate_temp") or key.endswith("_plate_temp_initial_layer")):
            continue
        value = _finite(_first_scalar(raw))
        if value is not None:
            bed_values.append(value)
    if not bed_values:
        raise ValueError("The filament profile does not expose explicit bed temperature setpoints.")

    macros = _macro_tokens(machine) | _macro_tokens(filament)
    if macros & _FORBIDDEN_MACROS:
        raise ValueError("An exact profile requests a macro that CP3 forbids for authority.")

    flavor = _text(machine.get("gcode_flavor")).lower()
    if not flavor:
        raise ValueError("The machine profile does not declare gcode_flavor.")

    return {
        "printerKey": printer_key,
        "gcodeFlavor": flavor,
        "envelopeMm": [width, depth, height],
        "nozzleTemperatureRangeC": [nozzle_low, nozzle_high],
        "bedTemperatureRangeC": [min(bed_values), max(bed_values)],
        "allowedMacros": sorted(macros),
    }


def _issue(issues: list[dict[str, Any]], code: str, line: int | None, message: str) -> None:
    entry: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        entry["line"] = line
    issues.append(entry)


def validate_exact_gcode(
    *,
    artifact: Mapping[str, Any] | Any,
    generation_receipt: Mapping[str, Any] | Any,
    machine_profile_bytes: bytes,
    filament_profile_bytes: bytes,
    validator_service_commit: str,
    toolchain_ref: str,
) -> dict[str, Any]:
    """Validate exact CP2 G-code bytes without invoking OrcaSlicer."""

    artifact = _record(artifact)
    generation = _record(generation_receipt)
    source = _record(generation.get("source"))
    printer = _record(generation.get("printer"))
    profiles = _record(generation.get("profiles"))
    project = _record(generation.get("project"))
    issues: list[dict[str, Any]] = []

    raw_bytes = artifact.get("bytes")
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        raise ValueError("CP3 requires the exact retained G-code bytes.")
    gcode_bytes = bytes(raw_bytes)
    actual_gcode_sha = _digest(gcode_bytes)
    receipt_gcode_sha = _sha(artifact.get("sha256"))
    if not receipt_gcode_sha or receipt_gcode_sha != actual_gcode_sha:
        _issue(issues, "gcode_sha256_mismatch", None, "Exact G-code bytes do not match the CP2 artifact receipt.")

    project_sha = _sha(artifact.get("project_sha256"))
    generation_project_sha = _sha(project.get("sha256"))
    if not project_sha or project_sha != generation_project_sha:
        _issue(issues, "project_sha256_mismatch", None, "CP2 artifact and generation receipt disagree on the production 3MF.")

    profile_hashes: dict[str, str] = {}
    artifact_profiles = _record(artifact.get("profile_sha256"))
    for kind in _PROFILE_KINDS:
        expected = _sha(_record(profiles.get(kind)).get("sha256"))
        actual = _sha(artifact_profiles.get(kind))
        if not expected or actual != expected:
            _issue(issues, "profile_sha256_mismatch", None, f"{kind} profile hash is not bound to the generation receipt.")
        if expected:
            profile_hashes[kind] = expected

    if profile_hashes.get("machine") and _digest(machine_profile_bytes) != profile_hashes["machine"]:
        _issue(issues, "machine_profile_bytes_mismatch", None, "Exact machine profile bytes do not match the generation SHA-256.")
    if profile_hashes.get("filament") and _digest(filament_profile_bytes) != profile_hashes["filament"]:
        _issue(issues, "filament_profile_bytes_mismatch", None, "Exact filament profile bytes do not match the generation SHA-256.")

    printer_key = _text(printer.get("key"))
    if _text(artifact.get("printer_key")) != printer_key:
        _issue(issues, "printer_mismatch", None, "CP2 artifact printer key differs from the generation receipt.")

    policy: dict[str, Any] | None = None
    if not any(issue["code"] in {"machine_profile_bytes_mismatch", "filament_profile_bytes_mismatch"} for issue in issues):
        try:
            policy = build_validation_policy(
                printer_key=printer_key,
                machine_profile_bytes=machine_profile_bytes,
                filament_profile_bytes=filament_profile_bytes,
            )
        except ValueError as exc:
            _issue(issues, "invalid_validation_policy", None, str(exc))

    try:
        text = gcode_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
        _issue(issues, "invalid_gcode_encoding", None, "Exact G-code is not valid UTF-8.")
    if "\x00" in text:
        _issue(issues, "nul_byte_in_gcode", None, "Exact G-code contains a NUL byte.")

    x: float | None = 0.0
    y: float | None = 0.0
    z: float | None = 0.0
    e = 0.0
    xyz_absolute = True
    e_absolute = True
    extrusion_count = 0
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    observed_macros: set[str] = set()
    nozzle_targets: list[float] = []
    bed_targets: list[float] = []

    envelope = policy.get("envelopeMm") if policy else None
    allowed_macros = set(policy.get("allowedMacros") or []) if policy else set()
    nozzle_range = policy.get("nozzleTemperatureRangeC") if policy else None
    bed_range = policy.get("bedTemperatureRangeC") if policy else None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        code = raw_line.split(";", 1)[0].strip()
        if not code:
            continue
        token = code.split(None, 1)[0].upper()

        if token in _FORBIDDEN_MACROS or token in _FORBIDDEN_M:
            _issue(issues, "forbidden_command", line_number, f"{token} is forbidden by the Workpiece validator.")
            continue

        if not token.startswith(("G", "M", "T")):
            observed_macros.add(token)
            if token not in allowed_macros:
                _issue(issues, "unapproved_macro", line_number, f"Macro {token} is not declared by the exact profiles.")
            if token == "START_PRINT" and policy:
                args = {name: float(value) for name, value in _MACRO_ARG_RE.findall(code)}
                for key in ("EXTRUDER_TEMP", "EXTRUDER_OTHER_LAYER_TEMP"):
                    if key in args:
                        nozzle_targets.append(args[key])
                if "BED_TEMP" in args:
                    bed_targets.append(args["BED_TEMP"])
            continue

        if token.startswith("T"):
            if token != "T0":
                _issue(issues, "unsupported_tool_change", line_number, "CP3 currently validates only single-tool FDM G-code.")
            continue

        if token.startswith("G"):
            if token not in _ALLOWED_G:
                _issue(issues, "unsupported_g_command", line_number, f"{token} can affect motion semantics and is not independently proven by CP3.")
                continue
            if token == "G21":
                continue
            if token == "G90":
                xyz_absolute = True
                continue
            if token == "G91":
                xyz_absolute = False
                continue
            if token == "G28":
                axes = {axis.upper() for axis, _ in _AXIS_RE.findall(code)} & {"X", "Y", "Z"}
                axes = axes or {"X", "Y", "Z"}
                if "X" in axes:
                    x = None
                if "Y" in axes:
                    y = None
                if "Z" in axes:
                    z = None
                continue
            values = {axis.upper(): float(value) for axis, value in _AXIS_RE.findall(code)}
            if token == "G92":
                if any(axis in values for axis in ("X", "Y", "Z")):
                    _issue(issues, "unsupported_coordinate_reset", line_number, "CP3 only permits G92 E resets; XYZ frame resets are not authority-safe yet.")
                if "E" in values:
                    e = values["E"]
                continue
            if token not in {"G0", "G1"}:
                continue

            def next_axis(current: float | None, axis: str) -> float | None:
                if axis not in values:
                    return current
                if xyz_absolute:
                    return values[axis]
                if current is None:
                    return None
                return current + values[axis]

            nx, ny, nz = next_axis(x, "X"), next_axis(y, "Y"), next_axis(z, "Z")
            ne = values.get("E", e) if e_absolute else e + values.get("E", 0.0)
            extruding = ne > e + 1e-9 and (nx != x or ny != y)
            if extruding:
                if None in (x, y, z, nx, ny, nz):
                    _issue(issues, "unknown_extrusion_coordinate", line_number, "Extrusion occurred before XYZ position was independently known.")
                elif envelope:
                    coords = ((float(x), float(y), float(z)), (float(nx), float(ny), float(nz)))
                    for point in coords:
                        if any(value < 0 or value > float(limit) for value, limit in zip(point, envelope, strict=True)):
                            _issue(issues, "extrusion_outside_envelope", line_number, "An extrusion move leaves the exact profile printable envelope.")
                            break
                    extrusion_count += 1
                    for point in coords:
                        for index, value in enumerate(point):
                            bounds_min[index] = min(bounds_min[index], value)
                            bounds_max[index] = max(bounds_max[index], value)
            x, y, z, e = nx, ny, nz, ne
            continue

        if token == "M82":
            e_absolute = True
        elif token == "M83":
            e_absolute = False
        elif token in {"M104", "M109", "M140", "M190"}:
            values = {axis.upper(): float(value) for axis, value in _AXIS_RE.findall(code)}
            if "S" in values:
                (nozzle_targets if token in {"M104", "M109"} else bed_targets).append(values["S"])
        elif token == "M200":
            _issue(issues, "unsupported_extrusion_units", line_number, "Volumetric extrusion mode is not independently supported by CP3.")

    if extrusion_count < 1:
        _issue(issues, "no_proven_extrusion", None, "No in-envelope extrusion path was independently proven.")

    def check_targets(name: str, targets: list[float], limits: Any) -> None:
        if not targets:
            return
        if not isinstance(limits, list) or len(limits) != 2:
            _issue(issues, f"missing_{name}_temperature_policy", None, f"Exact profiles did not provide {name} temperature bounds.")
            return
        low, high = float(limits[0]), float(limits[1])
        for target in targets:
            if target < low or target > high:
                _issue(issues, f"{name}_temperature_out_of_policy", None, f"Observed {name} target {target:g} C is outside exact-profile policy {low:g}-{high:g} C.")

    check_targets("nozzle", nozzle_targets, nozzle_range)
    check_targets("bed", bed_targets, bed_range)

    service_commit = _text(validator_service_commit).lower()
    if not _COMMIT_RE.fullmatch(service_commit):
        raise ValueError("Validator service commit must be an exact 40-character Git SHA.")
    if not _text(toolchain_ref):
        raise ValueError("CP3 validation requires an explicit toolchain reference for its receipt.")

    passed = not issues
    bounds = None if extrusion_count < 1 else {
        "min": [round(value, 6) for value in bounds_min],
        "max": [round(value, 6) for value in bounds_max],
    }
    return {
        "contractVersion": FDM_GCODE_VALIDATION_VERSION,
        "validator": {"name": VALIDATOR_NAME, "version": VALIDATOR_VERSION, "serviceCommit": service_commit},
        "passed": passed,
        "authorityCriticalComplete": passed,
        "gcodeSha256": actual_gcode_sha,
        "projectSha256": project_sha,
        "profileSha256": profile_hashes,
        "toolchainRef": _text(toolchain_ref),
        "sourceSha256": _sha(source.get("sha256")),
        "printerKey": printer_key,
        "policy": policy,
        "motion": {"extrusionSegmentCount": extrusion_count, "extrusionBoundsMm": bounds},
        "temperatures": {"nozzleTargetsC": nozzle_targets, "bedTargetsC": bed_targets},
        "observedMacros": sorted(observed_macros),
        "issues": issues,
    }
