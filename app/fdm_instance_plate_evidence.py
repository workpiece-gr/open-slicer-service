"""Per-instance/per-plate evidence for FDM Authority v2 CP4.

CP4 consumes the exact retained production 3MF, CP2 exact per-plate G-code
artifacts, and already-passed CP3 validation receipts. It never calls OrcaSlicer
and it does not grant production authority. Its job is to prove that every exact
3MF build item belongs to exactly one physical plate and corresponds to exactly
one Orca exclude-object definition with real extrusion activity in that plate's
exact G-code.

The current implementation intentionally supports the deterministic RatRig
project layout only. The temporary generic Ender route remains evidence-candidate
and must not be promoted by this module.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from typing import Any, Mapping
from xml.etree import ElementTree

INSTANCE_PLATE_EVIDENCE_VERSION = "fdm-instance-plate-evidence/1.0.0"
INSTANCE_ID_PREFIX = "fdm-inst-"
# Orca serializes object-definition coordinates at lower precision than the 3MF
# transforms. This is a software representation tolerance, not a physical
# calibration allowance.
GEOMETRY_SERIALIZATION_TOLERANCE_MM = 0.05

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_AXIS_RE = re.compile(r"([XYZE])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", re.I)
_NAME_ARG_RE = re.compile(r"(?:^|\s)NAME=([A-Za-z0-9_.:+-]+)(?:\s|$)")
_CENTER_ARG_RE = re.compile(r"(?:^|\s)CENTER=([^\s]+)(?:\s|$)")
_POLYGON_ARG_RE = re.compile(r"(?:^|\s)POLYGON=(\[.*\])(?:\s|$)")
_PROFILE_KINDS = ("machine", "process", "filament")


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256_RE.fullmatch(text) else ""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_transform(raw_value: Any) -> list[float]:
    raw = str(raw_value or "1 0 0 0 1 0 0 0 1 0 0 0").split()
    if len(raw) != 12:
        raise ValueError("The production 3MF contains an invalid 12-value build-item transform.")
    values = [_finite(value) for value in raw]
    if any(value is None for value in values):
        raise ValueError("The production 3MF contains a non-finite build-item transform.")
    return [float(value) for value in values if value is not None]


def _transform_point(values: list[float], point: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        x * values[0] + y * values[3] + z * values[6] + values[9],
        x * values[1] + y * values[4] + z * values[7] + values[10],
        x * values[2] + y * values[5] + z * values[8] + values[11],
    )


def _bounds(points: list[tuple[float, float, float]]) -> dict[str, list[float]]:
    if not points:
        raise ValueError("A production 3MF build object has no geometry vertices.")
    low = [min(point[axis] for point in points) for axis in range(3)]
    high = [max(point[axis] for point in points) for axis in range(3)]
    if any(not math.isfinite(value) for value in (*low, *high)) or any(high[i] <= low[i] for i in range(3)):
        raise ValueError("A production 3MF build object has invalid or zero-size bounds.")
    return {"min": low, "max": high}


def _profile_json(payload: bytes, kind: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"The exact {kind} profile bytes are not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict) or value.get("type") != kind:
        raise ValueError(f"The exact {kind} profile must declare type={kind}.")
    return value


def _machine_envelope(machine_profile_bytes: bytes) -> tuple[float, float, float]:
    machine = _profile_json(machine_profile_bytes, "machine")
    area = machine.get("printable_area")
    if not isinstance(area, list) or len(area) < 3:
        raise ValueError("The exact machine profile does not expose printable_area.")
    points: list[tuple[float, float]] = []
    for raw in area:
        text = str(raw)
        if "x" not in text.lower():
            raise ValueError("The exact machine printable_area contains a malformed point.")
        left, right = re.split("x", text, maxsplit=1, flags=re.I)
        x, y = _finite(left), _finite(right)
        if x is None or y is None:
            raise ValueError("The exact machine printable_area contains a non-finite point.")
        points.append((x, y))
    min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
    min_y, max_y = min(y for _, y in points), max(y for _, y in points)
    height = _finite(machine.get("printable_height"))
    if min_x != 0 or min_y != 0 or max_x <= 0 or max_y <= 0 or height is None or height <= 0:
        raise ValueError("CP4 currently requires an origin-based rectangular machine envelope.")
    return max_x, max_y, height


def _archive_payloads(project_bytes: bytes) -> dict[str, bytes]:
    if not project_bytes:
        raise ValueError("CP4 requires the exact retained production 3MF bytes.")
    try:
        with zipfile.ZipFile(io.BytesIO(project_bytes), "r") as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("The retained production 3MF is not a valid ZIP/3MF archive.") from exc


def _vertices_for_object(
    object_id: str,
    *,
    model_root: ElementTree.Element,
    payloads: Mapping[str, bytes],
) -> list[tuple[float, float, float]]:
    main_objects = {
        node.attrib.get("id"): node
        for node in model_root.iter()
        if node.tag.rsplit("}", 1)[-1] == "object" and node.attrib.get("id")
    }
    object_node = main_objects.get(object_id)
    if object_node is None:
        raise ValueError(f"Production 3MF build item references unknown object {object_id!r}.")

    components = [node for node in object_node if node.tag.rsplit("}", 1)[-1] == "components"]
    if components:
        result: list[tuple[float, float, float]] = []
        for component in components[0]:
            if component.tag.rsplit("}", 1)[-1] != "component":
                continue
            path_value = next(
                (value for key, value in component.attrib.items() if key.rsplit("}", 1)[-1] == "path"),
                None,
            )
            component_object_id = component.attrib.get("objectid")
            if not path_value or not component_object_id:
                raise ValueError("Production 3MF contains an incomplete component reference.")
            archive_name = path_value.lstrip("/")
            payload = payloads.get(archive_name)
            if payload is None:
                raise ValueError("Production 3MF references a missing component model.")
            try:
                component_root = ElementTree.fromstring(payload)
            except ElementTree.ParseError as exc:
                raise ValueError("Production 3MF contains invalid component geometry XML.") from exc
            component_object = next(
                (
                    node for node in component_root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "object" and node.attrib.get("id") == component_object_id
                ),
                None,
            )
            if component_object is None:
                raise ValueError("Production 3MF component object could not be found.")
            component_transform = _parse_transform(component.attrib.get("transform"))
            for vertex in component_object.iter():
                if vertex.tag.rsplit("}", 1)[-1] != "vertex":
                    continue
                try:
                    point = (float(vertex.attrib["x"]), float(vertex.attrib["y"]), float(vertex.attrib["z"]))
                except (KeyError, ValueError) as exc:
                    raise ValueError("Production 3MF contains an invalid component vertex.") from exc
                result.append(_transform_point(component_transform, point))
        if not result:
            raise ValueError("Production 3MF component build object has no mesh vertices.")
        return result

    result = []
    for vertex in object_node.iter():
        if vertex.tag.rsplit("}", 1)[-1] != "vertex":
            continue
        try:
            result.append((float(vertex.attrib["x"]), float(vertex.attrib["y"]), float(vertex.attrib["z"])))
        except (KeyError, ValueError) as exc:
            raise ValueError("Production 3MF contains an invalid mesh vertex.") from exc
    if not result:
        raise ValueError("Production 3MF build object has no mesh vertices.")
    return result


def _project_instances(project_bytes: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payloads = _archive_payloads(project_bytes)
    model_name = "3D/3dmodel.model"
    settings_name = "Metadata/model_settings.config"
    if model_name not in payloads or settings_name not in payloads:
        raise ValueError("Production 3MF is missing model geometry or physical plate settings.")
    try:
        model_root = ElementTree.fromstring(payloads[model_name])
        settings_root = ElementTree.fromstring(payloads[settings_name])
    except ElementTree.ParseError as exc:
        raise ValueError("Production 3MF contains invalid model/plate XML.") from exc

    build_nodes = [node for node in model_root.iter() if node.tag.rsplit("}", 1)[-1] == "item"]
    if not build_nodes:
        raise ValueError("Production 3MF contains no build items.")
    build_items: list[dict[str, Any]] = []
    for build_index, node in enumerate(build_nodes, start=1):
        object_id = _text(node.attrib.get("objectid"))
        if not object_id:
            raise ValueError("Production 3MF contains a build item without objectid.")
        transform = _parse_transform(node.attrib.get("transform"))
        vertices = _vertices_for_object(object_id, model_root=model_root, payloads=payloads)
        world_bounds = _bounds([_transform_point(transform, point) for point in vertices])
        build_items.append(
            {
                "build_index": build_index,
                "object_id": object_id,
                "transform": transform,
                "world_bounds": world_bounds,
            }
        )

    plates: list[dict[str, Any]] = []
    seen_plate_ids: set[int] = set()
    seen_identify_ids: set[str] = set()
    for plate_node in (node for node in settings_root if node.tag.rsplit("}", 1)[-1] == "plate"):
        metadata: dict[str, str | None] = {}
        members: list[dict[str, str]] = []
        for child in plate_node:
            local = child.tag.rsplit("}", 1)[-1]
            if local == "metadata":
                metadata[child.attrib.get("key", "")] = child.attrib.get("value")
            elif local == "model_instance":
                member = {
                    grandchild.attrib.get("key", ""): str(grandchild.attrib.get("value") or "")
                    for grandchild in child
                    if grandchild.tag.rsplit("}", 1)[-1] == "metadata"
                }
                members.append(member)
        try:
            plate_id = int(str(metadata.get("plater_id") or ""))
        except ValueError as exc:
            raise ValueError("Production 3MF contains a plate with invalid plater_id.") from exc
        if plate_id < 1 or plate_id in seen_plate_ids:
            raise ValueError("Production 3MF plate ids must be positive and unique.")
        if not members:
            raise ValueError(f"Production 3MF plate {plate_id} contains no model instances.")
        for member in members:
            if not _text(member.get("object_id")):
                raise ValueError(f"Production 3MF plate {plate_id} contains a member without object_id.")
            identify_id = _text(member.get("identify_id"))
            if not identify_id or identify_id in seen_identify_ids:
                raise ValueError("Production 3MF physical membership requires unique identify_id values.")
            seen_identify_ids.add(identify_id)
        seen_plate_ids.add(plate_id)
        plates.append({"plate_id": plate_id, "members": members})

    if not plates:
        raise ValueError("Production 3MF exposes no physical plate membership metadata.")
    plates.sort(key=lambda item: item["plate_id"])
    if [plate["plate_id"] for plate in plates] != list(range(1, len(plates) + 1)):
        raise ValueError("CP4 RatRig evidence currently requires contiguous physical plate ids starting at 1.")
    if sum(len(plate["members"]) for plate in plates) != len(build_items):
        raise ValueError("Production 3MF build-item count does not reconcile with physical plate membership metadata.")
    return build_items, plates


def _plate_origins(plate_count: int, envelope: tuple[float, float, float]) -> dict[int, tuple[float, float, float]]:
    bed_x, bed_y, _ = envelope
    cols = int(math.ceil(math.sqrt(plate_count)))
    stride_x, stride_y = bed_x * 1.2, bed_y * 1.2
    result: dict[int, tuple[float, float, float]] = {}
    for plate_id in range(1, plate_count + 1):
        virtual = plate_id - 1
        result[plate_id] = ((virtual % cols) * stride_x, -(virtual // cols) * stride_y, 0.0)
    return result


def _local_bounds(
    world_bounds: Mapping[str, list[float]],
    origin: tuple[float, float, float],
) -> dict[str, list[float]]:
    return {
        "min": [world_bounds["min"][axis] - origin[axis] for axis in range(3)],
        "max": [world_bounds["max"][axis] - origin[axis] for axis in range(3)],
    }


def _inside_envelope(bounds: Mapping[str, list[float]], envelope: tuple[float, float, float]) -> bool:
    epsilon = 1e-6
    return all(
        bounds["min"][axis] >= -epsilon and bounds["max"][axis] <= envelope[axis] + epsilon
        for axis in range(3)
    )


def _canonical_transform(values: list[float]) -> list[str]:
    return [format(value, ".17g") for value in values]


def _stable_instance_id(
    *,
    project_sha256: str,
    build_index: int,
    object_id: str,
    plate_id: int,
    transform: list[float],
) -> str:
    payload = json.dumps(
        {
            "projectSha256": project_sha256,
            "buildIndex": build_index,
            "objectId": object_id,
            "plateId": plate_id,
            "transform": _canonical_transform(transform),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return INSTANCE_ID_PREFIX + hashlib.sha256(payload).hexdigest()


def _bind_project_membership(
    *,
    project_sha256: str,
    build_items: list[dict[str, Any]],
    plates: list[dict[str, Any]],
    envelope: tuple[float, float, float],
) -> list[dict[str, Any]]:
    origins = _plate_origins(len(plates), envelope)
    grouped: dict[int, list[dict[str, Any]]] = {plate["plate_id"]: [] for plate in plates}
    instances: list[dict[str, Any]] = []
    for build in build_items:
        matches: list[tuple[int, dict[str, list[float]]]] = []
        for plate_id, origin in origins.items():
            local = _local_bounds(build["world_bounds"], origin)
            if _inside_envelope(local, envelope):
                matches.append((plate_id, local))
        if len(matches) != 1:
            raise ValueError(
                f"Build item {build['build_index']} does not map uniquely to one physical RatRig plate; candidates={[item[0] for item in matches]}."
            )
        plate_id, local = matches[0]
        instance = {
            "id": _stable_instance_id(
                project_sha256=project_sha256,
                build_index=build["build_index"],
                object_id=build["object_id"],
                plate_id=plate_id,
                transform=build["transform"],
            ),
            "objectId": build["object_id"],
            "plateId": str(plate_id),
            "plateIndex": plate_id,
            "buildItemIndex": build["build_index"],
            "transform": build["transform"],
            "boundsMm": {
                "min": [round(value, 6) for value in local["min"]],
                "max": [round(value, 6) for value in local["max"]],
            },
        }
        grouped[plate_id].append(instance)
        instances.append(instance)

    if len({item["id"] for item in instances}) != len(instances):
        raise ValueError("Stable Workpiece instance identities are not unique for the exact production 3MF.")

    for plate in plates:
        plate_id = plate["plate_id"]
        project_objects = Counter(item["objectId"] for item in grouped[plate_id])
        metadata_objects = Counter(_text(member.get("object_id")) for member in plate["members"])
        if project_objects != metadata_objects:
            raise ValueError(f"Physical plate {plate_id} geometry does not reconcile with model_instance object membership.")
        if len(grouped[plate_id]) != len(plate["members"]):
            raise ValueError(f"Physical plate {plate_id} instance count does not reconcile with model_instance membership.")

    instances.sort(key=lambda item: item["buildItemIndex"])
    return instances


def _object_definition(code: str) -> tuple[str, list[float], list[list[float]], dict[str, list[float]]]:
    name_match = _NAME_ARG_RE.search(code)
    center_match = _CENTER_ARG_RE.search(code)
    polygon_match = _POLYGON_ARG_RE.search(code)
    if not name_match or not center_match or not polygon_match:
        raise ValueError("EXCLUDE_OBJECT_DEFINE must contain NAME, CENTER, and POLYGON.")
    name = name_match.group(1)
    try:
        center = [float(value) for value in center_match.group(1).split(",")]
        polygon = json.loads(polygon_match.group(1))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Object definition {name!r} has malformed coordinates.") from exc
    if len(center) != 2 or not all(math.isfinite(value) for value in center):
        raise ValueError(f"Object definition {name!r} has invalid CENTER coordinates.")
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError(f"Object definition {name!r} has invalid POLYGON geometry.")
    points: list[list[float]] = []
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"Object definition {name!r} has a malformed POLYGON point.")
        x, y = _finite(point[0]), _finite(point[1])
        if x is None or y is None:
            raise ValueError(f"Object definition {name!r} has a non-finite POLYGON point.")
        points.append([x, y])
    definition_bounds = {
        "min": [min(point[0] for point in points), min(point[1] for point in points)],
        "max": [max(point[0] for point in points), max(point[1] for point in points)],
    }
    return name, center, points, definition_bounds


def _analyze_gcode_objects(gcode_bytes: bytes) -> dict[str, dict[str, Any]]:
    try:
        text = gcode_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CP4 requires UTF-8 exact G-code bytes already validated by CP3.") from exc

    definitions: dict[str, dict[str, Any]] = {}
    active: str | None = None
    x: float | None = 0.0
    y: float | None = 0.0
    z: float | None = 0.0
    e = 0.0
    xyz_absolute = True
    e_absolute = True

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        code = raw_line.split(";", 1)[0].strip()
        if not code:
            continue
        token = code.split(None, 1)[0].upper()
        if token == "EXCLUDE_OBJECT_DEFINE":
            name, center, polygon, definition_bounds = _object_definition(code)
            if name in definitions:
                raise ValueError(f"Exact G-code defines object {name!r} more than once.")
            definitions[name] = {
                "name": name,
                "centerMm": center,
                "polygon": polygon,
                "definitionBoundsMm": definition_bounds,
                "extrusionSegmentCount": 0,
                "extrusionBoundsMm": None,
                "_low": [math.inf, math.inf, math.inf],
                "_high": [-math.inf, -math.inf, -math.inf],
            }
            continue
        if token == "EXCLUDE_OBJECT_START":
            match = _NAME_ARG_RE.search(code)
            name = match.group(1) if match else ""
            if not name or name not in definitions or active is not None:
                raise ValueError(f"Invalid EXCLUDE_OBJECT_START at G-code line {line_number}.")
            active = name
            continue
        if token == "EXCLUDE_OBJECT_END":
            match = _NAME_ARG_RE.search(code)
            name = match.group(1) if match else ""
            if not name or active != name:
                raise ValueError(f"Invalid EXCLUDE_OBJECT_END at G-code line {line_number}.")
            active = None
            continue
        if token == "G90":
            xyz_absolute = True
            continue
        if token == "G91":
            xyz_absolute = False
            continue
        if token == "M82":
            e_absolute = True
            continue
        if token == "M83":
            e_absolute = False
            continue
        if token == "G28":
            x = y = z = None
            continue
        if token == "G92":
            values = {axis.upper(): float(value) for axis, value in _AXIS_RE.findall(code)}
            if "E" in values:
                e = values["E"]
            continue
        if token not in {"G0", "G1"}:
            continue

        values = {axis.upper(): float(value) for axis, value in _AXIS_RE.findall(code)}

        def next_axis(current: float | None, axis: str) -> float | None:
            if axis not in values:
                return current
            if xyz_absolute:
                return values[axis]
            return None if current is None else current + values[axis]

        nx, ny, nz = next_axis(x, "X"), next_axis(y, "Y"), next_axis(z, "Z")
        ne = values.get("E", e) if e_absolute else e + values.get("E", 0.0)
        extruding = ne > e + 1e-9 and (nx != x or ny != y)
        if extruding and active is not None:
            if None in (x, y, z, nx, ny, nz):
                raise ValueError(f"Object extrusion coordinates are unknown at G-code line {line_number}.")
            item = definitions[active]
            item["extrusionSegmentCount"] += 1
            for point in ((float(x), float(y), float(z)), (float(nx), float(ny), float(nz))):
                for axis, value in enumerate(point):
                    item["_low"][axis] = min(item["_low"][axis], value)
                    item["_high"][axis] = max(item["_high"][axis], value)
        x, y, z, e = nx, ny, nz, ne

    if active is not None:
        raise ValueError(f"Exact G-code leaves object annotation {active!r} open.")
    if not definitions:
        raise ValueError("Exact G-code exposes no exclude-object definitions for per-instance proof.")
    for name, item in definitions.items():
        if item["extrusionSegmentCount"] < 1:
            raise ValueError(f"Exact G-code object {name!r} has no proven extrusion activity.")
        item["extrusionBoundsMm"] = {
            "min": [round(value, 6) for value in item.pop("_low")],
            "max": [round(value, 6) for value in item.pop("_high")],
        }
    return definitions


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=GEOMETRY_SERIALIZATION_TOLERANCE_MM)


def _definition_matches_instance(definition: Mapping[str, Any], instance: Mapping[str, Any]) -> bool:
    db = _record(definition.get("definitionBoundsMm"))
    ib = _record(instance.get("boundsMm"))
    dmin, dmax, imin, imax = db.get("min"), db.get("max"), ib.get("min"), ib.get("max")
    if not all(isinstance(value, list) for value in (dmin, dmax, imin, imax)):
        return False
    if len(dmin) != 2 or len(dmax) != 2 or len(imin) < 2 or len(imax) < 2:
        return False
    return all(_close(float(left), float(right)) for left, right in zip((*dmin, *dmax), (*imin[:2], *imax[:2]), strict=True))


def _validation_map(value: Mapping[int, Mapping[str, Any]] | Any) -> dict[int, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("CP4 requires a per-plate mapping of CP3 validation receipts.")
    result: dict[int, Mapping[str, Any]] = {}
    for raw_key, raw_value in value.items():
        try:
            plate_id = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise ValueError("CP3 validation mapping contains an invalid physical plate id.") from exc
        if plate_id < 1 or plate_id in result or not isinstance(raw_value, Mapping):
            raise ValueError("CP3 validation mapping contains duplicate or malformed plate evidence.")
        result[plate_id] = raw_value
    return result


def build_instance_plate_evidence(
    *,
    project_bytes: bytes,
    exact_gcode_result: Mapping[str, Any] | Any,
    cp3_validations: Mapping[int, Mapping[str, Any]] | Any,
    machine_profile_bytes: bytes,
) -> dict[str, Any]:
    """Build fail-closed per-instance/per-plate evidence from exact retained bytes."""

    exact = _record(exact_gcode_result)
    project_sha256 = _digest(project_bytes)
    if _sha(exact.get("project_sha256")) != project_sha256:
        raise ValueError("Exact retained 3MF bytes do not match the CP2 project SHA-256 receipt.")
    generation = _record(exact.get("generation_receipt"))
    source_sha = _sha(_record(generation.get("source")).get("sha256"))
    printer = _record(generation.get("printer"))
    printer_key = _text(printer.get("key"))
    if printer_key != "ratrig_vcore3_300" or printer.get("temporary_generic") is not False:
        raise ValueError("CP4 production-instance evidence currently supports only the non-generic RatRig route.")

    profiles = _record(generation.get("profiles"))
    profile_hashes: dict[str, str] = {}
    for kind in _PROFILE_KINDS:
        digest = _sha(_record(profiles.get(kind)).get("sha256"))
        if not digest:
            raise ValueError(f"CP2 generation receipt is missing exact {kind} profile SHA-256.")
        profile_hashes[kind] = digest
    if _digest(machine_profile_bytes) != profile_hashes["machine"]:
        raise ValueError("Exact machine profile bytes do not match the CP2 generation receipt.")

    envelope = _machine_envelope(machine_profile_bytes)
    build_items, project_plates = _project_instances(project_bytes)
    instances = _bind_project_membership(
        project_sha256=project_sha256,
        build_items=build_items,
        plates=project_plates,
        envelope=envelope,
    )
    by_plate_instances: dict[int, list[dict[str, Any]]] = {plate["plate_id"]: [] for plate in project_plates}
    for instance in instances:
        by_plate_instances[int(instance["plateIndex"])].append(instance)

    artifacts = exact.get("plates")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("CP2 exact G-code result contains no retained plate artifacts.")
    artifact_by_plate: dict[int, Mapping[str, Any]] = {}
    for raw in artifacts:
        artifact = _record(raw)
        try:
            plate_id = int(artifact.get("plate_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("CP2 exact G-code artifact contains an invalid physical plate id.") from exc
        if plate_id < 1 or plate_id in artifact_by_plate:
            raise ValueError("CP2 exact G-code artifacts contain duplicate/invalid physical plate ids.")
        if _sha(artifact.get("project_sha256")) != project_sha256:
            raise ValueError(f"CP2 plate {plate_id} does not bind to the exact retained production 3MF.")
        payload = artifact.get("bytes")
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            raise ValueError(f"CP2 plate {plate_id} does not retain exact G-code bytes.")
        if _sha(artifact.get("sha256")) != _digest(bytes(payload)):
            raise ValueError(f"CP2 plate {plate_id} G-code bytes do not match its SHA-256 receipt.")
        artifact_by_plate[plate_id] = artifact

    expected_plate_ids = {plate["plate_id"] for plate in project_plates}
    if set(artifact_by_plate) != expected_plate_ids:
        raise ValueError("CP2 exact G-code artifact plate set does not match physical project plates.")
    validations = _validation_map(cp3_validations)
    if set(validations) != expected_plate_ids:
        raise ValueError("CP3 validation receipt plate set does not match physical project plates.")

    plate_evidence: list[dict[str, Any]] = []
    used_object_names: set[tuple[int, str]] = set()
    for plate_id in sorted(expected_plate_ids):
        artifact = artifact_by_plate[plate_id]
        validation = validations[plate_id]
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            raise ValueError(f"CP3 validation for physical plate {plate_id} is not authority-critical complete.")
        gcode_sha = _sha(artifact.get("sha256"))
        if _sha(validation.get("gcodeSha256")) != gcode_sha or _sha(validation.get("projectSha256")) != project_sha256:
            raise ValueError(f"CP3 validation receipt for physical plate {plate_id} is not bound to exact CP2 bytes.")
        validation_profiles = _record(validation.get("profileSha256"))
        if any(_sha(validation_profiles.get(kind)) != profile_hashes[kind] for kind in _PROFILE_KINDS):
            raise ValueError(f"CP3 validation receipt for physical plate {plate_id} is not bound to exact profile hashes.")

        definitions = _analyze_gcode_objects(bytes(artifact["bytes"]))
        validation_objects = validation.get("definedObjects")
        if not isinstance(validation_objects, list) or set(_text(name) for name in validation_objects) != set(definitions):
            raise ValueError(f"CP3 defined-object receipt for physical plate {plate_id} does not match exact G-code definitions.")

        plate_instances = by_plate_instances[plate_id]
        if len(definitions) != len(plate_instances):
            raise ValueError(
                f"Physical plate {plate_id} has {len(plate_instances)} project instances but {len(definitions)} exact G-code object definitions."
            )
        unmatched = set(definitions)
        for instance in plate_instances:
            candidates = [name for name in unmatched if _definition_matches_instance(definitions[name], instance)]
            if len(candidates) != 1:
                raise ValueError(
                    f"Instance {instance['id']} on physical plate {plate_id} does not map uniquely to one exact G-code object definition; candidates={candidates}."
                )
            name = candidates[0]
            unmatched.remove(name)
            if (plate_id, name) in used_object_names:
                raise ValueError("An exact G-code object definition was assigned to more than one physical instance.")
            used_object_names.add((plate_id, name))
            definition = definitions[name]
            instance["gcodeEvidence"] = {
                "objectName": name,
                "definitionCenterMm": [round(float(value), 6) for value in definition["centerMm"]],
                "definitionBoundsMm": {
                    "min": [round(float(value), 6) for value in definition["definitionBoundsMm"]["min"]],
                    "max": [round(float(value), 6) for value in definition["definitionBoundsMm"]["max"]],
                },
                "extrusionSegmentCount": int(definition["extrusionSegmentCount"]),
                "extrusionBoundsMm": definition["extrusionBoundsMm"],
                "gcodeSha256": gcode_sha,
            }
        if unmatched:
            raise ValueError(f"Physical plate {plate_id} has unmatched exact G-code object definitions: {sorted(unmatched)}.")

        plate_evidence.append(
            {
                "id": str(plate_id),
                "index": plate_id,
                "projectSha256": project_sha256,
                "gcodeSha256": gcode_sha,
                "validationGcodeSha256": _sha(validation.get("gcodeSha256")),
                "instanceIds": [item["id"] for item in plate_instances],
                "objectNames": [item["gcodeEvidence"]["objectName"] for item in plate_instances],
            }
        )

    if any("gcodeEvidence" not in instance for instance in instances):
        raise ValueError("At least one production 3MF instance lacks exact per-G-code evidence.")

    return {
        "contractVersion": INSTANCE_PLATE_EVIDENCE_VERSION,
        "authorityState": "evidence_candidate",
        "projectSha256": project_sha256,
        "sourceSha256": source_sha,
        "printerKey": printer_key,
        "profileSha256": profile_hashes,
        "geometrySerializationToleranceMm": GEOMETRY_SERIALIZATION_TOLERANCE_MM,
        "instances": instances,
        "plates": plate_evidence,
        "totals": {"instanceCount": len(instances), "plateCount": len(plate_evidence)},
    }
