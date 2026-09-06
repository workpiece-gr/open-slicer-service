import hashlib
import json

from app.fdm_profile_driven_gcode import validate_profile_driven_gcode


def encoded(value: dict) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def run(gcode: bytes, *, declared_bed_zero: bool):
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
        "nozzle_temperature_range_high": ["230"],
        "hot_plate_temp": ["60"],
        "hot_plate_temp_initial_layer": ["60"],
    }
    if declared_bed_zero:
        filament_value["eng_plate_temp"] = ["0"]
        filament_value["eng_plate_temp_initial_layer"] = ["0"]
    filament = encoded(filament_value)
    profile_hashes = {
        "machine": hashlib.sha256(machine).hexdigest(),
        "process": hashlib.sha256(process).hexdigest(),
        "filament": hashlib.sha256(filament).hexdigest(),
    }
    generation = {
        "source": {"sha256": "1" * 64},
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": False},
        "profiles": {
            kind: {"identity": f"ratrig_vcore3_300:{kind}", "sha256": digest}
            for kind, digest in profile_hashes.items()
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
    return validate_profile_driven_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine,
        process_profile_bytes=process,
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="candidate://thermal-test",
    )


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_start_print_bed_zero_requires_exact_zero_profile_setpoint():
    gcode = b"G21\nG90\nM83\nSTART_PRINT EXTRUDER_TEMP=220 EXTRUDER_OTHER_LAYER_TEMP=220 BED_TEMP=0\nG1 X10 Y10 Z0.2\nG1 X20 Y10 E1\nEND_PRINT\n"
    rejected = run(gcode, declared_bed_zero=False)
    accepted = run(gcode, declared_bed_zero=True)
    assert "bed_temperature_zero_not_declared" in codes(rejected)
    assert accepted["passed"] is True
    assert accepted["profileDriven"]["allDeclaredBedTemperatureSetpointsC"] == [0.0, 60.0]


def test_explicit_zero_m_commands_are_shutdown_but_zero_start_nozzle_is_not():
    shutdown = b"G21\nG90\nM83\nSTART_PRINT EXTRUDER_TEMP=220 EXTRUDER_OTHER_LAYER_TEMP=220 BED_TEMP=60\nG1 X10 Y10 Z0.2\nG1 X20 Y10 E1\nM140 S0\nM104 S0\nEND_PRINT\n"
    invalid_start = b"G21\nG90\nM83\nSTART_PRINT EXTRUDER_TEMP=0 EXTRUDER_OTHER_LAYER_TEMP=220 BED_TEMP=60\nG1 X10 Y10 Z0.2\nG1 X20 Y10 E1\nEND_PRINT\n"
    shutdown_result = run(shutdown, declared_bed_zero=False)
    invalid_result = run(invalid_start, declared_bed_zero=False)
    assert shutdown_result["passed"] is True
    assert shutdown_result["profileDriven"]["heaterShutdownObserved"] == {"bed": True, "nozzle": True}
    assert "nozzle_temperature_zero_in_start" in codes(invalid_result)
