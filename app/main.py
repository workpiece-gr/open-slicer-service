"""AGPL HTTP wrapper for a pinned OrcaSlicer CLI build."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse


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


@app.post("/v1/slice")
async def slice_model(
    file: Annotated[UploadFile, File(description="Single-colour STL")],
    material: Annotated[str, Form()] = "pla",
    quality: Annotated[str, Form()] = "balanced",
    strength: Annotated[str, Form()] = "functional",
    preoriented: Annotated[bool, Form()] = False,
) -> dict:
    if material not in MATERIALS:
        raise HTTPException(status_code=422, detail=f"material must be one of: {', '.join(MATERIALS)}")
    if quality not in QUALITY:
        raise HTTPException(status_code=422, detail=f"quality must be one of: {', '.join(QUALITY)}")
    if strength not in STRENGTH:
        raise HTTPException(status_code=422, detail=f"strength must be one of: {', '.join(STRENGTH)}")
    filename = file.filename or "upload.stl"
    if Path(filename).suffix.lower() != ".stl":
        raise HTTPException(status_code=415, detail="The pilot Orca service accepts STL files only.")
    if not ORCA_BIN.is_file():
        raise HTTPException(status_code=503, detail="The pinned OrcaSlicer binary is not available.")
    if not profiles_ready():
        missing = [name for name, path in PROFILE_FILES.items() if not path.is_file()]
        raise HTTPException(status_code=503, detail=f"Validated profiles are not installed: {', '.join(missing)}")

    material_profile = MATERIALS[material]
    with tempfile.TemporaryDirectory(prefix="open-slice-") as temporary:
        job = Path(temporary)
        input_path = job / "input.stl"
        output_dir = job / "output"
        output_dir.mkdir()
        upload_size = await save_upload(file, input_path)
        process_path = job / "process.json"
        effective = build_process_profile(
            material_profile["process"],
            quality,
            strength,
            process_path,
            str(material_profile["label"]),
        )
        settings_arg = f"{PROFILE_FILES['machine']};{process_path}"
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
            "--load-filaments", str(material_profile["filament"]),
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
            },
            "effective_process": {
                "material": material_profile["label"],
                "layer_height_mm": float(effective["layer_height"]),
                "wall_loops": int(effective["wall_loops"]),
                "infill_percent": 20,
            },
            "summary": summary,
            "toolpath": preview,
            "gcode_sha256": hashlib.sha256(gcode.read_bytes()).hexdigest(),
            "warnings": [
                "Sampled preview moves are for visualisation; validate production G-code in OrcaSlicer desktop.",
                "The pilot accepts one STL and one selected material profile per request.",
            ],
        }
