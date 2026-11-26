"""printed_rotations: the orientation of the parts in an exported project, measured from the meshes."""
import struct
import zipfile

from app.project_builder import printed_rotations

# An asymmetric tetrahedron.
POINTS = [(0, 0, 0), (30, 0, 0), (0, 20, 0), (0, 0, 10)]
FACETS = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
FLAT = [1, 0, 0, 0, 1, 0, 0, 0, 1]
SIDE = [1, 0, 0, 0, 0, -1, 0, 1, 0]  # row-major source-to-bed: source Y points up
# 3MF transforms are row-vector matrices: the transpose of the rotation above, then the translation.
SIDE_3MF = "1 0 0 0 0 1 0 -1 0 5 7 2"


def stl(path, ascii_form=False):
    if ascii_form:
        body = "".join(
            "facet normal 0 0 0\nouter loop\n" + "".join("vertex %s %s %s\n" % POINTS[i] for i in f) + "endloop\nendfacet\n"
            for f in FACETS
        )
        path.write_text("solid t\n" + body + "endsolid t\n")
    else:
        records = b"".join(struct.pack("<12fH", 0, 0, 0, *(c for i in f for c in POINTS[i]), 0) for f in FACETS)
        path.write_bytes(bytes(80) + struct.pack("<I", len(FACETS)) + records)
    return path


def project(path, item_transforms, points=POINTS, facets=FACETS):
    mesh = "<mesh><vertices>%s</vertices><triangles>%s</triangles></mesh>" % (
        "".join('<vertex x="%s" y="%s" z="%s"/>' % p for p in points),
        "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % f for f in facets),
    )
    ns = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
    main = '<model %s><resources><object id="2"><components><component p:path="/3D/Objects/part.model" objectid="1"/></components></object></resources><build>%s</build></model>' % (
        ns,
        "".join('<item objectid="2" transform="%s"/>' % t for t in item_transforms),
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", main)
        archive.writestr("3D/Objects/part.model", '<model %s><resources><object id="1">%s</object></resources></model>' % (ns, mesh))
    return path


def test_rotation_in_the_build_transform(tmp_path):
    for ascii_form in (False, True):
        source = stl(tmp_path / "part.stl", ascii_form)
        assert printed_rotations(project(tmp_path / "p.3mf", [SIDE_3MF]), source) == [SIDE]


def test_rotation_baked_into_the_vertices_and_recentred(tmp_path):
    baked = [(x - 4, -z + 9, y + 1) for x, y, z in POINTS]
    out = printed_rotations(project(tmp_path / "p.3mf", ["1 0 0 0 1 0 0 0 1 0 0 0"], baked), stl(tmp_path / "part.stl"))
    assert out == [SIDE]


def test_one_entry_per_distinct_orientation(tmp_path):
    source = stl(tmp_path / "part.stl")
    flat = "1 0 0 0 1 0 0 0 1 90 90 0"
    assert printed_rotations(project(tmp_path / "p.3mf", [flat, SIDE_3MF, flat]), source) == [FLAT, SIDE]


def test_unknown_rather_than_wrong(tmp_path):
    source = stl(tmp_path / "part.stl")
    scaled = [(2 * x, y, z) for x, y, z in POINTS]
    mirrored = [(-x, y, z) for x, y, z in POINTS]
    for number, kwargs in enumerate(({"points": scaled}, {"points": mirrored}, {"facets": FACETS[:3]}, {"facets": FACETS[1:] + FACETS[:1]})):
        out = printed_rotations(project(tmp_path / f"{number}.3mf", ["1 0 0 0 1 0 0 0 1 0 0 0"], **kwargs), source)
        assert out is None, kwargs
    assert printed_rotations(tmp_path / "missing.3mf", source) is None
