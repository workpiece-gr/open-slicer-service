import json
from pathlib import Path

from app.main import build_process_profile, parse_duration_seconds, parse_gcode_summary, sample_toolpath


def test_process_profile_maps_quality_and_strength(tmp_path: Path):
    base = tmp_path / "base.json"
    destination = tmp_path / "effective.json"
    base.write_text(json.dumps({"type": "process", "name": "Base", "layer_height": "0.2", "wall_loops": "2"}))
    result = build_process_profile(base, "fine", "load_bearing", destination)
    assert result["layer_height"] == "0.12"
    assert result["wall_loops"] == "5"
    assert result["sparse_infill_density"] == "20%"


def test_resolved_profile_matrix():
    profile_root = Path(__file__).parents[1] / "profiles"
    machine = json.loads((profile_root / "machine.json").read_text(encoding="utf-8"))
    assert machine["inherits"] == "RatRig V-Core 3 300 0.4 nozzle"
    expected = {
        profile_root / "machine.json": "machine",
        **{profile_root / "process" / f"{name}.json": "process" for name in ("pla", "petg", "pctg", "abs", "tpu")},
        **{profile_root / "filament" / f"{name}.json": "filament" for name in ("pla", "petg", "pctg", "abs", "tpu")},
    }
    for path, profile_type in expected.items():
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert profile["type"] == profile_type
        assert profile["inherits"]
        if profile_type in {"process", "filament"}:
            assert machine["inherits"] in profile["compatible_printers"]


def test_duration_parser():
    assert parse_duration_seconds("1h 2m 3s") == 3723
    assert parse_duration_seconds("45m 8s") == 2708


def test_gcode_summary_and_moves(tmp_path: Path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text(
        "; estimated printing time (normal mode) = 1h 2m 3s\n"
        "; total filament used [g] = 12.5\n"
        "; total layer number = 2\n"
        "G90\nM82\nG1 X0 Y0 Z0.2 E0\n; FEATURE: Outer wall\nG1 X10 Y0 E1\n"
        "; FEATURE: Sparse infill\nG1 X10 Y10 E2\n"
    )
    summary = parse_gcode_summary(gcode)
    preview = sample_toolpath(gcode, 10)
    assert summary == {"print_time_seconds": 3723, "filament_grams": 12.5, "layer_count": 2}
    assert preview["total_extrusion_moves"] == 2
    assert preview["features"] == ["Outer wall", "Sparse infill"]
