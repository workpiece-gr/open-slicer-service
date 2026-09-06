import hashlib
import json

from app.fdm_gcode_validator import build_validation_policy, validate_exact_gcode


def profile_bytes(value: dict) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def exact_profiles():
    machine = profile_bytes(
        {
            "type": "machine",
            "name": "Test RatRig",
            "gcode_flavor": "klipper",
            "printable_height": "300",
            "printable_area": ["0x0", "300x0", "300x300", "0x300"],
            "machine_start_gcode": "START_PRINT EXTRUDER_TEMP=220 BED_TEMP=60",
            "machine_end_gcode": "END_PRINT",
            "before_layer_change_gcode": "TIMELAPSE_TAKE_FRAME\nG92 E0",
        }
    )
    filament = profile_bytes(
        {
            "type": "filament",
            "name": "PLA",
            "nozzle_temperature_range_low": ["190"],
            "nozzle_temperature_range_high": ["230"],
            "hot_plate_temp": ["60"],
            "hot_plate_temp_initial_layer": ["60"],
            "filament_start_gcode": ["; none\n"],
        }
    )
    return machine, filament


def receipt_and_artifact(gcode: bytes):
    machine, filament = exact_profiles()
    profiles = {
        "machine": {"identity": "ratrig_vcore3_300:machine", "sha256": hashlib.sha256(machine).hexdigest()},
        "process": {"identity": "ratrig_vcore3_300:pla:balanced:functional:process", "sha256": "3" * 64},
        "filament": {"identity": "ratrig_vcore3_300:pla:filament", "sha256": hashlib.sha256(filament).hexdigest()},
    }
    generation = {
        "source": {"sha256": "1" * 64},
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": False},
        "profiles": profiles,
        "engine": {"name": "OrcaSlicer", "version": "2.4.2", "service_commit": "4" * 40},
        "project": {"sha256": "2" * 64},
    }
    artifact = {
        "bytes": gcode,
        "sha256": hashlib.sha256(gcode).hexdigest(),
        "project_sha256": "2" * 64,
        "source_sha256": "1" * 64,
        "printer_key": "ratrig_vcore3_300",
        "profile_sha256": {kind: value["sha256"] for kind, value in profiles.items()},
        "orca_version": "2.4.2",
        "service_commit": "4" * 40,
    }
    return machine, filament, generation, artifact


def validate(gcode: bytes):
    machine, filament, generation, artifact = receipt_and_artifact(gcode)
    return validate_exact_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine,
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="ghcr.io/workpiece-gr/open-slicer-service@sha256:" + "b" * 64,
    )


def good_gcode() -> bytes:
    return b"""; Orca test\nG21\nG90\nM83\nSTART_PRINT EXTRUDER_TEMP=220 EXTRUDER_OTHER_LAYER_TEMP=220 BED_TEMP=60\nG1 X10 Y10 Z0.2 F6000\nG1 X30 Y10 E1.2 F1200\nTIMELAPSE_TAKE_FRAME\nG92 E0\nG1 X30 Y30 E1.2\nEND_PRINT\n"""


def issue_codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_policy_is_derived_only_from_exact_profile_values():
    machine, filament = exact_profiles()
    policy = build_validation_policy(
        printer_key="ratrig_vcore3_300",
        machine_profile_bytes=machine,
        filament_profile_bytes=filament,
    )
    assert policy["envelopeMm"] == [300.0, 300.0, 300.0]
    assert policy["nozzleTemperatureRangeC"] == [190.0, 230.0]
    assert policy["bedTemperatureRangeC"] == [60.0, 60.0]
    assert policy["allowedMacros"] == ["END_PRINT", "START_PRINT", "TIMELAPSE_TAKE_FRAME"]


def test_independent_validator_passes_exact_profile_bound_gcode():
    result = validate(good_gcode())
    assert result["passed"] is True
    assert result["authorityCriticalComplete"] is True
    assert result["issues"] == []
    assert result["motion"]["extrusionSegmentCount"] == 2
    assert result["motion"]["extrusionBoundsMm"] == {"min": [10.0, 10.0, 0.2], "max": [30.0, 30.0, 0.2]}
    assert result["temperatures"]["nozzleTargetsC"] == [220.0, 220.0]
    assert result["temperatures"]["bedTargetsC"] == [60.0]
    assert result["observedMacros"] == ["END_PRINT", "START_PRINT", "TIMELAPSE_TAKE_FRAME"]
    assert {"G21", "G90", "M83", "START_PRINT", "G1", "TIMELAPSE_TAKE_FRAME", "G92", "END_PRINT"} <= set(result["observedCommands"])


def test_gcode_bytes_must_match_cp2_sha_receipt():
    machine, filament, generation, artifact = receipt_and_artifact(good_gcode())
    artifact["sha256"] = "f" * 64
    result = validate_exact_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine,
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="runtime-ref",
    )
    assert result["passed"] is False
    assert "gcode_sha256_mismatch" in issue_codes(result)


def test_exact_machine_and_filament_profile_bytes_are_hash_bound():
    machine, filament, generation, artifact = receipt_and_artifact(good_gcode())
    result = validate_exact_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine + b" ",
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="runtime-ref",
    )
    assert result["passed"] is False
    assert "machine_profile_bytes_mismatch" in issue_codes(result)


def test_cp2_source_orca_and_service_provenance_must_match():
    machine, filament, generation, artifact = receipt_and_artifact(good_gcode())
    artifact["source_sha256"] = "f" * 64
    artifact["orca_version"] = "0.0.0"
    artifact["service_commit"] = "e" * 40
    result = validate_exact_gcode(
        artifact=artifact,
        generation_receipt=generation,
        machine_profile_bytes=machine,
        filament_profile_bytes=filament,
        validator_service_commit="a" * 40,
        toolchain_ref="runtime-ref",
    )
    assert result["passed"] is False
    assert {"source_sha256_mismatch", "orca_version_mismatch", "service_commit_mismatch"} <= issue_codes(result)


def test_extrusion_outside_profile_envelope_fails_closed():
    result = validate(good_gcode().replace(b"X30 Y30", b"X301 Y30"))
    assert result["passed"] is False
    assert "extrusion_outside_envelope" in issue_codes(result)


def test_motion_semantics_not_implemented_by_cp3_fail_closed():
    result = validate(good_gcode().replace(b"G1 X30 Y30 E1.2", b"G2 X30 Y30 I10 J0 E1.2"))
    assert result["passed"] is False
    assert "unsupported_g_command" in issue_codes(result)


def test_xyz_g92_frame_reset_fails_closed():
    result = validate(good_gcode().replace(b"G92 E0", b"G92 X0 Y0 E0"))
    assert result["passed"] is False
    assert "unsupported_coordinate_reset" in issue_codes(result)


def test_unapproved_macro_fails_closed():
    result = validate(good_gcode().replace(b"TIMELAPSE_TAKE_FRAME", b"SOME_OTHER_MACRO"))
    assert result["passed"] is False
    assert "unapproved_macro" in issue_codes(result)


def test_unknown_numeric_m_command_fails_closed():
    result = validate(good_gcode().replace(b"END_PRINT", b"M73 P50\nEND_PRINT"))
    assert result["passed"] is False
    assert "unsupported_m_command" in issue_codes(result)
    assert "M73" in result["observedCommands"]


def test_profile_temperature_policy_is_enforced_without_invented_limits():
    result = validate(good_gcode().replace(b"EXTRUDER_TEMP=220", b"EXTRUDER_TEMP=250", 1))
    assert result["passed"] is False
    assert "nozzle_temperature_out_of_policy" in issue_codes(result)


def test_forbidden_state_changing_command_fails_closed():
    result = validate(good_gcode().replace(b"END_PRINT", b"SAVE_CONFIG\nEND_PRINT"))
    assert result["passed"] is False
    assert "forbidden_command" in issue_codes(result)
