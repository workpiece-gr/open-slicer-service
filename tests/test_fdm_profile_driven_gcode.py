import hashlib
import json

from app.fdm_profile_driven_gcode import validate_profile_driven_gcode


def encoded(value: dict) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def profiles(*, pressure=None, fan_script=False, bed=(60,)):
    machine = encoded({
        "type": "machine",
        "gcode_flavor": "klipper",
        "printable_height": "300",
        "printable_area": ["0x0", "300x0", "300x300", "0x300"],
        "machine_max_acceleration_extruding": ["9000"],
        "machine_max_acceleration_retracting": ["9000"],
        "machine_max_acceleration_travel": ["9000"],
        "machine_max_acceleration_x": ["9000"],
        "machine_max_acceleration_y": ["9000"],
        "machine_start_gcode": "START_PRINT EXTRUDER_TEMP=220 BED_TEMP=60",
        "machine_end_gcode": "END_PRINT",
    })
    process = encoded({"type": "process", "exclude_object": "0"})
    filament_value = {
        "type": "filament",
        "nozzle_temperature_range_low": ["190"],
        "nozzle_temperature_range_high": ["260"],
        "hot_plate_temp": [str(bed[0])],
        "hot_plate_temp_initial_layer": [str(bed[0])],
    }
    for index, value in enumerate(bed[1:], start=1):
        filament_value[f"test_{index}_plate_temp"] = [str(value)]
    if pressure is not None:
        filament_value["enable_pressure_advance"] = ["1"]
        filament_value["pressure_advance"] = [str(pressure)]
    if fan_script:
        filament_value["filament_start_gcode"] = ["SET_FAN_SPEED FAN=bentobox SPEED=1"]
        filament_value["filament_end_gcode"] = ["SET_FAN_SPEED FAN=bentobox SPEED=0"]
    filament = encoded(filament_value)
    return machine, process, filament


def evidence(gcode: bytes, *, pressure=None, fan_script=False, bed=(60,)):
    machine, process, filament = profiles(pressure=pressure, fan_script=fan_script, bed=bed)
    profile_hashes = {
        "machine": hashlib.sha256(machine).hexdigest(),
        "process": hashlib.sha256(process).hexdigest(),
        "filament": hashlib.sha256(filament).hexdigest(),
    }
    generation = {
        "source": {"sha256": "1" * 64},
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": False},
        "profiles": {
            "machine": {"identity": "ratrig_vcore3_300:machine", "sha256": profile_hashes["machine"]},
            "process": {"identity": "ratrig_vcore3_300:test:balanced:functional:process", "sha256": profile_hashes["process"]},
            "filament": {"identity": "ratrig_vcore3_300:test:filament", "sha256": profile_hashes["filament"]},
        },
        "engine": {"name": "OrcaSlicer", "version": "2.4.2", "service_commit": "4" * 40},
        "project": {"sha256": "2" * 64},
    }
    artifact = {
        "bytes": gcode,
        "sha256": hashlib.sha256(gcode).hexdigest(),
        "project_sha256": "2" * 64,
        "source_sha256": "1" * 64,
        "printer_key": "ratrig_vcore3_300",
        "profile_sha256": profile_hashes,
        "orca_version": "2.4.2",
        "service_commit": "4" * 40,
    }
    return machine, process, filament, generation, artifact


def base_gcode(extra: bytes = b"", *, bed=60) -> bytes:
    return (
        b"G21\nG90\nM83\n"
        + f"START_PRINT EXTRUDER_TEMP=220 EXTRUDER_OTHER_LAYER_TEMP=220 BED_TEMP={bed}\n".encode()
        + extra
        + b"G1 X10 Y10 Z0.2 F6000\nG1 X30 Y10 E1.2 F1200\nEND_PRINT\n"
    )


def validate(gcode: bytes, **profile_kwargs):
    machine, process, filament, generation, artifact = evidence(gcode, **profile_kwargs)
    return validate_profile_driven_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine,
        process_profile_bytes=process,
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="candidate://cp3-test",
    )


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_exact_pressure_advance_value_can_clear_core_unapproved_macro_issue():
    result = validate(base_gcode(b"SET_PRESSURE_ADVANCE ADVANCE=0.04\n"), pressure=0.04)
    assert result["passed"] is True
    assert result["profileDriven"]["pressureAdvanceProfileValue"] == 0.04
    assert result["profileDriven"]["observedPressureAdvance"] == [0.04]


def test_pressure_advance_must_exactly_match_sha_bound_filament_value():
    result = validate(base_gcode(b"SET_PRESSURE_ADVANCE ADVANCE=0.05\n"), pressure=0.04)
    assert result["passed"] is False
    assert "pressure_advance_out_of_policy" in codes(result)


def test_pressure_advance_is_rejected_when_profile_does_not_enable_it():
    result = validate(base_gcode(b"SET_PRESSURE_ADVANCE ADVANCE=0.04\n"))
    assert result["passed"] is False
    assert "pressure_advance_out_of_policy" in codes(result)


def test_exact_declared_generic_fan_commands_pass_with_bounded_speed():
    gcode = base_gcode(
        b"SET_FAN_SPEED FAN=bentobox SPEED=1\nSET_FAN_SPEED FAN=bentobox SPEED=0\n"
    )
    result = validate(gcode, fan_script=True)
    assert result["passed"] is True
    assert result["profileDriven"]["declaredGenericFans"] == {"bentobox": [0.0, 1.0]}
    assert result["profileDriven"]["observedGenericFans"] == [
        {"fan": "bentobox", "speed": 1.0},
        {"fan": "bentobox", "speed": 0.0},
    ]


def test_generic_fan_name_or_speed_not_declared_by_exact_profile_fails_closed():
    wrong_fan = validate(base_gcode(b"SET_FAN_SPEED FAN=other SPEED=1\n"), fan_script=True)
    wrong_speed = validate(base_gcode(b"SET_FAN_SPEED FAN=bentobox SPEED=0.5\n"), fan_script=True)
    assert "generic_fan_out_of_policy" in codes(wrong_fan)
    assert "generic_fan_out_of_policy" in codes(wrong_speed)


def test_bed_target_must_be_an_exact_positive_profile_setpoint_not_merely_inside_range():
    result = validate(base_gcode(bed=70), bed=(60, 80))
    assert result["passed"] is False
    assert "bed_temperature_not_exact_setpoint" in codes(result)
    assert result["profileDriven"]["bedTemperatureSetpointsC"] == [60.0, 80.0]


def test_zero_heater_target_is_treated_as_shutdown_not_print_temperature():
    result = validate(base_gcode(b"M140 S0\nM104 S0\n"), bed=(60, 80))
    assert result["passed"] is True
    assert result["profileDriven"]["heaterShutdownObserved"] == {"bed": True, "nozzle": True}
