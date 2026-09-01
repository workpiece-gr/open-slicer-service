import json
import struct
import zipfile
from pathlib import Path

from app.main import build_process_profile
from app.project_builder import (
    build_project_command,
    fits_axis_permutation,
    inspect_project_3mf,
    inspect_stl,
    patch_project_settings,
    repair_project_plate_layout,
    verify_project_command,
)


def write_binary_stl(path: Path):
    triangles = [
        ((0, 0, 0), (10, 0, 0), (0, 20, 30)),
        ((10, 0, 0), (10, 20, 30), (0, 20, 30)),
    ]
    with path.open("wb") as handle:
        handle.write(b"workpiece test".ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            handle.write(struct.pack("<12fH", 0, 0, 1, *a, *b, *c, 0))


def test_inspect_stl_and_machine_fit(tmp_path: Path):
    source = tmp_path / "part.stl"
    write_binary_stl(source)
    inspected = inspect_stl(source)
    assert inspected["encoding"] == "binary"
    assert inspected["triangle_count"] == 2
    assert inspected["dimensions_mm"] == [10.0, 20.0, 30.0]
    assert fits_axis_permutation(inspected["dimensions_mm"], (235, 235, 235))
    assert not fits_axis_permutation([236, 20, 20], (235, 235, 235))
    assert fits_axis_permutation([236, 20, 20], (300, 300, 300))


def test_project_commands_separate_editable_export_from_fresh_slice(tmp_path: Path):
    export = build_project_command(
        orca_bin=Path("/opt/orca/AppRun"),
        machine_profile=tmp_path / "machine.json",
        process_profile=tmp_path / "process.json",
        filament_profile=tmp_path / "filament.json",
        sources=[tmp_path / f"source-{index}.stl" for index in range(6)],
        project_path=tmp_path / "workpiece-production.3mf",
    )
    assert "--export-3mf" in export
    assert "--slice" not in export
    assert "--repetitions" not in export
    assert sum(str(value).endswith(".stl") for value in export) == 6
    assert "--orient" in export
    assert "--arrange" in export
    assert "--assemble" not in export

    single = build_project_command(
        orca_bin=Path("/opt/orca/AppRun"),
        machine_profile=tmp_path / "machine.json",
        process_profile=tmp_path / "process.json",
        filament_profile=tmp_path / "filament.json",
        sources=[tmp_path / "source-single.stl"],
        project_path=tmp_path / "workpiece-single.3mf",
    )
    assert "--assemble" in single
    assert sum(str(value).endswith(".stl") for value in single) == 1

    no_orient = build_project_command(
        orca_bin=Path("/opt/orca/AppRun"),
        machine_profile=tmp_path / "machine.json",
        process_profile=tmp_path / "process.json",
        filament_profile=tmp_path / "filament.json",
        sources=[tmp_path / "source-no-orient.stl"],
        project_path=tmp_path / "workpiece-no-orient.3mf",
        auto_orient=False,
    )
    orient_index = no_orient.index("--orient")
    assert no_orient[orient_index + 1] == "0"

    no_rotate = build_project_command(
        orca_bin=Path("/opt/orca/AppRun"),
        machine_profile=tmp_path / "machine.json",
        process_profile=tmp_path / "process.json",
        filament_profile=tmp_path / "filament.json",
        sources=[tmp_path / "source-no-rotate.stl"],
        project_path=tmp_path / "workpiece-no-rotate.3mf",
        auto_orient=False,
        allow_arrange_rotations=False,
    )
    assert "--allow-rotations=0" in no_rotate
    assert "--allow-rotations" not in no_rotate

    verify = verify_project_command(
        orca_bin=Path("/opt/orca/AppRun"),
        project_path=tmp_path / "workpiece-production.3mf",
        output_dir=tmp_path / "verify",
    )
    assert "--slice" in verify
    assert "--export-3mf" not in verify
    assert "--load-settings" not in verify
    assert "--load-filaments" not in verify


def test_inspect_project_counts_instances_and_embedded_settings(tmp_path: Path):
    project = tmp_path / "project.3mf"
    model = """<?xml version="1.0" encoding="UTF-8"?>
    <model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
      <resources><object id="1" type="model"><mesh /></object></resources>
      <build>
        <item objectid="1" />
        <item objectid="1" />
        <item objectid="1" />
      </build>
    </model>"""
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/project_settings.config", "{}")
        archive.writestr("Metadata/model_settings.config", "<config/>")
        archive.writestr("Metadata/machine_settings_1.json", "{}")
        archive.writestr("Metadata/process_settings_1.json", "{}")
        archive.writestr("Metadata/filament_settings_1.json", "{}")
    inspected = inspect_project_3mf(project)
    assert inspected["instance_count"] == 3
    assert len(inspected["build_items"]) == 3
    assert inspected["build_items"][0]["object_id"] == "1"
    assert inspected["plates"] == []
    assert inspected["embedded"]["project_settings"]
    assert inspected["embedded"]["model_settings"]


def test_automatic_project_supports_convert_manual_tree_support(tmp_path: Path):
    base = tmp_path / "base.json"
    destination = tmp_path / "effective.json"
    base.write_text(
        json.dumps(
            {
                "type": "process",
                "name": "Base",
                "layer_height": "0.2",
                "wall_loops": "2",
                "support_type": "tree(manual)",
                "support_on_build_plate_only": "1",
            }
        )
    )
    result = build_process_profile(
        base,
        "balanced",
        "functional",
        destination,
        material_label="PLA",
        automatic_supports=True,
        project_reopen_safe=True,
        single_colour_project=True,
    )
    assert result["enable_support"] == "1"
    assert result["support_type"] == "tree(auto)"
    assert result["support_on_build_plate_only"] == "0"
    assert result["enable_prime_tower"] == "0"
    assert "G92 E0" in result["layer_change_gcode"]


def test_project_reopen_safety_preserves_existing_layer_gcode(tmp_path: Path):
    base = tmp_path / "base-layer.json"
    destination = tmp_path / "effective-layer.json"
    base.write_text(
        json.dumps(
            {
                "type": "process",
                "name": "Base",
                "layer_height": "0.2",
                "wall_loops": "2",
                "layer_change_gcode": "M117 Layer change",
            }
        )
    )
    result = build_process_profile(
        base,
        "balanced",
        "functional",
        destination,
        project_reopen_safe=True,
    )
    assert result["layer_change_gcode"].startswith("M117 Layer change")
    assert result["layer_change_gcode"].strip().endswith("G92 E0")


def test_patch_project_settings_preserves_archive_and_updates_machine_structure(tmp_path: Path):
    project = tmp_path / "patch.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("Metadata/project_settings.config", json.dumps({"printer_structure": "i3", "printable_height": "300"}))
        archive.writestr("3D/3dmodel.model", "<model/>")
    patch_project_settings(project, {"printer_structure": "corexy"})
    with zipfile.ZipFile(project) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        assert settings["printer_structure"] == "corexy"
        assert settings["printable_height"] == "300"
        assert archive.read("3D/3dmodel.model") == b"<model/>"


def test_repair_project_plate_layout_centers_instances_and_builds_plate_membership(tmp_path: Path):
    project = tmp_path / "layout.3mf"
    model = """<?xml version="1.0" encoding="UTF-8"?>
    <model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
           xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
           requiredextensions="p">
      <resources>
        <object id="2" type="model"><mesh><vertices>
          <vertex x="0" y="0" z="0"/><vertex x="250" y="0" z="0"/>
          <vertex x="250" y="20" z="20"/><vertex x="0" y="20" z="20"/>
        </vertices></mesh></object>
        <object id="4" type="model"><mesh><vertices>
          <vertex x="0" y="0" z="0"/><vertex x="250" y="0" z="0"/>
          <vertex x="250" y="20" z="20"/><vertex x="0" y="20" z="20"/>
        </vertices></mesh></object>
      </resources>
      <build>
        <item objectid="2" transform="1 0 0 0 1 0 0 0 1 125 -70 0" printable="1"/>
        <item objectid="4" transform="1 0 0 0 1 0 0 0 1 125 -70 0" printable="1"/>
      </build>
    </model>"""
    settings = """<?xml version="1.0" encoding="UTF-8"?>
    <config>
      <object id="2"/><object id="4"/>
      <plate><metadata key="plater_id" value="1"/></plate>
      <assemble/>
    </config>"""
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/model_settings.config", settings)
        archive.writestr("Metadata/project_settings.config", "{}")
    result = repair_project_plate_layout(
        project,
        source_dimensions_mm=[250, 20, 20],
        envelope_mm=(300, 300, 300),
    )
    assert result["plate_count"] == 1
    assert [item["plate_index"] for item in result["placements"]] == [1, 1]
    inspected = inspect_project_3mf(project)
    assert inspected["instance_count"] == 2
    assert inspected["plates"][0]["model_instance_count"] == 2
    bounds = [item["placed_bounds_mm"] for item in result["placements"]]
    assert bounds[0][0][0] >= 0
    assert bounds[0][0][1] >= 0
    assert bounds[1][1][0] <= 300
    assert bounds[1][1][1] <= 300
