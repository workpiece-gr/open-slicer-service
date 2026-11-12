import json
from pathlib import Path

import pytest

from app.main import (
    absolute_first_layer_line_width,
    build_process_profile,
    machine_nozzle_diameter_mm,
    parse_duration_seconds,
    parse_gcode_summary,
    sample_toolpath,
)


def test_process_profile_maps_quality_and_strength(tmp_path: Path):
    base = tmp_path / "base.json"
    destination = tmp_path / "effective.json"
    base.write_text(json.dumps({"type": "process", "name": "Base", "layer_height": "0.2", "wall_loops": "2"}))
    result = build_process_profile(base, "fine", "load_bearing", destination)
    assert result["layer_height"] == "0.12"
    assert result["wall_loops"] == "5"
    assert result["sparse_infill_density"] == "20%"


def test_first_layer_line_width_percentage_becomes_millimetres_for_the_arranger():
    # OrcaSlicer's CLI arranger reads a "110%" first-layer width as 110 mm per skirt loop and
    # shrinks the arrange bed by 222 mm per side, so the generated process states the same
    # width in millimetres. Absolute widths are left alone.
    assert absolute_first_layer_line_width({"initial_layer_line_width": "110%"}, 0.4) == "0.44"
    assert absolute_first_layer_line_width({"initial_layer_line_width": "100%"}, 0.6) == "0.6"
    assert absolute_first_layer_line_width({"initial_layer_line_width": "0.42"}, 0.4) is None
    # Orca falls back to line_width when the first-layer width is unset or zero.
    assert absolute_first_layer_line_width({"initial_layer_line_width": "0", "line_width": "125%"}, 0.4) == "0.5"
    assert absolute_first_layer_line_width({"line_width": "0.45"}, 0.4) is None
    assert absolute_first_layer_line_width({}, 0.4) is None
    with pytest.raises(RuntimeError):
        absolute_first_layer_line_width({"initial_layer_line_width": "-5%"}, 0.4)
    with pytest.raises(RuntimeError):
        absolute_first_layer_line_width({"initial_layer_line_width": "abc%"}, 0.4)


def test_process_profile_resolves_percentage_width_against_the_machine_nozzle(tmp_path: Path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"type": "process", "name": "Base", "initial_layer_line_width": "110%", "line_width": "110%"}))
    machine = tmp_path / "machine.json"
    machine.write_text(json.dumps({"type": "machine", "nozzle_diameter": ["0.4"]}))
    nozzle = machine_nozzle_diameter_mm(machine)
    assert nozzle == 0.4
    result = build_process_profile(base, "balanced", "functional", tmp_path / "effective.json", nozzle_diameter_mm=nozzle)
    assert result["initial_layer_line_width"] == "0.44"
    # Every other width keeps its calibrated percentage form.
    assert result["line_width"] == "110%"
    # Without a nozzle the profile is copied unchanged (legacy callers).
    untouched = build_process_profile(base, "balanced", "functional", tmp_path / "legacy.json")
    assert untouched["initial_layer_line_width"] == "110%"


def test_machine_nozzle_diameter_refuses_unusable_profiles(tmp_path: Path):
    for payload in ({}, {"nozzle_diameter": []}, {"nozzle_diameter": ["0"]}, {"nozzle_diameter": ["nan"]}, {"nozzle_diameter": "x"}):
        machine = tmp_path / "machine.json"
        machine.write_text(json.dumps(payload))
        with pytest.raises(RuntimeError):
            machine_nozzle_diameter_mm(machine)
    machine = tmp_path / "machine.json"
    machine.write_text(json.dumps({"nozzle_diameter": "0.6"}))
    assert machine_nozzle_diameter_mm(machine) == 0.6


def test_committed_ratrig_profiles_need_the_width_resolution():
    # Names the failure mode the resolution guards: every committed RatRig process profile
    # states its first-layer width as a percentage, which the Orca CLI arranger misreads.
    profile_root = Path(__file__).parents[1] / "profiles"
    nozzle = machine_nozzle_diameter_mm(profile_root / "machine.json")
    assert nozzle == 0.4
    for name in ("pla", "petg", "pctg", "abs", "tpu"):
        profile = json.loads((profile_root / "process" / f"{name}.json").read_text(encoding="utf-8"))
        assert profile["initial_layer_line_width"].endswith("%"), name
        assert absolute_first_layer_line_width(profile, nozzle) == "0.44", name


def test_resolved_profile_matrix():
    profile_root = Path(__file__).parents[1] / "profiles"
    machine = json.loads((profile_root / "machine.json").read_text(encoding="utf-8"))
    assert machine["name"] == "RatRig V-Core 3 300 0.4 nozzle - Copy"
    assert machine["printer_settings_id"] == machine["name"]
    assert machine["printer_model"] == "RatRig V-Core 3 300"
    assert machine["from"] == "user"
    # OrcaSlicer 2.5.0 ignores silent_mode; the dual machine_max_* limits stay.
    assert "silent_mode" not in machine
    expected = {
        profile_root / "machine.json": "machine",
        **{profile_root / "process" / f"{name}.json": "process" for name in ("pla", "petg", "pctg", "abs", "tpu")},
        **{profile_root / "filament" / f"{name}.json": "filament" for name in ("pla", "petg", "pctg", "abs", "tpu")},
    }
    for path, profile_type in expected.items():
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert profile["type"] == profile_type
        # Flattened profiles: OrcaSlicer 2.5.0's CLI resolves `inherits` against an
        # installed vendor tree the isolated container never has, so none may inherit.
        assert "inherits" not in profile, path
        if profile_type in {"process", "filament"}:
            # Without `inherits`, the 2.5.0 CLI compares a reopened project's
            # compatible_printers against the embedded machine's own name.
            assert profile["compatible_printers"] == [machine["name"]]
        if profile_type == "process":
            # 2.5.0 enum value; 2.4.2 used "0".
            assert profile["enable_arc_fitting"] == "disabled"
        if profile_type == "filament":
            # 2.4.2 filled this from the parent chain; the 2.5.0 CLI dereferences it
            # without a default and segfaults when it is missing.
            assert profile["filament_diameter"] == ["1.75"]


def test_duration_parser():
    assert parse_duration_seconds("1h 2m 3s") == 3723
    assert parse_duration_seconds("45m 8s") == 2708


def test_gcode_summary_and_moves(tmp_path: Path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text(
        "; estimated printing time (normal mode) = 1h 2m 3s\n"
        "; total filament used [g] = 12.5\n"
        "; total layer number = 2\n"
        "G90\nM83\nG1 X0 Y0 Z0.2 E0\n; FEATURE: Outer wall\nG1 X10 Y0 E.5\n"
        "; FEATURE: Sparse infill\nG1 X10 Y10 E.5\n"
    )
    summary = parse_gcode_summary(gcode)
    preview = sample_toolpath(gcode, 10)
    assert summary == {"print_time_seconds": 3723, "filament_grams": 12.5, "layer_count": 2}
    assert preview["total_extrusion_moves"] == 2
    assert preview["features"] == ["Outer wall", "Sparse infill"]
