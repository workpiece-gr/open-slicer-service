"""Independent Workpiece G-code validator for FDM Authority v2 CP3.

The validator never calls OrcaSlicer. It consumes exact retained G-code bytes,
CP2 provenance receipts, and exact machine/process/filament profile bytes whose
SHA-256 values must match those receipts. Physical limits are derived only from
those exact profile bytes; no calibration or thermal limits are invented here.
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
_GCODE_TOKEN_RE = re.compile(r"^[GM]\d+$")
_TOOL_TOKEN_RE = re.compile(r"^T\d+$")
_AXIS_RE = re.compile(r"([XYZEFS])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", re.I)
_MACRO_ARG_RE = re.compile(r"\b([A-Z][A-Z0-9_]*)=([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\b")
_NAME_ARG_RE = re.compile(r"(?:^|\s)NAME=([A-Za-z0-9_.:+-]+)(?:\s|$)")
_CENTER_ARG_RE = re.compile(r"(?:^|\s)CENTER=([^\s]+)(?:\s|$)")
_POLYGON_ARG_RE = re.compile(r"(?:^|\s)POLYGON=(\[.*\])(?:\s|$)")

_ALLOWED_G = {"G0", "G1", "G4", "G10", "G11", "G21", "G28", "G90", "G91", "G92"}
_HANDLED_M = {"M73", "M82", "M83", "M104", "M106", "M109", "M140", "M190", "M200"}
_FORBIDDEN_M = {"M112", "M206", "M500", "M501", "M502", "M524", "M997", "M999"}
_FORBIDDEN_MACROS = {"FIRMWARE_RESTART", "RESTART", "RUN_SHELL_COMMAND", "SAVE_CONFIG"}
_KNOWN_EXTENDED = {"EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START", "EXCLUDE_OBJECT_END", "SET_VELOCITY_LIMIT"}
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


def _first_scalars(value: Any) -> list[float]:
    raw = value if isinstance(value, list) else [value]
    result: list[float] = []
    for item in raw:
        number = _finite(item)
        if number is not None:
            result.append(number)
    return result


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


def _is_macro(token: str) -> bool:
    return not _GCODE_TOKEN_RE.fullmatch(token) and not _TOOL_TOKEN_RE.fullmatch(token)


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
                if _is_macro(token):
                    result.add(token)
    return result


def _toolhead_acceleration_limit(machine: Mapping[str, Any]) -> float:
    values: list[float] = []
    for key in (
        "machine_max_acceleration_extruding",
        "machine_max_acceleration_retracting",
        "machine_max_acceleration_travel",
        "machine_max_acceleration_x",
        "machine_max_acceleration_y",
    ):
        values.extend(number for number in _first_scalars(machine.get(key)) if number > 0)
    if not values:
        raise ValueError("The machine profile does not expose exact positive toolhead acceleration limits.")
    return min(values)


def build_validation_policy(
    *,
    printer_key: str,
    machine_profile_bytes: bytes,
    process_profile_bytes: bytes,
    filament_profile_bytes: bytes,
) -> dict[str, Any]:
    """Build validator policy only from exact SHA-bound profile bytes."""

    machine = _profile_json(machine_profile_bytes, "machine")
    process = _profile_json(process_profile_bytes, "process")
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

    macros = _macro_tokens(machine) | _macro_tokens(process) | _macro_tokens(filament)
    if macros & _FORBIDDEN_MACROS:
        raise ValueError("An exact profile requests a macro that CP3 forbids for authority.")

    flavor = _text(machine.get("gcode_flavor")).lower()
    if flavor != "klipper":
        raise ValueError("CP3 currently provides independent motion-command semantics only for Klipper FDM profiles.")

    return {
        "printerKey": printer_key,
        "gcodeFlavor": flavor,
        "envelopeMm": [width, depth, height],
        "maxToolheadAccelerationMmS2": _toolhead_acceleration_limit(machine),
        "nozzleTemperatureRangeC": [nozzle_low, nozzle_high],
        "bedTemperatureRangeC": [min(bed_values), max(bed_values)],
        "excludeObjectAnnotations": str(process.get("exclude_object")).strip().lower() in {"1", "true"},
        "allowedMacros": sorted(macros),
    }


def _issue(issues: list[dict[str, Any]], code: str, line: int | None, message: str) -> None:
    entry: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        entry["line"] = line
    issues.append(entry)


def _letter_params(code: str) -> dict[str, float] | None:
    params: dict[str, float] = {}
    for raw in code.split()[1:]:
        if len(raw) < 2 or len(raw[0]) != 1 or not raw[0].isalpha() or not _NUMBER_RE.fullmatch(raw[1:]):
            return None
        key = raw[0].upper()
        if key in params:
            return None
        params[key] = float(raw[1:])
    return params


def _extended_numeric_params(code: str) -> dict[str, float] | None:
    params: dict[str, float] = {}
    for raw in code.split()[1:]:
        if "=" not in raw:
            return None
        name, value = raw.split("=", 1)
        name = name.upper()
        if not name or name in params or not _NUMBER_RE.fullmatch(value):
            return None
        params[name] = float(value)
    return params


def _object_name(code: str) -> str:
    match = _NAME_ARG_RE.search(code)
    return match.group(1) if match else ""


def _validate_object_definition(code: str, envelope: list[float] | None) -> tuple[str, str | None]:
    name = _object_name(code)
    center_match = _CENTER_ARG_RE.search(code)
    polygon_match = _POLYGON_ARG_RE.search(code)
    if not name or not center_match or not polygon_match:
        return name, "Object definition must contain NAME, CENTER, and POLYGON."
    try:
        center_values = [float(value) for value in center_match.group(1).split(",")]
        polygon = json.loads(polygon_match.group(1))
    except (ValueError, json.JSONDecodeError):
        return name, "Object definition contains malformed CENTER or POLYGON coordinates."
    if len(center_values) != 2 or not all(math.isfinite(value) for value in center_values):
        return name, "Object definition CENTER must contain two finite coordinates."
    if not isinstance(polygon, list) or len(polygon) < 3:
        return name, "Object definition POLYGON must contain at least three points."
    points: list[tuple[float, float]] = []
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2:
            return name, "Object definition POLYGON contains a malformed point."
        x, y = _finite(point[0]), _finite(point[1])
        if x is None or y is None:
            return name, "Object definition POLYGON contains a non-finite point."
        points.append((x, y))
    if envelope:
        width, depth = float(envelope[0]), float(envelope[1])
        if not (0 <= center_values[0] <= width and 0 <= center_values[1] <= depth):
            return name, "Object definition CENTER is outside the exact machine envelope."
        if any(x < 0 or x > width or y < 0 or y > depth for x, y in points):
            return name, "Object definition POLYGON is outside the exact machine envelope."
    return name, None


def validate_exact_gcode(
    *,
    artifact: Mapping[str, Any] | Any,
    generation_receipt: Mapping[str, Any] | Any,
    machine_profile_bytes: bytes,
    process_profile_bytes: bytes,
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
    engine = _record(generation.get("engine"))
    issues: list[dict[str, Any]] = []

    raw_bytes = artifact.get("bytes")
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        raise ValueError("CP3 requires the exact retained G-code bytes.")
    gcode_bytes = bytes(raw_bytes)
    actual_gcode_sha = _digest(gcode_bytes)
    if _sha(artifact.get("sha256")) != actual_gcode_sha:
        _issue(issues, "gcode_sha256_mismatch", None, "Exact G-code bytes do not match the CP2 artifact receipt.")

    project_sha = _sha(artifact.get("project_sha256"))
    generation_project_sha = _sha(project.get("sha256"))
    if not project_sha or project_sha != generation_project_sha:
        _issue(issues, "project_sha256_mismatch", None, "CP2 artifact and generation receipt disagree on the production 3MF.")
    source_sha = _sha(source.get("sha256"))
    if _sha(artifact.get("source_sha256")) != source_sha:
        _issue(issues, "source_sha256_mismatch", None, "CP2 artifact and generation receipt disagree on the immutable source.")
    if _text(artifact.get("orca_version")) != _text(engine.get("version")):
        _issue(issues, "orca_version_mismatch", None, "CP2 artifact Orca version differs from the generation receipt.")
    if _text(artifact.get("service_commit")).lower() != _text(engine.get("service_commit")).lower():
        _issue(issues, "service_commit_mismatch", None, "CP2 artifact service commit differs from the generation receipt.")

    profile_hashes: dict[str, str] = {}
    artifact_profiles = _record(artifact.get("profile_sha256"))
    exact_profile_bytes = {
        "machine": machine_profile_bytes,
        "process": process_profile_bytes,
        "filament": filament_profile_bytes,
    }
    for kind in _PROFILE_KINDS:
        expected = _sha(_record(profiles.get(kind)).get("sha256"))
        actual = _sha(artifact_profiles.get(kind))
        if not expected or actual != expected:
            _issue(issues, "profile_sha256_mismatch", None, f"{kind} profile hash is not bound to the generation receipt.")
        if expected:
            profile_hashes[kind] = expected
            if _digest(exact_profile_bytes[kind]) != expected:
                _issue(issues, f"{kind}_profile_bytes_mismatch", None, f"Exact {kind} profile bytes do not match the generation SHA-256.")

    printer_key = _text(printer.get("key"))
    if _text(artifact.get("printer_key")) != printer_key:
        _issue(issues, "printer_mismatch", None, "CP2 artifact printer key differs from the generation receipt.")

    policy: dict[str, Any] | None = None
    blocking = {f"{kind}_profile_bytes_mismatch" for kind in _PROFILE_KINDS}
    if not any(issue["code"] in blocking for issue in issues):
        try:
            policy = build_validation_policy(
                printer_key=printer_key,
                machine_profile_bytes=machine_profile_bytes,
                process_profile_bytes=process_profile_bytes,
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
    observed_commands: set[str] = set()
    nozzle_targets: list[float] = []
    bed_targets: list[float] = []
    fan_targets: list[float] = []
    progress_targets: list[float] = []
    velocity_accelerations: list[float] = []
    defined_objects: set[str] = set()
    active_object: str | None = None

    envelope = policy.get("envelopeMm") if policy else None
    allowed_macros = set(policy.get("allowedMacros") or []) if policy else set()
    nozzle_range = policy.get("nozzleTemperatureRangeC") if policy else None
    bed_range = policy.get("bedTemperatureRangeC") if policy else None
    max_acceleration = policy.get("maxToolheadAccelerationMmS2") if policy else None
    exclude_annotations = bool(policy and policy.get("excludeObjectAnnotations"))

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        code = raw_line.split(";", 1)[0].strip()
        if not code:
            continue
        token = code.split(None, 1)[0].upper()
        observed_commands.add(token)

        if token in _FORBIDDEN_MACROS or token in _FORBIDDEN_M:
            _issue(issues, "forbidden_command", line_number, f"{token} is forbidden by the Workpiece validator.")
            continue

        if token == "SET_VELOCITY_LIMIT":
            observed_macros.add(token)
            params = _extended_numeric_params(code)
            if params is None or set(params) != {"ACCEL"}:
                _issue(issues, "unsupported_velocity_limit_form", line_number, "CP3 currently permits only SET_VELOCITY_LIMIT ACCEL=<value>.")
                continue
            value = params["ACCEL"]
            velocity_accelerations.append(value)
            if value <= 0 or max_acceleration is None or value > float(max_acceleration) + 1e-9:
                _issue(issues, "acceleration_out_of_policy", line_number, "SET_VELOCITY_LIMIT acceleration exceeds the exact machine-profile toolhead limit.")
            continue

        if token == "EXCLUDE_OBJECT_DEFINE":
            observed_macros.add(token)
            if not exclude_annotations:
                _issue(issues, "unexpected_exclude_object_annotation", line_number, "Exact process profile does not enable exclude-object annotations.")
                continue
            name, error = _validate_object_definition(code, envelope)
            if error:
                _issue(issues, "invalid_exclude_object_definition", line_number, error)
            elif name in defined_objects:
                _issue(issues, "duplicate_exclude_object_definition", line_number, f"Object {name} is defined more than once.")
            else:
                defined_objects.add(name)
            continue

        if token == "EXCLUDE_OBJECT_START":
            observed_macros.add(token)
            name = _object_name(code)
            if not exclude_annotations or not name or name not in defined_objects:
                _issue(issues, "invalid_exclude_object_start", line_number, "EXCLUDE_OBJECT_START must reference a defined object enabled by the exact process profile.")
            elif active_object is not None:
                _issue(issues, "nested_exclude_object_start", line_number, "Exclude-object annotations may not overlap.")
            else:
                active_object = name
            continue

        if token == "EXCLUDE_OBJECT_END":
            observed_macros.add(token)
            name = _object_name(code)
            if not exclude_annotations or not name or active_object != name:
                _issue(issues, "invalid_exclude_object_end", line_number, "EXCLUDE_OBJECT_END must close the currently active defined object.")
            else:
                active_object = None
            continue

        if token in _KNOWN_EXTENDED:
            _issue(issues, "unhandled_extended_command", line_number, f"{token} reached an unhandled extended-command path.")
            continue

        if _is_macro(token):
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

        if _TOOL_TOKEN_RE.fullmatch(token):
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
                x = y = z = None
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
                return None if current is None else current + values[axis]

            nx, ny, nz = next_axis(x, "X"), next_axis(y, "Y"), next_axis(z, "Z")
            ne = values.get("E", e) if e_absolute else e + values.get("E", 0.0)
            extruding = ne > e + 1e-9 and (nx != x or ny != y)
            if extruding:
                if None in (x, y, z, nx, ny, nz):
                    _issue(issues, "unknown_extrusion_coordinate", line_number, "Extrusion occurred before XYZ position was independently known.")
                elif envelope:
                    coords = ((float(x), float(y), float(z)), (float(nx), float(ny), float(nz)))
                    outside = any(
                        any(value < 0 or value > float(limit) for value, limit in zip(point, envelope, strict=True))
                        for point in coords
                    )
                    if outside:
                        _issue(issues, "extrusion_outside_envelope", line_number, "An extrusion move leaves the exact profile printable envelope.")
                    else:
                        extrusion_count += 1
                        for point in coords:
                            for index, value in enumerate(point):
                                bounds_min[index] = min(bounds_min[index], value)
                                bounds_max[index] = max(bounds_max[index], value)
            x, y, z, e = nx, ny, nz, ne
            continue

        if token.startswith("M") and token not in _HANDLED_M:
            _issue(issues, "unsupported_m_command", line_number, f"{token} is not independently classified by CP3.")
            continue
        if token == "M82":
            e_absolute = True
        elif token == "M83":
            e_absolute = False
        elif token == "M73":
            params = _letter_params(code)
            if params is None or "P" not in params or set(params) - {"P", "R"}:
                _issue(issues, "invalid_progress_command", line_number, "M73 must contain bounded P progress and optional non-negative R remaining-time metadata.")
            else:
                progress = params["P"]
                remaining = params.get("R")
                if progress < 0 or progress > 100 or not progress.is_integer() or (remaining is not None and remaining < 0):
                    _issue(issues, "invalid_progress_command", line_number, "M73 progress metadata is outside the supported range.")
                else:
                    progress_targets.append(progress)
        elif token == "M106":
            params = _letter_params(code)
            if params is None or set(params) != {"S"} or params["S"] < 0 or params["S"] > 255:
                _issue(issues, "invalid_fan_command", line_number, "M106 must contain only S in the Klipper-supported 0-255 range.")
            else:
                fan_targets.append(params["S"])
        elif token in {"M104", "M109", "M140", "M190"}:
            values = {axis.upper(): float(value) for axis, value in _AXIS_RE.findall(code)}
            if "S" in values:
                (nozzle_targets if token in {"M104", "M109"} else bed_targets).append(values["S"])
        elif token == "M200":
            _issue(issues, "unsupported_extrusion_units", line_number, "Volumetric extrusion mode is not independently supported by CP3.")

    if active_object is not None:
        _issue(issues, "unclosed_exclude_object_annotation", None, f"Object annotation {active_object} was not closed.")
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
        "sourceSha256": source_sha,
        "printerKey": printer_key,
        "policy": policy,
        "motion": {"extrusionSegmentCount": extrusion_count, "extrusionBoundsMm": bounds},
        "temperatures": {"nozzleTargetsC": nozzle_targets, "bedTargetsC": bed_targets},
        "fanTargets": fan_targets,
        "progressTargets": progress_targets,
        "velocityLimitAccelerations": velocity_accelerations,
        "definedObjects": sorted(defined_objects),
        "observedMacros": sorted(observed_macros),
        "observedCommands": sorted(observed_commands),
        "issues": issues,
    }
