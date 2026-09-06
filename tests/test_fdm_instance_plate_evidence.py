import hashlib
import io
import json
import zipfile
from xml.etree import ElementTree

import pytest

from app.fdm_instance_plate_evidence import build_instance_plate_evidence


def profile_bytes(value: dict) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def machine_profile() -> bytes:
    return profile_bytes(
        {
            "type": "machine",
            "name": "RatRig test",
            "gcode_flavor": "klipper",
            "printable_height": "300",
            "printable_area": ["0x0", "300x0", "300x300", "0x300"],
        }
    )


def cube_vertices(size: float = 20.0):
    return [
        (0, 0, 0),
        (size, 0, 0),
        (size, size, 0),
        (0, size, 0),
        (0, 0, 10),
        (size, 0, 10),
        (size, size, 10),
        (0, size, 10),
    ]


def transform(tx: float, ty: float, tz: float = 0.0) -> str:
    return f"1 0 0 0 1 0 0 0 1 {tx} {ty} {tz}"


def make_project(*, metadata_object_ids=None) -> bytes:
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    model = ElementTree.Element(f"{{{core}}}model")
    resources = ElementTree.SubElement(model, f"{{{core}}}resources")
    obj = ElementTree.SubElement(resources, f"{{{core}}}object", {"id": "1", "type": "model"})
    mesh = ElementTree.SubElement(obj, f"{{{core}}}mesh")
    vertices = ElementTree.SubElement(mesh, f"{{{core}}}vertices")
    for x, y, z in cube_vertices():
        ElementTree.SubElement(vertices, f"{{{core}}}vertex", {"x": str(x), "y": str(y), "z": str(z)})
    triangles = ElementTree.SubElement(mesh, f"{{{core}}}triangles")
    for a, b, c in ((0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1)):
        ElementTree.SubElement(triangles, f"{{{core}}}triangle", {"v1": str(a), "v2": str(b), "v3": str(c)})
    build = ElementTree.SubElement(model, f"{{{core}}}build")
    for raw_transform in (transform(12, 12), transform(40, 12), transform(372, 12)):
        ElementTree.SubElement(build, f"{{{core}}}item", {"objectid": "1", "transform": raw_transform})

    settings = ElementTree.Element("config")
    ids = metadata_object_ids or {1: ["1", "1"], 2: ["1"]}
    identify = 10000
    for plate_id in (1, 2):
        plate = ElementTree.SubElement(settings, "plate")
        ElementTree.SubElement(plate, "metadata", {"key": "plater_id", "value": str(plate_id)})
        for object_id in ids[plate_id]:
            identify += 1
            instance = ElementTree.SubElement(plate, "model_instance")
            for key, value in (
                ("object_id", object_id),
                ("instance_id", "0"),
                ("identify_id", str(identify)),
            ):
                ElementTree.SubElement(instance, "metadata", {"key": key, "value": value})

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("3D/3dmodel.model", ElementTree.tostring(model, encoding="utf-8", xml_declaration=True))
        archive.writestr("Metadata/model_settings.config", ElementTree.tostring(settings, encoding="utf-8", xml_declaration=True))
    return buffer.getvalue()


def definition(name: str, low_x: float, low_y: float, high_x: float, high_y: float) -> str:
    center_x = (low_x + high_x) / 2
    center_y = (low_y + high_y) / 2
    polygon = [[low_x, low_y], [high_x, low_y], [high_x, high_y], [low_x, high_y], [low_x, low_y]]
    return f"EXCLUDE_OBJECT_DEFINE NAME={name} CENTER={center_x},{center_y} POLYGON={json.dumps(polygon, separators=(',', ':'))}"


def object_moves(name: str, x0: float, y0: float, x1: float, y1: float) -> list[str]:
    return [
        f"G1 X{x0} Y{y0} Z0.2 F6000",
        f"EXCLUDE_OBJECT_START NAME={name}",
        f"G1 X{x1} Y{y0} E1 F1200",
        f"G1 X{x1} Y{y1} E1",
        f"EXCLUDE_OBJECT_END NAME={name}",
    ]


def plate_gcode(objects) -> bytes:
    lines = ["G21", "G90", "M83"]
    lines.extend(definition(name, *bounds) for name, bounds in objects)
    for name, bounds in objects:
        lines.extend(object_moves(name, bounds[0], bounds[1], bounds[2], bounds[3]))
    return ("\n".join(lines) + "\n").encode()


def fixture(*, project_bytes=None, plate1=None, plate2=None, temporary_generic=False):
    project = project_bytes or make_project()
    project_sha = hashlib.sha256(project).hexdigest()
    machine = machine_profile()
    machine_sha = hashlib.sha256(machine).hexdigest()
    profile_hashes = {
        "machine": machine_sha,
        "process": "2" * 64,
        "filament": "3" * 64,
    }
    gcode = {
        1: plate1 or plate_gcode([("left", (12, 12, 32, 32)), ("right", (40, 12, 60, 32))]),
        2: plate2 or plate_gcode([("second_plate", (12, 12, 32, 32))]),
    }
    generation = {
        "source": {"sha256": "1" * 64},
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": temporary_generic},
        "profiles": {kind: {"identity": f"ratrig_vcore3_300:{kind}", "sha256": digest} for kind, digest in profile_hashes.items()},
        "engine": {"name": "OrcaSlicer", "version": "2.4.2", "service_commit": "4" * 40},
        "project": {"sha256": project_sha},
    }
    artifacts = []
    validations = {}
    for plate_id in (1, 2):
        payload = gcode[plate_id]
        digest = hashlib.sha256(payload).hexdigest()
        artifacts.append(
            {
                "plate_id": plate_id,
                "bytes": payload,
                "sha256": digest,
                "project_sha256": project_sha,
            }
        )
        names = []
        for line in payload.decode().splitlines():
            if line.startswith("EXCLUDE_OBJECT_DEFINE "):
                names.append(line.split("NAME=", 1)[1].split()[0])
        validations[plate_id] = {
            "passed": True,
            "authorityCriticalComplete": True,
            "gcodeSha256": digest,
            "projectSha256": project_sha,
            "profileSha256": profile_hashes,
            "definedObjects": names,
        }
    exact = {
        "project_sha256": project_sha,
        "generation_receipt": generation,
        "plates": artifacts,
    }
    return project, machine, exact, validations


def test_builds_unique_stable_instance_and_plate_evidence_despite_native_zero_instance_ids():
    project, machine, exact, validations = fixture()
    result = build_instance_plate_evidence(
        project_bytes=project,
        exact_gcode_result=exact,
        cp3_validations=validations,
        machine_profile_bytes=machine,
    )
    assert result["contractVersion"] == "fdm-instance-plate-evidence/1.0.0"
    assert result["authorityState"] == "evidence_candidate"
    assert result["totals"] == {"instanceCount": 3, "plateCount": 2}
    assert len({item["id"] for item in result["instances"]}) == 3
    assert [item["plateId"] for item in result["instances"]] == ["1", "1", "2"]
    assert [item["boundsMm"]["min"][:2] for item in result["instances"]] == [[12.0, 12.0], [40.0, 12.0], [12.0, 12.0]]
    assert all(item["gcodeEvidence"]["extrusionSegmentCount"] == 2 for item in result["instances"])
    assert result["plates"][0]["instanceIds"] == [result["instances"][0]["id"], result["instances"][1]["id"]]


def test_exact_project_bytes_must_match_cp2_receipt():
    project, machine, exact, validations = fixture()
    exact["project_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="do not match the CP2 project SHA"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_temporary_generic_printer_cannot_receive_cp4_production_instance_evidence():
    project, machine, exact, validations = fixture(temporary_generic=True)
    with pytest.raises(ValueError, match="non-generic RatRig"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_project_membership_metadata_must_reconcile_with_geometry():
    project = make_project(metadata_object_ids={1: ["1", "999"], 2: ["1"]})
    project, machine, exact, validations = fixture(project_bytes=project)
    with pytest.raises(ValueError, match="does not reconcile with model_instance object membership"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_every_instance_requires_exactly_one_gcode_definition():
    one_definition = plate_gcode([("left", (12, 12, 32, 32))])
    project, machine, exact, validations = fixture(plate1=one_definition)
    with pytest.raises(ValueError, match="project instances but 1 exact G-code object definitions"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_ambiguous_definition_geometry_fails_closed():
    duplicate_geometry = plate_gcode([("left_a", (12, 12, 32, 32)), ("left_b", (12, 12, 32, 32))])
    project, machine, exact, validations = fixture(plate1=duplicate_geometry)
    with pytest.raises(ValueError, match="does not map uniquely"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_definition_geometry_outside_serialization_tolerance_fails_closed():
    shifted = plate_gcode([("left", (12.2, 12, 32.2, 32)), ("right", (40, 12, 60, 32))])
    project, machine, exact, validations = fixture(plate1=shifted)
    with pytest.raises(ValueError, match="does not map uniquely"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_defined_object_without_extrusion_fails_closed():
    payload = ("\n".join([
        "G21",
        "G90",
        "M83",
        definition("left", 12, 12, 32, 32),
        definition("right", 40, 12, 60, 32),
        "G1 X12 Y12 Z0.2",
        "EXCLUDE_OBJECT_START NAME=left",
        "EXCLUDE_OBJECT_END NAME=left",
        *object_moves("right", 40, 12, 60, 32),
    ]) + "\n").encode()
    project, machine, exact, validations = fixture(plate1=payload)
    with pytest.raises(ValueError, match="has no proven extrusion activity"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_cp3_receipt_must_bind_to_exact_gcode_bytes():
    project, machine, exact, validations = fixture()
    validations[1]["gcodeSha256"] = "f" * 64
    with pytest.raises(ValueError, match="not bound to exact CP2 bytes"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )


def test_cp3_defined_object_set_must_match_exact_gcode():
    project, machine, exact, validations = fixture()
    validations[1]["definedObjects"] = ["left"]
    with pytest.raises(ValueError, match="does not match exact G-code definitions"):
        build_instance_plate_evidence(
            project_bytes=project,
            exact_gcode_result=exact,
            cp3_validations=validations,
            machine_profile_bytes=machine,
        )
