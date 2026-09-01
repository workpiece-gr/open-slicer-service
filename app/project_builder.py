"""Helpers for the experimental editable Orca project-3MF pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounds_from_vertices(vertices):
    iterator = iter(vertices)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise ValueError("The STL does not contain any vertices.") from exc
    mins = [first[0], first[1], first[2]]
    maxs = [first[0], first[1], first[2]]
    count = 1
    for vertex in iterator:
        count += 1
        for axis in range(3):
            mins[axis] = min(mins[axis], vertex[axis])
            maxs[axis] = max(maxs[axis], vertex[axis])
    dims = [maxs[index] - mins[index] for index in range(3)]
    if count < 3 or any(not math.isfinite(value) for value in (*mins, *maxs, *dims)):
        raise ValueError("The STL contains invalid coordinates.")
    if any(value <= 0 for value in dims):
        raise ValueError("The STL has a zero-size bounding box.")
    return {
        "min": [round(value, 6) for value in mins],
        "max": [round(value, 6) for value in maxs],
        "dimensions_mm": [round(value, 6) for value in dims],
        "vertex_count": count,
    }


def inspect_stl(path: Path) -> dict:
    """Validate an STL enough for routing and return a conservative axis-aligned envelope."""
    size = path.stat().st_size
    if size < 84:
        raise ValueError("The STL is empty or incomplete.")

    with path.open("rb") as handle:
        header = handle.read(84)
        triangle_count = struct.unpack("<I", header[80:84])[0]
        expected = 84 + triangle_count * 50
        if triangle_count > 0 and expected == size:
            vertices = []
            for _ in range(triangle_count):
                record = handle.read(50)
                if len(record) != 50:
                    raise ValueError("The binary STL ended unexpectedly.")
                values = struct.unpack("<12fH", record)
                vertices.extend((values[3:6], values[6:9], values[9:12]))
            bounds = _bounds_from_vertices(vertices)
            return {
                "encoding": "binary",
                "triangle_count": triangle_count,
                **bounds,
            }

    text = path.read_text(encoding="utf-8", errors="ignore")
    vertex_re = re.compile(
        r"\bvertex\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
        re.I,
    )
    vertices = [tuple(float(value) for value in match.groups()) for match in vertex_re.finditer(text)]
    if len(vertices) < 3 or len(vertices) % 3:
        raise ValueError("The STL is neither a valid binary STL nor a supported ASCII STL.")
    bounds = _bounds_from_vertices(vertices)
    return {
        "encoding": "ascii",
        "triangle_count": len(vertices) // 3,
        **bounds,
    }


def fits_axis_permutation(dimensions_mm: list[float], envelope_mm: tuple[float, float, float]) -> bool:
    part = sorted(float(value) for value in dimensions_mm)
    bed = sorted(float(value) for value in envelope_mm)
    return all(value <= limit + 1e-6 for value, limit in zip(part, bed, strict=True))


def inspect_project_3mf(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        raise ValueError("Orca did not produce a valid 3MF archive.")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        model_name = "3D/3dmodel.model"
        if model_name not in names:
            raise ValueError("The exported 3MF does not contain 3D/3dmodel.model.")
        root = ElementTree.fromstring(archive.read(model_name))
        build_nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "item"]
        build_items = [
            {
                "object_id": node.attrib.get("objectid"),
                "transform": node.attrib.get("transform"),
                "printable": node.attrib.get("printable"),
            }
            for node in build_nodes
        ]
        plates = []
        model_settings_name = "Metadata/model_settings.config"
        if model_settings_name in names:
            settings_root = ElementTree.fromstring(archive.read(model_settings_name))
            for plate_node in (node for node in settings_root if node.tag.rsplit("}", 1)[-1] == "plate"):
                metadata = {}
                instances = []
                for child in plate_node:
                    local = child.tag.rsplit("}", 1)[-1]
                    if local == "metadata":
                        metadata[child.attrib.get("key", "")] = child.attrib.get("value")
                    elif local == "model_instance":
                        instance_metadata = {
                            grandchild.attrib.get("key", ""): grandchild.attrib.get("value")
                            for grandchild in child
                            if grandchild.tag.rsplit("}", 1)[-1] == "metadata"
                        }
                        instances.append(instance_metadata)
                plates.append(
                    {
                        "plater_id": metadata.get("plater_id"),
                        "model_instance_count": len(instances),
                        "model_instances": instances,
                    }
                )
        embedded = {
            "project_settings": "Metadata/project_settings.config" in names,
            "model_settings": model_settings_name in names,
            "machine_presets": sorted(name for name in names if name.startswith("Metadata/machine_settings_")),
            "process_presets": sorted(name for name in names if name.startswith("Metadata/process_settings_")),
            "filament_presets": sorted(name for name in names if name.startswith("Metadata/filament_settings_")),
        }
        return {
            "instance_count": len(build_items),
            "build_items": build_items,
            "plates": plates,
            "embedded": embedded,
            "entries": len(names),
        }


def patch_project_settings(path: Path, updates: dict[str, object]) -> None:
    """Rewrite only Orca's flattened project settings inside an editable 3MF."""
    if not zipfile.is_zipfile(path):
        raise ValueError("Cannot patch settings in a non-3MF archive.")
    temporary = path.with_name(path.name + ".patched")
    found = False
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "Metadata/project_settings.config":
                    try:
                        settings = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError("The project 3MF contains invalid project settings.") from exc
                    settings.update(updates)
                    payload = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                    found = True
                target.writestr(info, payload)
        if not found:
            raise ValueError("The project 3MF does not contain Metadata/project_settings.config.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_project_plate_layout(
    path: Path,
    *,
    source_dimensions_mm: list[float],
    envelope_mm: tuple[float, float, float],
    margin_mm: float = 5.0,
    gap_mm: float = 8.0,
) -> dict:
    """Repair Orca 2.4.2 CLI plate placement while preserving its orientation matrices."""
    if len(source_dimensions_mm) != 3 or any(float(value) <= 0 for value in source_dimensions_mm):
        raise ValueError("Source dimensions are invalid for project layout repair.")
    if not zipfile.is_zipfile(path):
        raise ValueError("Cannot repair layout in a non-3MF archive.")

    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        payloads = {info.filename: archive.read(info.filename) for info in infos}
    model_name = "3D/3dmodel.model"
    settings_name = "Metadata/model_settings.config"
    if model_name not in payloads or settings_name not in payloads:
        raise ValueError("The project 3MF is missing model or plate settings.")

    core_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    production_ns = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    bambu_ns = "http://schemas.bambulab.com/package/2021"
    ElementTree.register_namespace("", core_ns)
    ElementTree.register_namespace("p", production_ns)
    ElementTree.register_namespace("BambuStudio", bambu_ns)

    try:
        model_root = ElementTree.fromstring(payloads[model_name])
        settings_root = ElementTree.fromstring(payloads[settings_name])
    except ElementTree.ParseError as exc:
        raise ValueError("The project 3MF contains invalid layout XML.") from exc

    build_items = [node for node in model_root.iter() if node.tag.rsplit("}", 1)[-1] == "item"]
    if not build_items:
        raise ValueError("The project 3MF has no build items to arrange.")

    bed_x, bed_y, bed_z = (float(value) for value in envelope_mm)
    usable_x = bed_x - 2 * margin_mm
    usable_y = bed_y - 2 * margin_mm
    if usable_x <= 0 or usable_y <= 0:
        raise ValueError("Printer envelope is too small for layout margins.")

    def parse_transform(raw_value: str | None) -> list[float]:
        raw = (raw_value or "1 0 0 0 1 0 0 0 1 0 0 0").split()
        if len(raw) != 12:
            raise ValueError("The project 3MF contains an invalid instance transform.")
        try:
            return [float(value) for value in raw]
        except ValueError as exc:
            raise ValueError("The project 3MF contains a non-numeric instance transform.") from exc

    def transform_point(values: list[float], point: tuple[float, float, float], include_translation: bool = True):
        x, y, z = point
        tx, ty, tz = values[9:12] if include_translation else (0.0, 0.0, 0.0)
        return (
            x * values[0] + y * values[3] + z * values[6] + tx,
            x * values[1] + y * values[4] + z * values[7] + ty,
            x * values[2] + y * values[5] + z * values[8] + tz,
        )

    main_objects = {
        node.attrib.get("id"): node
        for node in model_root.iter()
        if node.tag.rsplit("}", 1)[-1] == "object" and node.attrib.get("id")
    }

    def vertices_for_build_object(object_id: str | None):
        object_node = main_objects.get(object_id)
        if object_node is None:
            raise ValueError("The project 3MF references an unknown build object.")
        components = [node for node in object_node if node.tag.rsplit("}", 1)[-1] == "components"]
        if components:
            vertices = []
            for component in components[0]:
                if component.tag.rsplit("}", 1)[-1] != "component":
                    continue
                path_value = next(
                    (value for key, value in component.attrib.items() if key.rsplit("}", 1)[-1] == "path"),
                    None,
                )
                component_object_id = component.attrib.get("objectid")
                if not path_value or not component_object_id:
                    raise ValueError("The project 3MF contains an incomplete component reference.")
                archive_name = path_value.lstrip("/")
                if archive_name not in payloads:
                    raise ValueError("The project 3MF references a missing component model.")
                try:
                    component_root = ElementTree.fromstring(payloads[archive_name])
                except ElementTree.ParseError as exc:
                    raise ValueError("The project 3MF contains invalid component geometry XML.") from exc
                component_object = next(
                    (
                        node for node in component_root.iter()
                        if node.tag.rsplit("}", 1)[-1] == "object" and node.attrib.get("id") == component_object_id
                    ),
                    None,
                )
                if component_object is None:
                    raise ValueError("The project 3MF component object could not be found.")
                component_transform = parse_transform(component.attrib.get("transform"))
                for vertex in component_object.iter():
                    if vertex.tag.rsplit("}", 1)[-1] != "vertex":
                        continue
                    try:
                        point = (float(vertex.attrib["x"]), float(vertex.attrib["y"]), float(vertex.attrib["z"]))
                    except (KeyError, ValueError) as exc:
                        raise ValueError("The project 3MF contains an invalid mesh vertex.") from exc
                    vertices.append(transform_point(component_transform, point))
            if vertices:
                return vertices

        vertices = []
        for vertex in object_node.iter():
            if vertex.tag.rsplit("}", 1)[-1] != "vertex":
                continue
            try:
                vertices.append((float(vertex.attrib["x"]), float(vertex.attrib["y"]), float(vertex.attrib["z"])))
            except (KeyError, ValueError) as exc:
                raise ValueError("The project 3MF contains an invalid mesh vertex.") from exc
        if not vertices:
            raise ValueError("The project 3MF build object has no mesh vertices.")
        return vertices

    placements = []
    plate_index = 0
    cursor_x = margin_mm
    cursor_y = margin_mm
    row_height = 0.0
    for item_index, item in enumerate(build_items):
        values = parse_transform(item.attrib.get("transform"))
        object_vertices = vertices_for_build_object(item.attrib.get("objectid"))
        oriented = [transform_point(values, point, include_translation=False) for point in object_vertices]
        mins = [min(point[axis] for point in oriented) for axis in range(3)]
        maxs = [max(point[axis] for point in oriented) for axis in range(3)]
        width, depth, height = [maxs[axis] - mins[axis] for axis in range(3)]
        if width <= 0 or depth <= 0 or height <= 0:
            raise ValueError("The project 3MF contains a zero-size oriented object.")
        if width > usable_x + 1e-6 or depth > usable_y + 1e-6 or height > bed_z + 1e-6:
            raise ValueError("Orca's selected orientation does not fit the selected printer envelope.")

        if cursor_x > margin_mm and cursor_x + width > bed_x - margin_mm + 1e-6:
            cursor_x = margin_mm
            cursor_y += row_height + gap_mm
            row_height = 0.0
        if cursor_y + depth > bed_y - margin_mm + 1e-6:
            plate_index += 1
            cursor_x = margin_mm
            cursor_y = margin_mm
            row_height = 0.0

        # Use the actual embedded mesh bounds, not the source STL's assumed
        # centre. Orca stores source offsets separately and imported models are
        # not guaranteed to be centred at the 3MF object origin.
        tx = cursor_x - mins[0]
        ty = cursor_y - mins[1]
        tz = -mins[2]
        values[9:12] = [tx, ty, tz]
        item.set("transform", " ".join(f"{value:.8g}" for value in values))
        placements.append(
            {
                "object_id": item.attrib.get("objectid"),
                "plate_index": plate_index + 1,
                "translation_mm": [round(tx, 6), round(ty, 6), round(tz, 6)],
                "placed_bounds_mm": [
                    [round(cursor_x, 6), round(cursor_y, 6), 0.0],
                    [round(cursor_x + width, 6), round(cursor_y + depth, 6), round(height, 6)],
                ],
                "footprint_mm": [round(width, 6), round(depth, 6), round(height, 6)],
            }
        )
        cursor_x += width + gap_mm
        row_height = max(row_height, depth)

    # Replace Orca's broken plate-membership section with deterministic plate
    # membership matching the repaired build transforms.
    for child in list(settings_root):
        if child.tag.rsplit("}", 1)[-1] == "plate":
            settings_root.remove(child)
    assemble_index = next(
        (index for index, child in enumerate(list(settings_root)) if child.tag.rsplit("}", 1)[-1] == "assemble"),
        len(settings_root),
    )
    plates = []
    for current_plate in range(1, plate_index + 2):
        plate = ElementTree.Element("plate")
        for key, value in (
            ("plater_id", str(current_plate)),
            ("plater_name", ""),
            ("locked", "false"),
            ("filament_map_mode", "Auto For Flush"),
            ("gcode_file", ""),
        ):
            ElementTree.SubElement(plate, "metadata", {"key": key, "value": value})
        members = [placement for placement in placements if placement["plate_index"] == current_plate]
        for member_index, placement in enumerate(members, start=1):
            instance = ElementTree.SubElement(plate, "model_instance")
            for key, value in (
                ("object_id", str(placement["object_id"])),
                ("instance_id", "0"),
                ("identify_id", str(current_plate * 10000 + member_index)),
            ):
                ElementTree.SubElement(instance, "metadata", {"key": key, "value": value})
        settings_root.insert(assemble_index + current_plate - 1, plate)
        plates.append({"index": current_plate, "instance_count": len(members)})

    payloads[model_name] = ElementTree.tostring(model_root, encoding="utf-8", xml_declaration=True)
    payloads[settings_name] = ElementTree.tostring(settings_root, encoding="utf-8", xml_declaration=True)

    temporary = path.with_name(path.name + ".layout")
    try:
        with zipfile.ZipFile(temporary, "w") as target:
            for info in infos:
                target.writestr(info, payloads[info.filename])
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

    return {"repaired": True, "plate_count": len(plates), "plates": plates, "placements": placements}


def build_project_command(
    *,
    orca_bin: Path,
    machine_profile: Path,
    process_profile: Path,
    filament_profile: Path,
    sources: list[Path],
    project_path: Path,
    auto_orient: bool = True,
    allow_arrange_rotations: bool = True,
) -> list[str]:
    if not sources:
        raise ValueError("At least one STL source is required.")
    # OrcaSlicer 2.4.2 rejects --repetitions when plate_to_slice is 0, which is
    # exactly the mode needed for an unsliced editable project export. Supplying
    # one STL path per ordered instance avoids coupling project creation to
    # --slice and keeps the output editable instead of producing .gcode.3mf.
    command = [
        "xvfb-run", "-a", str(orca_bin),
        "--arrange", "1",
        *([] if allow_arrange_rotations else ["--allow-rotations=0"]),
        *(["--orient", "1"] if auto_orient else ["--orient", "0"]),
        "--ensure-on-bed",
        "--allow-newer-file",
        "--load-settings", f"{machine_profile};{process_profile}",
        "--load-filaments", str(filament_profile),
    ]
    # OrcaSlicer 2.4.2 can export a lone STL without assigning its only
    # instance to a printable plate for some user machine profiles (observed
    # with the RatRig profile). Reopening that otherwise valid project then
    # fails with CLI_NO_SUITABLE_OBJECTS. --assemble rebuilds the single model
    # with an explicit instance before the arrange/export pass, while keeping
    # one build item and the same mesh geometry.
    if len(sources) == 1:
        command.append("--assemble")
    command.extend(
        [
            "--export-3mf", str(project_path),
            *(str(source) for source in sources),
        ]
    )
    return command


def verify_project_command(*, orca_bin: Path, project_path: Path, output_dir: Path) -> list[str]:
    # Deliberately do not load external profiles here. Successful verification proves the
    # editable 3MF can be reopened with its embedded manufacturing configuration.
    return [
        "xvfb-run", "-a", str(orca_bin),
        "--slice", "0",
        "--allow-newer-file",
        "--outputdir", str(output_dir),
        str(project_path),
    ]
