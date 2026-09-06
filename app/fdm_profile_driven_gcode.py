"""Profile-driven CP3 validation layered over the independent G-code core.

This module handles commands whose authority depends on exact filament/profile
settings rather than generic Klipper motion semantics. It never invokes Orca.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Mapping

from .fdm_gcode_validator import validate_exact_gcode

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_BED_KEYS = ("_plate_temp", "_plate_temp_initial_layer")


def _profile(payload: bytes, kind: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Exact {kind} profile bytes are not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict) or value.get("type") != kind:
        raise ValueError(f"Exact {kind} profile must declare type={kind}.")
    return value


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _named_params(code: str) -> dict[str, str] | None:
    params: dict[str, str] = {}
    for raw in code.split()[1:]:
        if "=" not in raw:
            return None
        name, value = raw.split("=", 1)
        name = name.upper()
        if not name or name in params or not value:
            return None
        params[name] = value
    return params


def _letter_params(code: str) -> dict[str, float] | None:
    params: dict[str, float] = {}
    for raw in code.split()[1:]:
        if len(raw) < 2 or not raw[0].isalpha() or not _NUMBER.fullmatch(raw[1:]):
            return None
        name = raw[0].upper()
        if name in params:
            return None
        params[name] = float(raw[1:])
    return params


def _declared_generic_fan_commands(*profiles: Mapping[str, Any]) -> dict[str, set[float]]:
    result: dict[str, set[float]] = {}
    script_keys = (
        "machine_start_gcode", "machine_end_gcode", "before_layer_change_gcode", "layer_change_gcode",
        "filament_start_gcode", "filament_end_gcode",
    )
    for profile in profiles:
        for key in script_keys:
            raw = profile.get(key)
            blocks = raw if isinstance(raw, list) else [raw]
            for block in blocks:
                if not isinstance(block, str):
                    continue
                for line in block.splitlines():
                    code = line.split(";", 1)[0].strip()
                    if not code or code.split(None, 1)[0].upper() != "SET_FAN_SPEED":
                        continue
                    params = _named_params(code)
                    if params is None or set(params) != {"FAN", "SPEED"}:
                        continue
                    speed = _finite(params["SPEED"])
                    if speed is None or speed < 0 or speed > 1:
                        continue
                    result.setdefault(params["FAN"], set()).add(speed)
    return result


def _bed_setpoints(filament: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    all_values: set[float] = set()
    for key, raw in filament.items():
        if not key.endswith(_BED_KEYS):
            continue
        value = _finite(_first(raw))
        if value is not None and value >= 0:
            all_values.add(value)
    positive = sorted(value for value in all_values if value > 0)
    if not positive:
        raise ValueError("Exact filament profile exposes no positive bed-temperature setpoint.")
    return positive, sorted(all_values)


def _pressure_advance(filament: Mapping[str, Any]) -> float | None:
    enabled = str(_first(filament.get("enable_pressure_advance"))).strip().lower() in {"1", "true"}
    if not enabled:
        return None
    value = _finite(_first(filament.get("pressure_advance")))
    if value is None or value < 0:
        raise ValueError("Pressure advance is enabled but the exact filament profile has no valid value.")
    return value


def validate_profile_driven_gcode(
    *,
    artifact: Mapping[str, Any] | Any,
    generation_receipt: Mapping[str, Any] | Any,
    machine_profile_bytes: bytes,
    process_profile_bytes: bytes,
    filament_profile_bytes: bytes,
    validator_service_commit: str,
    toolchain_ref: str,
) -> dict[str, Any]:
    """Run the CP3 core and prove material/profile-driven command semantics."""

    machine = _profile(machine_profile_bytes, "machine")
    process = _profile(process_profile_bytes, "process")
    filament = _profile(filament_profile_bytes, "filament")
    result = copy.deepcopy(validate_exact_gcode(
        artifact=artifact,
        generation_receipt=generation_receipt,
        machine_profile_bytes=machine_profile_bytes,
        process_profile_bytes=process_profile_bytes,
        filament_profile_bytes=filament_profile_bytes,
        validator_service_commit=validator_service_commit,
        toolchain_ref=toolchain_ref,
    ))

    raw = artifact.get("bytes") if isinstance(artifact, Mapping) else None
    if not isinstance(raw, (bytes, bytearray)):
        return result
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return result

    issues: list[dict[str, Any]] = list(result.get("issues") or [])
    declared_fans = _declared_generic_fan_commands(machine, process, filament)
    positive_bed_setpoints, all_bed_setpoints = _bed_setpoints(filament)
    pressure_advance = _pressure_advance(filament)
    valid_pressure_lines: set[int] = set()
    observed_pressure: list[float] = []
    observed_generic_fans: list[dict[str, Any]] = []
    explicit_bed_shutdown = False
    explicit_nozzle_shutdown = False
    valid_zero_bed_start = False
    invalid_zero_bed_start = False
    invalid_zero_nozzle_start = False

    def add(code: str, line: int | None, message: str) -> None:
        issue: dict[str, Any] = {"code": code, "message": message}
        if line is not None:
            issue["line"] = line
        issues.append(issue)

    def check_bed_target(target: float, line_number: int, *, from_start_macro: bool) -> None:
        nonlocal explicit_bed_shutdown, valid_zero_bed_start, invalid_zero_bed_start
        if target == 0:
            if from_start_macro:
                if 0.0 in all_bed_setpoints:
                    valid_zero_bed_start = True
                else:
                    invalid_zero_bed_start = True
                    add(
                        "bed_temperature_zero_not_declared",
                        line_number,
                        "START_PRINT BED_TEMP=0 is only authority-safe when zero is an exact filament-profile bed setpoint.",
                    )
            else:
                explicit_bed_shutdown = True
            return
        if not any(abs(target - expected) <= 1e-9 for expected in positive_bed_setpoints):
            add(
                "bed_temperature_not_exact_setpoint",
                line_number,
                f"Observed bed target {target:g} C is not an exact positive filament-profile setpoint.",
            )

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        code = raw_line.split(";", 1)[0].strip()
        if not code:
            continue
        token = code.split(None, 1)[0].upper()
        if token == "SET_PRESSURE_ADVANCE":
            params = _named_params(code)
            value = _finite(params.get("ADVANCE")) if params else None
            if (
                pressure_advance is None
                or params is None
                or set(params) != {"ADVANCE"}
                or value is None
                or abs(value - pressure_advance) > 1e-9
            ):
                add(
                    "pressure_advance_out_of_policy",
                    line_number,
                    "SET_PRESSURE_ADVANCE must exactly match the SHA-bound filament profile ADVANCE value and may not add unproven parameters.",
                )
            else:
                valid_pressure_lines.add(line_number)
                observed_pressure.append(value)
        elif token == "SET_FAN_SPEED":
            params = _named_params(code)
            fan = params.get("FAN") if params else None
            speed = _finite(params.get("SPEED")) if params else None
            allowed = declared_fans.get(fan or "", set())
            if params is None or set(params) != {"FAN", "SPEED"} or speed is None or speed not in allowed:
                add(
                    "generic_fan_out_of_policy",
                    line_number,
                    "SET_FAN_SPEED must exactly match a bounded FAN/SPEED pair declared by the SHA-bound profiles.",
                )
            else:
                observed_generic_fans.append({"fan": fan, "speed": speed})
        elif token == "START_PRINT":
            params = _named_params(code)
            if params:
                bed = _finite(params.get("BED_TEMP"))
                if bed is not None:
                    check_bed_target(bed, line_number, from_start_macro=True)
                for name in ("EXTRUDER_TEMP", "EXTRUDER_OTHER_LAYER_TEMP"):
                    nozzle = _finite(params.get(name))
                    if nozzle == 0:
                        invalid_zero_nozzle_start = True
                        add(
                            "nozzle_temperature_zero_in_start",
                            line_number,
                            f"START_PRINT {name}=0 cannot be treated as a heater-shutdown command.",
                        )
        elif token in {"M140", "M190"}:
            params = _letter_params(code)
            if params and "S" in params:
                check_bed_target(params["S"], line_number, from_start_macro=False)
        elif token in {"M104", "M109"}:
            params = _letter_params(code)
            if params and params.get("S") == 0:
                explicit_nozzle_shutdown = True

    if valid_pressure_lines:
        issues = [
            issue for issue in issues
            if not (
                issue.get("code") == "unapproved_macro"
                and issue.get("line") in valid_pressure_lines
                and "SET_PRESSURE_ADVANCE" in str(issue.get("message"))
            )
        ]

    # The core deliberately treats every zero as outside an active print range.
    # Remove that generic finding only when this layer has proven the zero came
    # from an explicit heater-off command or from a zero bed setpoint declared
    # by the exact filament profile. Invalid start-macro zeroes retain/further
    # add fail-closed findings.
    filtered: list[dict[str, Any]] = []
    for issue in issues:
        code = issue.get("code")
        message = str(issue.get("message"))
        if code == "bed_temperature_out_of_policy" and "target 0 C" in message:
            if (explicit_bed_shutdown or valid_zero_bed_start) and not invalid_zero_bed_start:
                continue
        if code == "nozzle_temperature_out_of_policy" and "target 0 C" in message:
            if explicit_nozzle_shutdown and not invalid_zero_nozzle_start:
                continue
        filtered.append(issue)
    issues = filtered

    result["issues"] = issues
    result["passed"] = not issues
    result["authorityCriticalComplete"] = not issues
    result["profileDriven"] = {
        "bedTemperatureSetpointsC": positive_bed_setpoints,
        "allDeclaredBedTemperatureSetpointsC": all_bed_setpoints,
        "heaterShutdownObserved": {
            "bed": explicit_bed_shutdown,
            "nozzle": explicit_nozzle_shutdown,
        },
        "pressureAdvanceProfileValue": pressure_advance,
        "observedPressureAdvance": observed_pressure,
        "declaredGenericFans": {name: sorted(values) for name, values in sorted(declared_fans.items())},
        "observedGenericFans": observed_generic_fans,
    }
    return result
