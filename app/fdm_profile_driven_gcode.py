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


def _bed_setpoints(filament: Mapping[str, Any]) -> list[float]:
    values: set[float] = set()
    for key, raw in filament.items():
        if not key.endswith(_BED_KEYS):
            continue
        value = _finite(_first(raw))
        if value is not None and value > 0:
            values.add(value)
    if not values:
        raise ValueError("Exact filament profile exposes no positive bed-temperature setpoint.")
    return sorted(values)


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
    bed_setpoints = _bed_setpoints(filament)
    pressure_advance = _pressure_advance(filament)
    valid_pressure_lines: set[int] = set()
    observed_pressure: list[float] = []
    observed_generic_fans: list[dict[str, Any]] = []

    def add(code: str, line: int | None, message: str) -> None:
        issue: dict[str, Any] = {"code": code, "message": message}
        if line is not None:
            issue["line"] = line
        issues.append(issue)

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

    if valid_pressure_lines:
        issues = [
            issue for issue in issues
            if not (
                issue.get("code") == "unapproved_macro"
                and issue.get("line") in valid_pressure_lines
                and "SET_PRESSURE_ADVANCE" in str(issue.get("message"))
            )
        ]

    # Core temperature validation intentionally uses a conservative range. This
    # layer tightens bed authority to exact positive slicer setpoints and treats
    # zero as an explicit heater-off state rather than a print temperature.
    bed_targets = [float(value) for value in (result.get("temperatures") or {}).get("bedTargetsC") or []]
    nozzle_targets = [float(value) for value in (result.get("temperatures") or {}).get("nozzleTargetsC") or []]
    for target in bed_targets:
        if target == 0:
            continue
        if not any(abs(target - expected) <= 1e-9 for expected in bed_setpoints):
            add("bed_temperature_not_exact_setpoint", None, f"Observed bed target {target:g} C is not an exact positive filament-profile setpoint.")
    # Remove only the core's range failures for explicit shutdown targets.
    issues = [
        issue for issue in issues
        if not (
            issue.get("code") in {"bed_temperature_out_of_policy", "nozzle_temperature_out_of_policy"}
            and "target 0 C" in str(issue.get("message"))
        )
    ]

    result["issues"] = issues
    result["passed"] = not issues
    result["authorityCriticalComplete"] = not issues
    result["profileDriven"] = {
        "bedTemperatureSetpointsC": bed_setpoints,
        "heaterShutdownObserved": {
            "bed": any(value == 0 for value in bed_targets),
            "nozzle": any(value == 0 for value in nozzle_targets),
        },
        "pressureAdvanceProfileValue": pressure_advance,
        "observedPressureAdvance": observed_pressure,
        "declaredGenericFans": {name: sorted(values) for name, values in sorted(declared_fans.items())},
        "observedGenericFans": observed_generic_fans,
    }
    return result
