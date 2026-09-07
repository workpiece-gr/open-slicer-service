#!/usr/bin/env python3
"""Generate deterministic ASCII STL fixtures for Workpiece RatRig qualification.

These fixtures are test inputs only. They do not qualify any printer and carry
no production authority.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path


def _normal(a, b, c):
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _fmt(value):
    if abs(value) < 5e-12:
        value = 0.0
    return f"{value:.6f}"


class Mesh:
    def __init__(self, name):
        self.name = name
        self.faces = []

    def tri(self, a, b, c):
        self.faces.append((tuple(a), tuple(b), tuple(c)))

    def quad(self, a, b, c, d):
        self.tri(a, b, c)
        self.tri(a, c, d)

    def box(self, x0, y0, z0, x1, y1, z1):
        p000 = (x0, y0, z0)
        p100 = (x1, y0, z0)
        p110 = (x1, y1, z0)
        p010 = (x0, y1, z0)
        p001 = (x0, y0, z1)
        p101 = (x1, y0, z1)
        p111 = (x1, y1, z1)
        p011 = (x0, y1, z1)
        self.quad(p000, p010, p110, p100)
        self.quad(p001, p101, p111, p011)
        self.quad(p000, p100, p101, p001)
        self.quad(p010, p011, p111, p110)
        self.quad(p000, p001, p011, p010)
        self.quad(p100, p110, p111, p101)

    def cylinder(self, cx, cy, z0, z1, radius, segments=48):
        bottom = []
        top = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            bottom.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), z0))
            top.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), z1))
        cb = (cx, cy, z0)
        ct = (cx, cy, z1)
        for i in range(segments):
            j = (i + 1) % segments
            self.tri(cb, bottom[j], bottom[i])
            self.tri(ct, top[i], top[j])
            self.quad(bottom[i], bottom[j], top[j], top[i])

    def ring(self, cx, cy, z0, z1, inner_radius, outer_radius, segments=64):
        ib, it, ob, ot = [], [], [], []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            ca, sa = math.cos(angle), math.sin(angle)
            ib.append((cx + inner_radius * ca, cy + inner_radius * sa, z0))
            it.append((cx + inner_radius * ca, cy + inner_radius * sa, z1))
            ob.append((cx + outer_radius * ca, cy + outer_radius * sa, z0))
            ot.append((cx + outer_radius * ca, cy + outer_radius * sa, z1))
        for i in range(segments):
            j = (i + 1) % segments
            self.quad(ob[i], ob[j], ot[j], ot[i])
            self.quad(ib[j], ib[i], it[i], it[j])
            self.quad(ot[i], ot[j], it[j], it[i])
            self.quad(ob[j], ob[i], ib[i], ib[j])

    def write(self, path):
        lines = [f"solid {self.name}"]
        for a, b, c in self.faces:
            n = _normal(a, b, c)
            lines.append(f"  facet normal {_fmt(n[0])} {_fmt(n[1])} {_fmt(n[2])}")
            lines.append("    outer loop")
            for p in (a, b, c):
                lines.append(f"      vertex {_fmt(p[0])} {_fmt(p[1])} {_fmt(p[2])}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {self.name}")
        path.write_text("\n".join(lines) + "\n", encoding="ascii")


def dimensions_fixture():
    mesh = Mesh("workpiece_ratrig_dimensions_v1")
    mesh.box(0, 0, 0, 20, 20, 20)
    mesh.box(30, 0, 0, 130, 10, 5)
    mesh.box(140, 0, 0, 150, 10, 40)
    return mesh


def holes_fixture():
    mesh = Mesh("workpiece_ratrig_holes_v1")
    specs = [(5, 7.5), (10, 10), (20, 15)]
    x = 0
    for inner_diameter, outer_radius in specs:
        inner_radius = inner_diameter / 2
        mesh.ring(x + outer_radius, outer_radius, 0, 5, inner_radius, outer_radius)
        mesh.cylinder(x + outer_radius, outer_radius + 35, 0, 10, inner_radius)
        x += outer_radius * 2 + 12
    return mesh


def bridge_support_fixture():
    mesh = Mesh("workpiece_ratrig_bridge_support_v1")
    # Three independent closed bodies. The 0.20 mm vertical separation keeps
    # the STL manifold while forcing automatic-support handling of a 40 mm span.
    mesh.box(0, 0, 0, 10, 12, 19.8)
    mesh.box(50, 0, 0, 60, 12, 19.8)
    mesh.box(0, 0, 20, 60, 12, 25)
    return mesh


FIXTURES = {
    "qualification-dimensions-v1.stl": dimensions_fixture,
    "qualification-holes-v1.stl": holes_fixture,
    "qualification-bridge-support-v1.stl": bridge_support_fixture,
}


def generate(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, factory in FIXTURES.items():
        factory().write(output_dir / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
