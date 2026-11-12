"""Test double for the OrcaSlicer CLI.

Installed by the API tests as ORCA_BIN together with a stub ``xvfb-run`` on PATH.
It understands only the argument shapes app.main produces:

* ``--export-3mf <path> <stl>...`` writes a minimal editable 3MF with one build
  item per STL argument (what /v1/project inspects and repairs);
* ``--slice 0 ... --outputdir <dir> <input>`` writes one G-code file with the
  header lines the summary parser reads plus extrusion moves tagged with Orca
  feature roles, including a support role.

Environment switches: FAKE_ORCA_SLEEP (seconds), FAKE_ORCA_FAIL (non-empty →
exit 1 with a diagnostic), FAKE_ORCA_STDERR (text echoed to stderr),
FAKE_ORCA_NO_SUPPORT (non-empty → no support extrusion),
FAKE_ORCA_RECORD (path → one JSON line per invocation with argv and the loaded
process profile, which the job directory deletes before a test can read it),
FAKE_ORCA_NO_OBJECTS_WITHOUT_ROTATIONS (non-empty → a slice with
--allow-rotations=0 ends like the real arranger that could not place a long part:
result.json return_code -50 and exit status 206).
"""

import json
import os
import sys
import time
import zipfile
from pathlib import Path

MODEL_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
{objects}
  </resources>
  <build>
{items}
  </build>
</model>"""

OBJECT_TEMPLATE = """    <object id="{id}" type="model"><mesh><vertices>
      <vertex x="0" y="0" z="0"/><vertex x="20" y="0" z="0"/>
      <vertex x="20" y="20" z="20"/><vertex x="0" y="20" z="20"/>
    </vertices></mesh></object>"""

GCODE = """; estimated printing time (normal mode) = 1h 2m 3s
; total filament used [g] = 12.5
; total layer number = 2
G90
M83
G1 X0 Y0 Z0.2 E0
; FEATURE: Outer wall
G1 X10 Y0 E1.0
; FEATURE: Sparse infill
G1 X10 Y10 E1.0
{support}"""

SUPPORT_LINES = """; FEATURE: Support
G1 X0 Y10 E0.5
; FEATURE: Support interface
G1 X0 Y0 E0.5
"""


NO_SUITABLE_OBJECTS = -50
NO_SUITABLE_OBJECTS_MESSAGE = (
    "No object is fully inside the print volume. Arrange ran without rotations."
    " If an object only fits the bed when rotated, pass --allow-rotations."
    " Otherwise the plate is empty or an object is larger than the printable area or height."
)


def record_invocation(argv: list[str]) -> None:
    record = os.environ.get("FAKE_ORCA_RECORD")
    if not record:
        return
    process = None
    if "--load-settings" in argv:
        for setting in argv[argv.index("--load-settings") + 1].split(";"):
            try:
                loaded = json.loads(Path(setting).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if loaded.get("type") == "process":
                process = loaded
    with Path(record).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "process": process}) + "\n")


def main(argv: list[str]) -> int:
    record_invocation(argv)
    sleep = float(os.environ.get("FAKE_ORCA_SLEEP", "0") or 0)
    if sleep:
        time.sleep(sleep)
    if os.environ.get("FAKE_ORCA_STDERR"):
        print(os.environ["FAKE_ORCA_STDERR"], file=sys.stderr)
    output_dir = Path(argv[argv.index("--outputdir") + 1]) if "--outputdir" in argv else None
    if os.environ.get("FAKE_ORCA_NO_OBJECTS_WITHOUT_ROTATIONS") and "--slice" in argv and "--allow-rotations=0" in argv and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(json.dumps({"return_code": NO_SUITABLE_OBJECTS, "error_string": NO_SUITABLE_OBJECTS_MESSAGE, "plate_index": 1}))
        print(f"plate 1: {NO_SUITABLE_OBJECTS_MESSAGE}", file=sys.stderr)
        return NO_SUITABLE_OBJECTS & 0xFF
    if os.environ.get("FAKE_ORCA_FAIL"):
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps({"return_code": 1, "error_string": "fake failure"}))
        print("fake orca: simulated failure", file=sys.stderr)
        return 1
    if "--export-3mf" in argv:
        project = Path(argv[argv.index("--export-3mf") + 1])
        sources = [item for item in argv[argv.index("--export-3mf") + 2 :] if item.endswith(".stl")]
        objects = "\n".join(OBJECT_TEMPLATE.format(id=2 * (index + 1)) for index in range(len(sources)))
        items = "\n".join(
            f'    <item objectid="{2 * (index + 1)}" transform="1 0 0 0 1 0 0 0 1 {30 * index} 0 0" printable="1"/>'
            for index in range(len(sources))
        )
        settings = "".join(f'<object id="{2 * (index + 1)}"/>' for index in range(len(sources)))
        with zipfile.ZipFile(project, "w") as archive:
            archive.writestr("3D/3dmodel.model", MODEL_TEMPLATE.format(objects=objects, items=items))
            archive.writestr("Metadata/model_settings.config", f'<?xml version="1.0" encoding="UTF-8"?><config>{settings}<plate><metadata key="plater_id" value="1"/></plate><assemble/></config>')
            archive.writestr("Metadata/project_settings.config", json.dumps({"printable_height": "300", "printer_settings_id": "fake"}))
            archive.writestr("Metadata/machine_settings_1.json", "{}")
            archive.writestr("Metadata/process_settings_1.json", "{}")
            archive.writestr("Metadata/filament_settings_1.json", "{}")
        return 0
    if "--slice" in argv and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        support = "" if os.environ.get("FAKE_ORCA_NO_SUPPORT") else SUPPORT_LINES
        (output_dir / "plate_1.gcode").write_text(GCODE.format(support=support))
        (output_dir / "result.json").write_text(json.dumps({"return_code": 0}))
        return 0
    print("fake orca: unsupported arguments", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
