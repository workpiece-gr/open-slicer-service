"""Helpers for the experimental editable Orca project-3MF pipeline."""

from __future__ import annotations

import hashlib
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
        "--allow-rotations", "1" if allow_arrange_rotations else "0",
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
