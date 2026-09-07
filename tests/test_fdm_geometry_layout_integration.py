import math
import zipfile
from pathlib import Path

from app.project_builder import inspect_project_3mf, repair_project_plate_layout


def make_box_project(path: Path, x: float, y: float, z: float) -> None:
    model = f'''<?xml version="1.0" encoding="UTF-8"?>
    <model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
      <resources>
        <object id="2" type="model"><mesh><vertices>
          <vertex x="0" y="0" z="0"/><vertex x="{x}" y="0" z="0"/>
          <vertex x="0" y="{y}" z="0"/><vertex x="{x}" y="{y}" z="0"/>
          <vertex x="0" y="0" z="{z}"/><vertex x="{x}" y="0" z="{z}"/>
          <vertex x="0" y="{y}" z="{z}"/><vertex x="{x}" y="{y}" z="{z}"/>
        </vertices></mesh></object>
      </resources>
      <build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/></build>
    </model>'''
    settings = '''<?xml version="1.0" encoding="UTF-8"?><config><plate/><assemble/></config>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/model_settings.config", settings)
        archive.writestr("Metadata/project_settings.config", "{}")


def test_retained_project_uses_controlled_45_degree_for_290mm_low_rod(tmp_path: Path):
    project = tmp_path / "diagonal.3mf"
    make_box_project(project, 290, 20, 20)
    result = repair_project_plate_layout(project, envelope_mm=(300, 300, 300))

    assert result["stability"]["diagonalInstanceCount"] == 1
    assert result["stability"]["warningCount"] == 0
    placement = result["placements"][0]
    assert placement["stability"]["code"] == "controlled_diagonal_orientation"
    assert max(placement["footprint_mm"][:2]) < 276
    assert math.isclose(placement["footprint_mm"][2], 20.0, abs_tol=1e-5)

    transform = [float(value) for value in inspect_project_3mf(project)["build_items"][0]["transform"].split()]
    assert not math.isclose(transform[0], 1.0, abs_tol=1e-6)
    assert math.isclose(abs(transform[0]), math.sqrt(0.5), abs_tol=1e-6)
    assert math.isclose(abs(transform[1]), math.sqrt(0.5), abs_tol=1e-6)


def test_retained_project_lays_down_tall_rod_without_diagonal_when_possible(tmp_path: Path):
    project = tmp_path / "laid-down.3mf"
    make_box_project(project, 20, 20, 240)
    result = repair_project_plate_layout(project, envelope_mm=(300, 300, 300))

    assert result["stability"]["adjustedInstanceCount"] == 1
    assert result["stability"]["diagonalInstanceCount"] == 0
    placement = result["placements"][0]
    assert placement["stability"]["initialTallSlender"] is True
    assert placement["stability"]["finalTallSlender"] is False
    assert placement["stability"]["code"] == "controlled_low_orientation"
    assert placement["footprint_mm"][2] <= 20.0 + 1e-5


def test_retained_project_preserves_warning_for_tall_non_rod_geometry(tmp_path: Path):
    project = tmp_path / "warning.3mf"
    make_box_project(project, 20, 60, 180)
    result = repair_project_plate_layout(project, envelope_mm=(300, 300, 300))

    assert result["stability"]["warningCount"] == 1
    assert result["stability"]["adjustedInstanceCount"] == 0
    placement = result["placements"][0]
    assert placement["stability"]["rodLike"] is False
    assert placement["stability"]["code"] == "tall_slender_print"
    assert result["warnings"] == [placement["stability"]["message"]]
