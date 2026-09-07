"""Deterministic FDM geometry stability checks and controlled orientation rescue.

This module intentionally does not ask Orca's arranger for unrestricted rotations.
It evaluates the exact orientation Orca selected and may apply only a small,
reviewable set of world-space rotations:

- +/-45 degrees around Z when a footprint needs diagonal bed use;
- +/-90 degrees around X or Y for clearly rod-like tall parts, optionally
  followed by +/-45 degrees around Z.

The policy is a warning / geometry-routing aid. It does not qualify a printer,
change process settings, or grant manufacturing authority.
"""

from __future__ import annotations

import math
from itertools import permutations
from typing import Iterable, Sequence

POLICY_VERSION = "fdm-geometry-stability/1.0.0"
DEFAULT_MARGIN_MM = 12.0
TALL_HEIGHT_FRACTION = 0.40
TALL_SLENDERNESS = 6.0
ROD_ASPECT_RATIO = 4.0
MIN_HEIGHT_REDUCTION = 0.30
_EPS = 1e-6


def _dims(points: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    if not points:
        raise ValueError("Geometry stability evaluation requires vertices.")
    mins = [min(float(point[axis]) for point in points) for axis in range(3)]
    maxs = [max(float(point[axis]) for point in points) for axis in range(3)]
    dims = tuple(maxs[axis] - mins[axis] for axis in range(3))
    if any(not math.isfinite(value) or value <= 0 for value in dims):
        raise ValueError("Geometry stability evaluation found an invalid oriented envelope.")
    return dims


def _matrix(values: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    if len(values) < 9:
        raise ValueError("An Orca transform requires a 3x3 orientation matrix.")
    # Orca/3MF stores the matrix in the same flattened convention already used
    # by project_builder.transform_point: x'=x*v0+y*v3+z*v6, etc.
    return (
        (float(values[0]), float(values[3]), float(values[6])),
        (float(values[1]), float(values[4]), float(values[7])),
        (float(values[2]), float(values[5]), float(values[8])),
    )


def _flatten(matrix: Sequence[Sequence[float]], original: Sequence[float]) -> list[float]:
    values = list(float(value) for value in original)
    values[0], values[3], values[6] = matrix[0]
    values[1], values[4], values[7] = matrix[1]
    values[2], values[5], values[8] = matrix[2]
    return values


def _multiply(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]):
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _rotation(axis: str, degrees: float):
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    if axis == "x":
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == "y":
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    if axis == "z":
        return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    raise ValueError(f"Unsupported rotation axis: {axis}")


def _apply(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = (float(value) for value in point)
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    )


def _fits(dims: Sequence[float], usable_x: float, usable_y: float, bed_z: float) -> bool:
    return dims[0] <= usable_x + _EPS and dims[1] <= usable_y + _EPS and dims[2] <= bed_z + _EPS


def _tall_slender(dims: Sequence[float], bed_z: float) -> bool:
    width, depth, height = (float(value) for value in dims)
    return (
        height >= bed_z * TALL_HEIGHT_FRACTION - _EPS
        and height / max(min(width, depth), _EPS) >= TALL_SLENDERNESS
    )


def _rod_like(dims: Sequence[float]) -> bool:
    ordered = sorted(float(value) for value in dims)
    return ordered[2] / max(ordered[1], _EPS) >= ROD_ASPECT_RATIO


def _round_dims(dims: Sequence[float]) -> list[float]:
    return [round(float(value), 6) for value in dims]


def source_fits_controlled_ratrig_diagonal(
    dimensions_mm: Sequence[float],
    envelope_mm: Sequence[float],
    *,
    margin_mm: float = DEFAULT_MARGIN_MM,
) -> bool:
    """Conservative source-envelope routing check including one 45-degree rod case.

    Axis permutations remain the default. Only clearly rod-like source envelopes
    gain the additional diagonal-bed allowance, preventing arbitrary broad
    diagonal acceptance for complex parts whose AABB is not a useful fit proof.
    """
    if len(dimensions_mm) != 3 or len(envelope_mm) != 3:
        return False
    part = [float(value) for value in dimensions_mm]
    bed_x, bed_y, bed_z = (float(value) for value in envelope_mm)
    if any(not math.isfinite(value) or value <= 0 for value in (*part, bed_x, bed_y, bed_z)):
        return False
    usable_x, usable_y = bed_x - 2 * margin_mm, bed_y - 2 * margin_mm
    if usable_x <= 0 or usable_y <= 0:
        return False

    for width, depth, height in set(permutations(part, 3)):
        if _fits((width, depth, height), usable_x, usable_y, bed_z):
            return True

    if not _rod_like(part):
        return False
    c = math.sqrt(0.5)
    for width, depth, height in set(permutations(part, 3)):
        if height > bed_z + _EPS:
            continue
        diagonal_width = abs(width * c) + abs(depth * c)
        diagonal_depth = abs(width * c) + abs(depth * c)
        if diagonal_width <= usable_x + _EPS and diagonal_depth <= usable_y + _EPS:
            return True
    return False


def resolve_controlled_orientation(
    transform_values: Sequence[float],
    object_vertices: Iterable[Sequence[float]],
    envelope_mm: Sequence[float],
    *,
    margin_mm: float = DEFAULT_MARGIN_MM,
) -> tuple[list[float], dict]:
    """Return a deterministic safe transform candidate and a structured assessment."""
    vertices = [tuple(float(value) for value in point) for point in object_vertices]
    if not vertices:
        raise ValueError("Geometry stability evaluation requires object vertices.")
    if len(envelope_mm) != 3:
        raise ValueError("Geometry stability evaluation requires a 3D printer envelope.")
    bed_x, bed_y, bed_z = (float(value) for value in envelope_mm)
    usable_x, usable_y = bed_x - 2 * margin_mm, bed_y - 2 * margin_mm
    if usable_x <= 0 or usable_y <= 0 or bed_z <= 0:
        raise ValueError("Printer envelope is too small for geometry stability evaluation.")

    base_matrix = _matrix(transform_values)

    def candidate(lay_axis: str | None = None, lay_degrees: int = 0, z_degrees: int = 0):
        matrix = base_matrix
        if lay_axis:
            matrix = _multiply(_rotation(lay_axis, lay_degrees), matrix)
        if z_degrees:
            matrix = _multiply(_rotation("z", z_degrees), matrix)
        dims = _dims([_apply(matrix, point) for point in vertices])
        return {
            "matrix": matrix,
            "dims": dims,
            "fits": _fits(dims, usable_x, usable_y, bed_z),
            "layAxis": lay_axis,
            "layDegrees": lay_degrees,
            "zDegrees": z_degrees,
        }

    initial = candidate()
    initial_dims = initial["dims"]
    initial_tall = _tall_slender(initial_dims, bed_z)
    rod_like = _rod_like(initial_dims)
    selected = initial
    tested = [initial]

    # If the selected orientation is already low enough but only misses the
    # usable XY footprint, try exactly +/-45 degrees around Z. We do not give
    # Orca permission to choose arbitrary arranger rotations.
    if not initial["fits"] and initial_dims[2] <= bed_z + _EPS:
        for z_degrees in (45, -45):
            tested.append(candidate(z_degrees=z_degrees))
        fitting = [item for item in tested[1:] if item["fits"]]
        if fitting:
            selected = min(fitting, key=lambda item: (abs(item["zDegrees"]), -item["zDegrees"]))

    # A genuinely tall rod gets a second, deliberately narrow rescue set: lay
    # the long direction into X/Y, preferring a non-diagonal placement and only
    # using +/-45 degrees when the square bed needs the diagonal.
    if initial_tall and rod_like:
        lay_candidates = []
        for axis in ("x", "y"):
            for lay_degrees in (90, -90):
                for z_degrees in (0, 45, -45):
                    item = candidate(axis, lay_degrees, z_degrees)
                    tested.append(item)
                    if item["fits"] and item["dims"][2] <= initial_dims[2] * (1.0 - MIN_HEIGHT_REDUCTION) + _EPS:
                        lay_candidates.append(item)
        if lay_candidates:
            selected = min(
                lay_candidates,
                key=lambda item: (
                    item["dims"][2],
                    1 if item["zDegrees"] else 0,
                    abs(item["zDegrees"]),
                    0 if item["layDegrees"] == 90 else 1,
                    0 if item["layAxis"] == "x" else 1,
                ),
            )

    final_dims = selected["dims"]
    final_tall = _tall_slender(final_dims, bed_z)
    adjusted = selected is not initial
    diagonal = bool(selected["zDegrees"])

    if final_tall:
        severity = "warning"
        code = "tall_slender_print"
        message = (
            "Tall-part stability warning: this part remains tall and slender within the available build area. "
            "Printing may require additional bed adhesion, supports, reduced speed, another orientation, or splitting the part. "
            "Workpiece will review it before production."
        )
    elif adjusted and diagonal:
        severity = "info"
        code = "controlled_diagonal_orientation"
        message = (
            "Workpiece applied a controlled 45-degree diagonal orientation to fit this slender part lower on the bed and reduce tall-print instability. "
            "Workshop review is still required."
        )
    elif adjusted:
        severity = "info"
        code = "controlled_low_orientation"
        message = (
            "Workpiece reoriented this tall slender part to a lower bed orientation to reduce print-instability risk. "
            "Workshop review is still required."
        )
    else:
        severity = "none"
        code = None
        message = None

    final_values = _flatten(selected["matrix"], transform_values)
    assessment = {
        "policyVersion": POLICY_VERSION,
        "severity": severity,
        "code": code,
        "message": message,
        "rodLike": rod_like,
        "initialTallSlender": initial_tall,
        "finalTallSlender": final_tall,
        "orientationAdjusted": adjusted,
        "diagonal45Applied": diagonal,
        "initialFootprintMm": _round_dims(initial_dims),
        "finalFootprintMm": _round_dims(final_dims),
        "usableBedMm": [round(usable_x, 6), round(usable_y, 6), round(bed_z, 6)],
        "heightFraction": round(final_dims[2] / bed_z, 6),
        "slenderness": round(final_dims[2] / max(min(final_dims[0], final_dims[1]), _EPS), 6),
        "appliedRotation": {
            "layAxis": selected["layAxis"],
            "layDegrees": selected["layDegrees"],
            "zDegrees": selected["zDegrees"],
        },
        "testedOrientationCount": len(tested),
    }
    return final_values, assessment
