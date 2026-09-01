"""AGPL HTTP wrapper for a pinned OrcaSlicer CLI build."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .project_builder import (
    build_project_command,
    fits_axis_permutation,
    inspect_project_3mf,
    inspect_stl,
    repair_project_plate_layout,
    sha256_file,
    verify_project_command,
)


QUALITY = {
    "draft": {"label": "Draft", "layer_height": "0.28"},
    "balanced": {"label": "Balanced", "layer_height": "0.20"},
    "fine": {"label": "Fine", "layer_height": "0.12"},
}
STRENGTH = {
    "prototype": {"label": "Prototype", "wall_loops": "2"},
    "functional": {"label": "Functional", "wall_loops": "3"},
    "load_bearing": {"label": "Load bearing", "wall_loops": "5"},
}

ORCA_VERSION = os.getenv("ORCA_VERSION", "2.4.2")
ORCA_BIN = Path(os.getenv("ORCA_BIN", "/opt/orca/squashfs-root/AppRun"))
PROFILE_ROOT = Path(os.getenv("PROFILE_ROOT", "/app/profiles"))
SOURCE_CODE_URL = os.getenv("SOURCE_CODE_URL", "")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
SLICE_TIMEOUT_SECONDS = int(os.getenv("SLICE_TIMEOUT_SECONDS", "180"))
MAX_PREVIEW_MOVES = int(os.getenv("MAX_PREVIEW_MOVES", "30000"))
MAX_PROJECT_QUANTITY = int(os.getenv("MAX_PROJECT_QUANTITY", "50"))
MAX_PROJECT_BYTES = int(os.getenv("MAX_PROJECT_BYTES", str(80 * 1024 * 1024)))
ORCA_RESOURCE_ROOT = Path(os.getenv("ORCA_RESOURCE_ROOT", "/opt/orca/squashfs-root/resources/profiles"))
ENABLE_EXPERIMENTAL_PROJECT_API = os.getenv("ENABLE_EXPERIMENTAL_PROJECT_API", "0").strip().lower() in {"1", "true", "yes"}
WORKPIECE_PROJECT_API_TOKEN = os.getenv("WORKPIECE_PROJECT_API_TOKEN", "").strip()
PROJECT_QUEUE_TIMEOUT_SECONDS = max(1, int(os.getenv("PROJECT_QUEUE_TIMEOUT_SECONDS", "300")))
SERVICE_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip() or os.getenv("SOURCE_COMMIT_SHA", "").strip()
PROJECT_GENERATION_LOCK = threading.Lock()

MATERIALS = {
    "pla": {
        "label": "PLA",
        "process": PROFILE_ROOT / "process" / "pla.json",
        "filament": PROFILE_ROOT / "filament" / "pla.json",
    },
    "petg": {
        "label": "PETG",
        "process": PROFILE_ROOT / "process" / "petg.json",
        "filament": PROFILE_ROOT / "filament" / "petg.json",
    },
    "pctg": {
        "label": "PCTG",
        "process": PROFILE_ROOT / "process" / "pctg.json",
        "filament": PROFILE_ROOT / "filament" / "pctg.json",
    },
    "abs": {
        "label": "ABS",
        "process": PROFILE_ROOT / "process" / "abs.json",
        "filament": PROFILE_ROOT / "filament" / "abs.json",
    },
    "tpu": {
        "label": "TPU",
        "process": PROFILE_ROOT / "process" / "tpu.json",
        "filament": PROFILE_ROOT / "filament" / "tpu.json",
    },
}
PROFILE_FILES = {"machine": PROFILE_ROOT / "machine.json"}
for material_key, material_profile in MATERIALS.items():
    PROFILE_FILES[f"{material_key}_process"] = material_profile["process"]
    PROFILE_FILES[f"{material_key}_filament"] = material_profile["filament"]

ENDER_GENERIC_ROOT = ORCA_RESOURCE_ROOT / "Creality"
ENDER_GENERIC_MACHINE = ENDER_GENERIC_ROOT / "machine" / "Creality Ender-3 0.4 nozzle.json"
ENDER_GENERIC_PROCESS = ENDER_GENERIC_ROOT / "process" / "0.20mm Standard @Creality Ender3 0.4.json"
ENDER_GENERIC_FILAMENTS = {
    "pla": ENDER_GENERIC_ROOT / "filament" / "Creality Generic PLA.json",
    "petg": ENDER_GENERIC_ROOT / "filament" / "Creality Generic PETG.json",
    "abs": ENDER_GENERIC_ROOT / "filament" / "Creality Generic ABS.json",
    "tpu": ENDER_GENERIC_ROOT / "filament" / "Creality Generic TPU.json",
}
ENDER_GENERIC_FILAMENT_COMMON = ENDER_GENERIC_ROOT / "filament" / "fdm_filament_common.json"
ENDER_GENERIC_FILAMENT_BASES = {
    "pla": ENDER_GENERIC_ROOT / "filament" / "fdm_filament_pla.json",
    "petg": ENDER_GENERIC_ROOT / "filament" / "fdm_filament_pet.json",
    "abs": ENDER_GENERIC_ROOT / "filament" / "fdm_filament_abs.json",
    "tpu": ENDER_GENERIC_ROOT / "filament" / "fdm_filament_tpu.json",
}
PROJECT_PRINTERS = {
    "ratrig_vcore3_300": {
        "label": "RatRig V-Core 3 300 / 0.4 mm",
        "envelope_mm": (300.0, 300.0, 300.0),
        "temporary_generic": False,
        "materials": tuple(MATERIALS.keys()),
    },
    "ender3_generic_235": {
        "label": "Ender 3 generic / 0.4 mm (temporary)",
        "envelope_mm": (235.0, 235.0, 235.0),
        "temporary_generic": True,
        "materials": tuple(ENDER_GENERIC_FILAMENTS.keys()),
    },
}

app = FastAPI(
    title="Open Slicer Service",
    version="0.3.0",
    license_info={"name": "GNU AGPL-3.0-or-later", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
)

origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def source_url_is_public() -> bool:
    parsed = urlparse(SOURCE_CODE_URL)
    return parsed.scheme == "https" and parsed.netloc != "" and "YOUR-NAME" not in SOURCE_CODE_URL


def profiles_ready() -> bool:
    return all(path.is_file() and path.stat().st_size > 20 for path in PROFILE_FILES.values())


def ender_generic_profiles_ready() -> bool:
    paths = [
        ENDER_GENERIC_MACHINE,
        ENDER_GENERIC_PROCESS,
        ENDER_GENERIC_FILAMENT_COMMON,
        *ENDER_GENERIC_FILAMENT_BASES.values(),
        *ENDER_GENERIC_FILAMENTS.values(),
    ]
    return all(path.is_file() and path.stat().st_size > 20 for path in paths)


def project_access(authorization: Annotated[str | None, Header()] = None):
    if not ENABLE_EXPERIMENTAL_PROJECT_API:
        raise HTTPException(status_code=404, detail="The project builder is not enabled.")
    if not WORKPIECE_PROJECT_API_TOKEN:
        raise HTTPException(status_code=503, detail="The project builder service token is not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied.strip(), WORKPIECE_PROJECT_API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="A valid project-builder service token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not PROJECT_GENERATION_LOCK.acquire(timeout=PROJECT_QUEUE_TIMEOUT_SECONDS):
        raise HTTPException(status_code=503, detail="The project-builder queue is busy. Retry later.")
    try:
        yield
    finally:
        PROJECT_GENERATION_LOCK.release()


@app.get("/")
def root() -> dict:
    return {
        "service": "Open Slicer Service",
        "engine": f"OrcaSlicer {ORCA_VERSION}",
        "license": "AGPL-3.0-or-later",
        "source": SOURCE_CODE_URL or None,
        "docs": "/docs",
        "printer": "RatRig V-Core 3 300 / 0.4 mm nozzle",
        "materials": {key: profile["label"] for key, profile in MATERIALS.items()},
    }


@app.get("/health")
def health() -> dict:
    engine_ready = ORCA_BIN.is_file()
    profile_state = profiles_ready()
    source_ready = source_url_is_public()
    return {
        "ok": engine_ready and profile_state and source_ready,
        "engine": f"OrcaSlicer {ORCA_VERSION}",
        "engine_ready": engine_ready,
        "profiles_ready": profile_state,
        "source_ready": source_ready,
        "profile_files": {name: path.is_file() for name, path in PROFILE_FILES.items()},
        "project_3mf": {
            "experimental": True,
            "enabled": ENABLE_EXPERIMENTAL_PROJECT_API,
            "authenticated": bool(WORKPIECE_PROJECT_API_TOKEN),
            "concurrency": 1,
            "queue_timeout_seconds": PROJECT_QUEUE_TIMEOUT_SECONDS,
            "ender_generic_profiles_ready": ender_generic_profiles_ready(),
            "printers": {key: {"label": value["label"], "envelope_mm": value["envelope_mm"], "temporary_generic": value["temporary_generic"]} for key, value in PROJECT_PRINTERS.items()},
        },
    }


@app.get("/source", response_class=RedirectResponse)
def source() -> RedirectResponse:
    if not source_url_is_public():
        raise HTTPException(status_code=503, detail="SOURCE_CODE_URL is not configured to the public corresponding source.")
    return RedirectResponse(SOURCE_CODE_URL, status_code=307)


def build_process_profile(
    base_path: Path,
    quality: str,
    strength: str,
    destination: Path,
    material_label: str = "",
    automatic_supports: bool = False,
    project_reopen_safe: bool = False,
    single_colour_project: bool = False,
) -> dict:
    try:
        profile = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid process profile: {exc}") from exc
    if profile.get("type") != "process":
        raise RuntimeError(f"{base_path} must have type=process")
    material_prefix = f"{material_label} / " if material_label else ""
    profile.update(
        {
            "name": f"Open Slicer {material_prefix}{QUALITY[quality]['label']} / {STRENGTH[strength]['label']}",
            "from": "user",
            "instantiation": "true",
            "layer_height": QUALITY[quality]["layer_height"],
            "wall_loops": STRENGTH[strength]["wall_loops"],
            "sparse_infill_density": "20%",
        }
    )
    if automatic_supports:
        support_type = str(profile.get("support_type") or "tree(auto)").replace("(manual)", "(auto)")
        if "(auto)" not in support_type:
            support_type = "tree(auto)"
        profile.update(
            {
                "enable_support": "1",
                "support_type": support_type,
                "support_on_build_plate_only": "0",
            }
        )
    if single_colour_project:
        # Workpiece CP2b currently accepts a single-colour STL/material job.
        # A prime/wipe tower has no purpose here and can push generated G-code
        # outside the printable area on a 300 mm RatRig bed.
        profile["enable_prime_tower"] = "0"
    if project_reopen_safe:
        # OrcaSlicer 2.4.2 validates reopened project 3MFs more strictly than
        # the initial export. With relative extrusion on Marlin it requires an
        # exact uppercase G92 E0 in before_layer_change_gcode or
        # layer_change_gcode. Use the latter and preserve any existing script.
        layer_change_gcode = str(profile.get("layer_change_gcode") or "")
        if "G92 E0" not in layer_change_gcode:
            profile["layer_change_gcode"] = (layer_change_gcode.rstrip() + "\nG92 E0").lstrip()
    destination.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return profile


async def save_upload(upload: UploadFile, destination: Path) -> int:
    size = 0
    with destination.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"STL exceeds the {MAX_UPLOAD_BYTES} byte upload limit.")
            output.write(chunk)
    if size < 84:
        raise HTTPException(status_code=400, detail="The uploaded STL is empty or incomplete.")
    return size


def build_ender_generic_machine(destination: Path) -> dict:
    try:
        profile = json.loads(ENDER_GENERIC_MACHINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid bundled Ender 3 machine profile: {exc}") from exc
    if profile.get("type") != "machine":
        raise RuntimeError("The bundled Ender 3 profile must have type=machine")
    # Temporary Workpiece envelope requested for the two Ender 3-class machines.
    # Replace this override with the real printer profiles before production authority.
    # Make this a real user preset that inherits the official Orca Ender-3
    # system preset. Orca's CLI compares a user machine's inherits value to the
    # process compatible_printers list; inheriting fdm_creality_common directly
    # incorrectly makes the stock Ender process look incompatible.
    profile.update(
        {
            "name": "Workpiece Ender 3 235 temporary",
            "from": "user",
            "inherits": "Creality Ender-3 0.4 nozzle",
            "instantiation": "true",
            "printer_settings_id": "Workpiece Ender 3 235 temporary",
            "printable_area": ["0x0", "235x0", "235x235", "0x235"],
            "printable_height": "235",
            "printer_notes": "Workpiece temporary generic Ender 3 profile; replace with measured production profile.",
        }
    )
    destination.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return profile


def build_ender_generic_filament(material: str, destination: Path) -> dict:
    if material not in ENDER_GENERIC_FILAMENTS:
        raise HTTPException(status_code=422, detail=f"The temporary Ender 3 profile does not support {material.upper()}.")
    paths = [
        ENDER_GENERIC_FILAMENT_COMMON,
        ENDER_GENERIC_FILAMENT_BASES[material],
        ENDER_GENERIC_FILAMENTS[material],
    ]
    merged: dict = {}
    try:
        for source in paths:
            merged.update(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid bundled Ender 3 filament profile chain: {exc}") from exc
    if merged.get("type") != "filament":
        raise RuntimeError("The resolved Ender 3 filament profile must have type=filament")
    merged.pop("inherits", None)
    merged["from"] = "user"
    merged["instantiation"] = "true"
    merged["filament_settings_id"] = [str(merged.get("name") or f"Workpiece {material.upper()}")]
    destination.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return merged


def project_profile_paths(printer: str, material: str, job: Path, quality: str, strength: str) -> tuple[Path, Path, Path]:
    if printer == "ratrig_vcore3_300":
        if not profiles_ready():
            raise HTTPException(status_code=503, detail="Validated RatRig profiles are not installed.")
        material_profile = MATERIALS[material]
        machine_path = PROFILE_FILES["machine"]
        process_base = material_profile["process"]
        filament_path = material_profile["filament"]
    elif printer == "ender3_generic_235":
        if material not in ENDER_GENERIC_FILAMENTS:
            raise HTTPException(status_code=422, detail=f"The temporary Ender 3 profile does not yet support {material.upper()}.")
        required = (
            ENDER_GENERIC_MACHINE,
            ENDER_GENERIC_PROCESS,
            ENDER_GENERIC_FILAMENT_COMMON,
            ENDER_GENERIC_FILAMENT_BASES[material],
            ENDER_GENERIC_FILAMENTS[material],
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise HTTPException(status_code=503, detail="The pinned OrcaSlicer Ender 3 generic profiles are not available.")
        machine_path = job / "ender3-machine.json"
        build_ender_generic_machine(machine_path)
        process_base = ENDER_GENERIC_PROCESS
        filament_path = job / "ender3-filament.json"
        build_ender_generic_filament(material, filament_path)
    else:
        raise HTTPException(status_code=422, detail=f"Unknown project printer: {printer}")

    process_path = job / "project-process.json"
    material_label = MATERIALS[material]["label"] if material in MATERIALS else material.upper()
    build_process_profile(
        process_base,
        quality,
        strength,
        process_path,
        str(material_label),
        automatic_supports=True,
        project_reopen_safe=True,
        single_colour_project=True,
    )
    return machine_path, process_path, filament_path


def choose_project_printer(requested: str, material: str, dimensions_mm: list[float]) -> str:
    if requested != "auto":
        if requested not in PROJECT_PRINTERS:
            raise HTTPException(status_code=422, detail=f"printer must be auto or one of: {', '.join(PROJECT_PRINTERS)}")
        profile = PROJECT_PRINTERS[requested]
        if material not in profile["materials"]:
            raise HTTPException(status_code=422, detail=f"{material.upper()} is not supported by {requested}.")
        if not fits_axis_permutation(dimensions_mm, profile["envelope_mm"]):
            raise HTTPException(status_code=422, detail=f"The STL does not fit the configured {requested} envelope.")
        return requested
    ender = PROJECT_PRINTERS["ender3_generic_235"]
    if material in ender["materials"] and fits_axis_permutation(dimensions_mm, ender["envelope_mm"]):
        return "ender3_generic_235"
    ratrig = PROJECT_PRINTERS["ratrig_vcore3_300"]
    if fits_axis_permutation(dimensions_mm, ratrig["envelope_mm"]):
        return "ratrig_vcore3_300"
    raise HTTPException(status_code=422, detail="The STL exceeds both configured Workpiece FDM envelopes before Orca orientation.")


def isolated_orca_env(job: Path) -> dict[str, str]:
    xdg_root = job / "xdg"
    xdg_config, xdg_cache, xdg_data = xdg_root / "config", xdg_root / "cache", xdg_root / "data"
    for directory in (xdg_config, xdg_cache, xdg_data):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_DATA_HOME": str(xdg_data),
    }


def run_orca(command: list[str], *, cwd: Path, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"OrcaSlicer exceeded {timeout} seconds.") from exc
    if completed.returncode != 0:
        if env.get("ORCA_DEBUG_LOGS") == "1":
            print("ORCA DEBUG command:", " ".join(command), flush=True)
            print("ORCA DEBUG stdout:", (completed.stdout or "")[-10000:], flush=True)
            print("ORCA DEBUG stderr:", (completed.stderr or "")[-10000:], flush=True)
            if "--outputdir" in command:
                try:
                    output_dir = Path(command[command.index("--outputdir") + 1])
                    result_file = output_dir / "result.json"
                    if result_file.is_file():
                        print("ORCA DEBUG result.json:", result_file.read_text(encoding="utf-8", errors="ignore")[-8000:], flush=True)
                except Exception as debug_exc:
                    print("ORCA DEBUG output inspection failed:", repr(debug_exc), flush=True)
        raise HTTPException(status_code=422, detail="OrcaSlicer could not build or verify the requested project.")
    return completed


def find_outputs(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.gcode"), key=lambda item: item.name)


def parse_duration_seconds(value: str) -> int | None:
    match = re.search(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?", value.strip(), re.I)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(item or 0) for item in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_gcode_summary(path: Path) -> dict:
    summary = {"print_time_seconds": None, "filament_grams": None, "layer_count": None}
    time_patterns = (
        re.compile(r"estimated printing time.*?=\s*(.+)$", re.I),
        re.compile(r"(?:model|total) printing time\s*:?\s*(.+)$", re.I),
    )
    filament_pattern = re.compile(r"(?:total )?filament used \[g\]\s*=\s*([\d.]+)", re.I)
    layer_pattern = re.compile(r"(?:total layer number|total_layer_count)\s*[:=]\s*(\d+)", re.I)
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            for pattern in time_patterns:
                if match := pattern.search(line):
                    parsed = parse_duration_seconds(match.group(1))
                    if parsed is not None:
                        summary["print_time_seconds"] = parsed
            if match := filament_pattern.search(line):
                summary["filament_grams"] = float(match.group(1))
            if match := layer_pattern.search(line):
                summary["layer_count"] = int(match.group(1))
    return summary


def _iter_extrusions(path: Path):
    x = y = z = e = 0.0
    xyz_absolute = True
    e_absolute = True
    feature = "Unclassified"
    feature_re = re.compile(r";\s*(?:FEATURE|TYPE)\s*:\s*(.+)", re.I)
    axis_re = re.compile(r"([XYZE])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if match := feature_re.search(line):
                feature = match.group(1).strip()
            code = line.split(";", 1)[0].strip()
            if code == "G90":
                xyz_absolute = True
                continue
            if code == "G91":
                xyz_absolute = False
                continue
            if code == "M82":
                e_absolute = True
                continue
            if code == "M83":
                e_absolute = False
                continue
            if code.startswith("G92"):
                values = {axis: float(value) for axis, value in axis_re.findall(code)}
                x, y, z, e = values.get("X", x), values.get("Y", y), values.get("Z", z), values.get("E", e)
                continue
            if not (code.startswith("G0 ") or code.startswith("G1 ")):
                continue
            values = {axis: float(value) for axis, value in axis_re.findall(code)}
            nx = values.get("X", x) if xyz_absolute else x + values.get("X", 0.0)
            ny = values.get("Y", y) if xyz_absolute else y + values.get("Y", 0.0)
            nz = values.get("Z", z) if xyz_absolute else z + values.get("Z", 0.0)
            ne = values.get("E", e) if e_absolute else e + values.get("E", 0.0)
            if ne > e + 1e-7 and (nx != x or ny != y):
                yield (x, y, nx, ny, nz, feature)
            x, y, z, e = nx, ny, nz, ne


def sample_toolpath(path: Path, limit: int) -> dict:
    count = sum(1 for _ in _iter_extrusions(path))
    stride = max(1, math.ceil(count / max(1, limit)))
    features: list[str] = []
    feature_ids: dict[str, int] = {}
    moves: list[list[float | int]] = []
    min_x = min_y = min_z = math.inf
    max_x = max_y = max_z = -math.inf
    for index, (x1, y1, x2, y2, z, feature) in enumerate(_iter_extrusions(path)):
        min_x, min_y, min_z = min(min_x, x1, x2), min(min_y, y1, y2), min(min_z, z)
        max_x, max_y, max_z = max(max_x, x1, x2), max(max_y, y1, y2), max(max_z, z)
        if index % stride:
            continue
        feature_id = feature_ids.get(feature)
        if feature_id is None:
            feature_id = len(features)
            feature_ids[feature] = feature_id
            features.append(feature)
        moves.append([round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3), round(z, 3), feature_id])
    bounds = None if count == 0 else {"min": [min_x, min_y, min_z], "max": [max_x, max_y, max_z]}
    return {"features": features, "moves": moves, "bounds": bounds, "total_extrusion_moves": count, "sample_stride": stride}


def find_output(directory: Path) -> Path:
    candidates = sorted(directory.rglob("*.gcode"), key=lambda item: item.stat().st_size, reverse=True)
    if not candidates:
        raise RuntimeError("OrcaSlicer completed without producing a .gcode file.")
    return candidates[0]


@app.post("/v1/project")
async def build_project(
    _project_access: Annotated[None, Depends(project_access)],
    file: Annotated[UploadFile, File(description="Single-colour STL")],
    material: Annotated[str, Form()] = "pla",
    quality: Annotated[str, Form()] = "balanced",
    strength: Annotated[str, Form()] = "functional",
    quantity: Annotated[int, Form()] = 1,
    printer: Annotated[str, Form()] = "auto",
    verify: Annotated[bool, Form()] = True,
) -> dict:
    if material not in MATERIALS:
        raise HTTPException(status_code=422, detail=f"material must be one of: {', '.join(MATERIALS)}")
    if quality not in QUALITY:
        raise HTTPException(status_code=422, detail=f"quality must be one of: {', '.join(QUALITY)}")
    if strength not in STRENGTH:
        raise HTTPException(status_code=422, detail=f"strength must be one of: {', '.join(STRENGTH)}")
    if quantity < 1 or quantity > MAX_PROJECT_QUANTITY:
        raise HTTPException(status_code=422, detail=f"quantity must be between 1 and {MAX_PROJECT_QUANTITY}")
    filename = file.filename or "upload.stl"
    if Path(filename).suffix.lower() != ".stl":
        raise HTTPException(status_code=415, detail="The experimental project builder accepts STL files only.")
    if not ORCA_BIN.is_file():
        raise HTTPException(status_code=503, detail="The pinned OrcaSlicer binary is not available.")

    with tempfile.TemporaryDirectory(prefix="open-project-") as temporary:
        job = Path(temporary)
        input_path = job / "source-original.stl"
        upload_size = await save_upload(file, input_path)
        try:
            inspection = inspect_stl(input_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        selected_printer = choose_project_printer(printer, material, inspection["dimensions_mm"])
        machine_path, process_path, filament_path = project_profile_paths(selected_printer, material, job, quality, strength)
        project_path = job / "workpiece-production.3mf"
        env = isolated_orca_env(job)

        instance_paths = [input_path]
        for index in range(2, quantity + 1):
            instance_path = job / f"source-instance-{index:03d}.stl"
            try:
                os.link(input_path, instance_path)
            except OSError:
                # Hardlinks are normally available in the job temp directory.
                # Fall back to the same immutable path rather than copying a
                # potentially large STL N times.
                instance_path = input_path
            instance_paths.append(instance_path)
        export_command = build_project_command(
            orca_bin=ORCA_BIN,
            machine_profile=machine_path,
            process_profile=process_path,
            filament_profile=filament_path,
            sources=instance_paths,
            project_path=project_path,
            # Keep Orca's 3D auto-orientation. For RatRig, deterministic
            # post-export layout repair fixes the known 2.4.2 CLI placement
            # defect while preserving Orca's selected orientation matrix.
            auto_orient=True,
            # Do not let the RatRig arranger add arbitrary Z rotations. The
            # 2.4.2 CLI has produced wasteful 45-degree placement for long
            # rectangular parts here.
            allow_arrange_rotations=selected_printer != "ratrig_vcore3_300",
        )
        run_orca(export_command, cwd=job, timeout=SLICE_TIMEOUT_SECONDS, env=env)
        if not project_path.is_file():
            candidates = sorted(job.rglob("*.3mf"), key=lambda item: item.stat().st_size, reverse=True)
            if not candidates:
                raise HTTPException(status_code=422, detail="OrcaSlicer completed without exporting an editable project 3MF.")
            project_path = candidates[0]

        layout_repair = None
        if selected_printer == "ratrig_vcore3_300":
            try:
                layout_repair = repair_project_plate_layout(
                    project_path,
                    envelope_mm=PROJECT_PRINTERS[selected_printer]["envelope_mm"],
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        project_bytes = project_path.stat().st_size
        if project_bytes <= 0 or project_bytes > MAX_PROJECT_BYTES:
            raise HTTPException(status_code=422, detail="The generated project 3MF is outside the supported size limit.")
        try:
            project_inspection = inspect_project_3mf(project_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if project_inspection["instance_count"] != quantity:
            raise HTTPException(
                status_code=422,
                detail=f"Orca project quantity verification failed: expected {quantity}, found {project_inspection['instance_count']}.",
            )
        if not project_inspection["embedded"]["project_settings"]:
            raise HTTPException(status_code=422, detail="The exported 3MF did not embed project settings.")

        if os.getenv("ORCA_DEBUG_LOGS") == "1":
            print("ORCA DEBUG project inspection:", json.dumps(project_inspection, separators=(",", ":")), flush=True)

        verification = {
            "performed": False,
            "reopened_in_fresh_orca_process": False,
            "plate_count": None,
            "plates": [],
            "total_print_time_seconds": None,
            "total_filament_grams": None,
        }
        if verify:
            verification_dir = job / "verify"
            verification_dir.mkdir()
            verify_command = verify_project_command(orca_bin=ORCA_BIN, project_path=project_path, output_dir=verification_dir)
            run_orca(verify_command, cwd=job, timeout=SLICE_TIMEOUT_SECONDS, env=env)
            gcode_files = find_outputs(verification_dir)
            if not gcode_files:
                raise HTTPException(status_code=422, detail="The exported project could not be reopened and sliced by a fresh Orca process.")

            plates = []
            total_time = 0
            total_filament = 0.0
            for index, gcode in enumerate(gcode_files, start=1):
                summary = parse_gcode_summary(gcode)
                if not summary["print_time_seconds"] or not summary["filament_grams"] or not summary["layer_count"]:
                    if os.getenv("ORCA_DEBUG_LOGS") == "1":
                        interesting = []
                        with gcode.open("r", encoding="utf-8", errors="ignore") as handle:
                            for line in handle:
                                lower = line.lower()
                                if any(token in lower for token in ("filament", "printing time", "estimated time", "layer")):
                                    interesting.append(line.rstrip())
                                    if len(interesting) >= 80:
                                        break
                        print("ORCA DEBUG incomplete summary:", summary, flush=True)
                        print("ORCA DEBUG summary lines:\n" + "\n".join(interesting), flush=True)
                    raise HTTPException(status_code=422, detail="Fresh-project verification produced incomplete slice statistics.")
                total_time += int(summary["print_time_seconds"])
                total_filament += float(summary["filament_grams"])
                plates.append(
                    {
                        "index": index,
                        "filename": gcode.name,
                        "summary": summary,
                        "gcode_sha256": sha256_file(gcode),
                    }
                )
            verification = {
                "performed": True,
                "reopened_in_fresh_orca_process": True,
                "plate_count": len(plates),
                "plates": plates,
                "total_print_time_seconds": total_time,
                "total_filament_grams": round(total_filament, 3),
            }

        profile = PROJECT_PRINTERS[selected_printer]
        return {
            "experimental": True,
            "engine": {
                "name": "OrcaSlicer",
                "version": ORCA_VERSION,
                "source": SOURCE_CODE_URL or None,
                "service_commit": SERVICE_COMMIT_SHA or None,
            },
            "source": {
                "filename": Path(filename).name,
                "upload_bytes": upload_size,
                "sha256": sha256_file(input_path),
                "inspection": inspection,
            },
            "printer": {
                "key": selected_printer,
                "label": profile["label"],
                "envelope_mm": profile["envelope_mm"],
                "temporary_generic": profile["temporary_generic"],
                "selection": "automatic_smallest_capable" if printer == "auto" else "requested",
            },
            "request": {
                "material": material,
                "quality": quality,
                "strength": strength,
                "quantity": quantity,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
                "verification_requested": verify,
            },
            "profiles": {
                "machine": {
                    "identity": f"{selected_printer}:machine",
                    "sha256": sha256_file(machine_path),
                },
                "process": {
                    "identity": f"{selected_printer}:{material}:{quality}:{strength}:process",
                    "sha256": sha256_file(process_path),
                },
                "filament": {
                    "identity": f"{selected_printer}:{material}:filament",
                    "sha256": sha256_file(filament_path),
                },
            },
            "project": {
                "filename": "workpiece-production.3mf",
                "media_type": "model/3mf",
                "bytes": project_bytes,
                "sha256": sha256_file(project_path),
                "base64": base64.b64encode(project_path.read_bytes()).decode("ascii"),
                "inspection": project_inspection,
                "layout_repair": layout_repair,
            },
            "verification": verification,
            "warnings": [
                "Experimental CP2b: the editable project 3MF must be manually opened in OrcaSlicer desktop before production use.",
                "The Ender 3 profile is a temporary Orca-derived generic profile with a Workpiece 235 x 235 x 235 mm envelope override; replace it with measured machine profiles.",
                "Automatic orientation, arrangement and supports require acceptance testing with representative Workpiece models before manufacturing authority.",
            ],
        }


@app.post("/v1/slice")
async def slice_model(
    file: Annotated[UploadFile, File(description="Single-colour STL")],
    material: Annotated[str, Form()] = "pla",
    quality: Annotated[str, Form()] = "balanced",
    strength: Annotated[str, Form()] = "functional",
    preoriented: Annotated[bool, Form()] = False,
    printer: Annotated[str, Form()] = "ratrig_vcore3_300",
) -> dict:
    if material not in MATERIALS:
        raise HTTPException(status_code=422, detail=f"material must be one of: {', '.join(MATERIALS)}")
    if quality not in QUALITY:
        raise HTTPException(status_code=422, detail=f"quality must be one of: {', '.join(QUALITY)}")
    if strength not in STRENGTH:
        raise HTTPException(status_code=422, detail=f"strength must be one of: {', '.join(STRENGTH)}")
    if printer not in PROJECT_PRINTERS:
        raise HTTPException(status_code=422, detail=f"printer must be one of: {', '.join(PROJECT_PRINTERS)}")
    if material not in PROJECT_PRINTERS[printer]["materials"]:
        raise HTTPException(status_code=422, detail=f"{material.upper()} is not supported by {printer}.")
    filename = file.filename or "upload.stl"
    if Path(filename).suffix.lower() != ".stl":
        raise HTTPException(status_code=415, detail="The pilot Orca service accepts STL files only.")
    if not ORCA_BIN.is_file():
        raise HTTPException(status_code=503, detail="The pinned OrcaSlicer binary is not available.")
    if printer == "ratrig_vcore3_300":
        if not profiles_ready():
            missing = [name for name, path in PROFILE_FILES.items() if not path.is_file()]
            raise HTTPException(status_code=503, detail=f"Validated RatRig profiles are not installed: {', '.join(missing)}")
    else:
        required = (
            ENDER_GENERIC_MACHINE,
            ENDER_GENERIC_PROCESS,
            ENDER_GENERIC_FILAMENT_COMMON,
            ENDER_GENERIC_FILAMENT_BASES[material],
            ENDER_GENERIC_FILAMENTS[material],
        )
        if any(not path.is_file() or path.stat().st_size <= 20 for path in required):
            raise HTTPException(status_code=503, detail="The pinned OrcaSlicer Ender 3 generic profiles for the requested material are not available.")

    material_profile = MATERIALS[material]
    with tempfile.TemporaryDirectory(prefix="open-slice-") as temporary:
        job = Path(temporary)
        input_path = job / "input.stl"
        output_dir = job / "output"
        output_dir.mkdir()
        upload_size = await save_upload(file, input_path)
        process_path = job / "process.json"
        if printer == "ratrig_vcore3_300":
            machine_path = PROFILE_FILES["machine"]
            filament_path = material_profile["filament"]
            effective = build_process_profile(
                material_profile["process"],
                quality,
                strength,
                process_path,
                str(material_profile["label"]),
            )
        else:
            machine_path = job / "ender3-machine.json"
            build_ender_generic_machine(machine_path)
            filament_path = job / "ender3-filament.json"
            build_ender_generic_filament(material, filament_path)
            effective = build_process_profile(
                ENDER_GENERIC_PROCESS,
                quality,
                strength,
                process_path,
                str(material_profile["label"]),
                automatic_supports=True,
            )
        settings_arg = f"{machine_path};{process_path}"
        xdg_root = job / "xdg"
        xdg_config, xdg_cache, xdg_data = xdg_root / "config", xdg_root / "cache", xdg_root / "data"
        for directory in (xdg_config, xdg_cache, xdg_data):
            directory.mkdir(parents=True, exist_ok=True)
        command = [
            "xvfb-run", "-a", str(ORCA_BIN),
            "--slice", "0",
            "--arrange", "1",
        ]
        if not preoriented:
            command.extend(["--orient", "1"])
        command.extend([
            "--ensure-on-bed",
            "--allow-newer-file",
            "--load-settings", settings_arg,
            "--load-filaments", str(filament_path),
            "--outputdir", str(output_dir),
            str(input_path),
        ])
        try:
            completed = subprocess.run(
                command,
                cwd=job,
                capture_output=True,
                text=True,
                timeout=SLICE_TIMEOUT_SECONDS,
                check=False,
                env={
                    **os.environ,
                    "XDG_CONFIG_HOME": str(xdg_config),
                    "XDG_CACHE_HOME": str(xdg_cache),
                    "XDG_DATA_HOME": str(xdg_data),
                },
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail=f"Slicing exceeded {SLICE_TIMEOUT_SECONDS} seconds.") from exc

        result_path = output_dir / "result.json"
        engine_result = None
        if result_path.is_file():
            try:
                engine_result = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                engine_result = None
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout or "OrcaSlicer failed")[-2000:]
            raise HTTPException(status_code=422, detail={"engine_result": engine_result, "diagnostic": diagnostic})
        try:
            gcode = find_output(output_dir)
        except RuntimeError as exc:
            diagnostic = (completed.stderr or completed.stdout or str(exc))[-2000:]
            raise HTTPException(status_code=422, detail={"engine_result": engine_result, "diagnostic": diagnostic}) from exc

        summary = parse_gcode_summary(gcode)
        preview = sample_toolpath(gcode, MAX_PREVIEW_MOVES)
        return {
            "engine": {"name": "OrcaSlicer", "version": ORCA_VERSION, "license": "AGPL-3.0", "source": SOURCE_CODE_URL or None},
            "request": {
                "filename": Path(filename).name,
                "upload_bytes": upload_size,
                "material": material,
                "quality": quality,
                "strength": strength,
                "preoriented": preoriented,
                "printer": printer,
            },
            "effective_process": {
                "material": material_profile["label"],
                "layer_height_mm": float(effective["layer_height"]),
                "wall_loops": int(effective["wall_loops"]),
                "infill_percent": 20,
                "printer": printer,
                "temporary_generic_machine_profile": bool(PROJECT_PRINTERS[printer]["temporary_generic"]),
            },
            "summary": summary,
            "toolpath": preview,
            "gcode_sha256": hashlib.sha256(gcode.read_bytes()).hexdigest(),
            "warnings": [
                "Sampled preview moves are for visualisation; validate production G-code in OrcaSlicer desktop.",
                "The pilot accepts one STL and one selected material profile per request.",
            ],
        }
