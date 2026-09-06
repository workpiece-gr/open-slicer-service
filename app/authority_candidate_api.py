"""Separate, disabled-by-default HTTP entrypoint for FDM Authority v2 candidates.

This module is not the normal Open Slicer Service application. The existing
``app.main:app`` command and ``/v1/project`` route remain unchanged. A caller
must deliberately start this application and enable its independent feature
flag/token before the route exists operationally.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from .fdm_authority_candidate import FDM_AUTHORITY_CANDIDATE_API_VERSION, build_fdm_authority_candidate
from .main import (
    MATERIALS,
    MAX_PROJECT_BYTES,
    MAX_PROJECT_QUANTITY,
    ORCA_BIN,
    ORCA_VERSION,
    PROJECT_PRINTERS,
    SERVICE_COMMIT_SHA,
    SLICE_TIMEOUT_SECONDS,
    build_project_command,
    choose_project_printer,
    inspect_project_3mf,
    inspect_stl,
    isolated_orca_env,
    parse_gcode_summary,
    profiles_ready,
    project_profile_paths,
    repair_project_plate_layout,
    run_orca,
    save_upload,
    sha256_file,
)

ENABLE_FDM_AUTHORITY_V2_API = os.getenv("ENABLE_FDM_AUTHORITY_V2_API", "0").strip().lower() in {"1", "true", "yes"}
WORKPIECE_FDM_AUTHORITY_V2_TOKEN = os.getenv("WORKPIECE_FDM_AUTHORITY_V2_TOKEN", "").strip()
WORKPIECE_FDM_AUTHORITY_RUNTIME_REF = os.getenv("WORKPIECE_FDM_AUTHORITY_RUNTIME_REF", "").strip().lower()
WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF = os.getenv("WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF", "").strip().lower()
AUTHORITY_QUEUE_TIMEOUT_SECONDS = max(1, int(os.getenv("FDM_AUTHORITY_V2_QUEUE_TIMEOUT_SECONDS", "30")))
MAX_AUTHORITY_BUNDLE_BYTES = max(1, int(os.getenv("MAX_FDM_AUTHORITY_BUNDLE_BYTES", str(150 * 1024 * 1024))))
FDM_TOOLCHAIN_LOCK = Path(os.getenv("FDM_TOOLCHAIN_LOCK", "/app/fdm-toolchain.lock.json"))
FDM_TOOLCHAIN_MANIFEST = Path(os.getenv("FDM_TOOLCHAIN_MANIFEST", "/opt/workpiece-toolchain/manifest.json"))
FDM_PACKAGE_INVENTORY = Path(os.getenv("FDM_PACKAGE_INVENTORY", "/opt/workpiece-toolchain/packages.txt"))

_DIGEST_REF = re.compile(r"^.+@sha256:[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
AUTHORITY_GENERATION_LOCK = threading.Lock()
RATRIG = "ratrig_vcore3_300"

app = FastAPI(
    title="Workpiece FDM Authority v2 Candidate API",
    version="0.1.0",
    license_info={"name": "GNU AGPL-3.0-or-later", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
)


def authority_config_status() -> dict[str, bool]:
    return {
        "enabled": ENABLE_FDM_AUTHORITY_V2_API,
        "authenticated": bool(WORKPIECE_FDM_AUTHORITY_V2_TOKEN),
        "service_commit": bool(_COMMIT.fullmatch(SERVICE_COMMIT_SHA.lower())),
        "runtime_digest_ref": bool(_DIGEST_REF.fullmatch(WORKPIECE_FDM_AUTHORITY_RUNTIME_REF)),
        "toolchain_digest_ref": bool(_DIGEST_REF.fullmatch(WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF)),
        "orca_runtime": ORCA_BIN.is_file(),
        "ratrig_profiles": profiles_ready(),
        "toolchain_lock": FDM_TOOLCHAIN_LOCK.is_file(),
        "toolchain_manifest": FDM_TOOLCHAIN_MANIFEST.is_file(),
        "package_inventory": FDM_PACKAGE_INVENTORY.is_file(),
    }


def authority_access(authorization: Annotated[str | None, Header()] = None):
    if not ENABLE_FDM_AUTHORITY_V2_API:
        raise HTTPException(status_code=404, detail="The FDM Authority v2 candidate API is not enabled.")
    if not WORKPIECE_FDM_AUTHORITY_V2_TOKEN:
        raise HTTPException(status_code=503, detail="The FDM Authority v2 service token is not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(
        supplied.strip(), WORKPIECE_FDM_AUTHORITY_V2_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid FDM Authority v2 server token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    config = authority_config_status()
    required = (
        "service_commit",
        "runtime_digest_ref",
        "toolchain_digest_ref",
        "orca_runtime",
        "ratrig_profiles",
        "toolchain_lock",
        "toolchain_manifest",
        "package_inventory",
    )
    missing = [key for key in required if not config[key]]
    if missing:
        raise HTTPException(status_code=503, detail=f"FDM Authority v2 candidate runtime is incomplete: {', '.join(missing)}.")
    if not AUTHORITY_GENERATION_LOCK.acquire(timeout=AUTHORITY_QUEUE_TIMEOUT_SECONDS):
        raise HTTPException(status_code=503, detail="The FDM Authority v2 candidate queue is busy. Retry later.")
    try:
        yield
    finally:
        AUTHORITY_GENERATION_LOCK.release()


@app.get("/health")
def health() -> dict:
    status = authority_config_status()
    ready_keys = tuple(key for key in status if key != "enabled")
    return {
        "ok": status["enabled"] and all(status[key] for key in ready_keys),
        "service": "Workpiece FDM Authority v2 Candidate API",
        "contractVersion": FDM_AUTHORITY_CANDIDATE_API_VERSION,
        "candidateOnly": True,
        "productionEnablementPerformed": False,
        "config": status,
    }


@app.post("/v2/authority-candidate")
async def build_authority_candidate(
    _access: Annotated[None, Depends(authority_access)],
    file: Annotated[UploadFile, File(description="Single-colour immutable STL")],
    material: Annotated[str, Form()] = "pla",
    quality: Annotated[str, Form()] = "balanced",
    strength: Annotated[str, Form()] = "functional",
    quantity: Annotated[int, Form()] = 1,
    printer: Annotated[str, Form()] = RATRIG,
) -> dict:
    if material not in MATERIALS:
        raise HTTPException(status_code=422, detail=f"material must be one of: {', '.join(MATERIALS)}")
    if quality not in {"draft", "balanced", "fine"}:
        raise HTTPException(status_code=422, detail="quality must be one of: draft, balanced, fine")
    if strength not in {"prototype", "functional", "load_bearing"}:
        raise HTTPException(status_code=422, detail="strength must be one of: prototype, functional, load_bearing")
    if quantity < 1 or quantity > MAX_PROJECT_QUANTITY:
        raise HTTPException(status_code=422, detail=f"quantity must be between 1 and {MAX_PROJECT_QUANTITY}")
    if printer != RATRIG:
        raise HTTPException(
            status_code=422,
            detail="Authority v2 candidate API currently supports only ratrig_vcore3_300; temporary/generic Ender authority is intentionally blocked.",
        )
    filename = file.filename or "upload.stl"
    if Path(filename).suffix.lower() != ".stl":
        raise HTTPException(status_code=415, detail="Authority v2 candidate API accepts STL files only.")

    with tempfile.TemporaryDirectory(prefix="workpiece-authority-v2-") as temporary:
        job = Path(temporary)
        source_path = job / "source-original.stl"
        await save_upload(file, source_path)
        try:
            inspection = inspect_stl(source_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        selected = choose_project_printer(RATRIG, material, inspection["dimensions_mm"])
        if selected != RATRIG or PROJECT_PRINTERS[selected]["temporary_generic"]:
            raise HTTPException(status_code=422, detail="Authority v2 candidate routing did not resolve to the exact RatRig profile.")
        machine_path, process_path, filament_path = project_profile_paths(selected, material, job, quality, strength)
        project_path = job / "workpiece-production.3mf"
        generation_env = isolated_orca_env(job)

        sources = [source_path]
        for index in range(2, quantity + 1):
            instance = job / f"source-instance-{index:03d}.stl"
            try:
                os.link(source_path, instance)
            except OSError:
                instance = source_path
            sources.append(instance)

        command = build_project_command(
            orca_bin=ORCA_BIN,
            machine_profile=machine_path,
            process_profile=process_path,
            filament_profile=filament_path,
            sources=sources,
            project_path=project_path,
            auto_orient=True,
            allow_arrange_rotations=False,
        )
        run_orca(command, cwd=job, timeout=SLICE_TIMEOUT_SECONDS, env=generation_env)
        if not project_path.is_file():
            candidates = sorted(job.rglob("*.3mf"), key=lambda item: item.stat().st_size, reverse=True)
            if not candidates:
                raise HTTPException(status_code=422, detail="OrcaSlicer completed without exporting an editable authority project 3MF.")
            project_path = candidates[0]

        try:
            layout_repair = repair_project_plate_layout(
                project_path,
                envelope_mm=PROJECT_PRINTERS[RATRIG]["envelope_mm"],
            )
            project_inspection = inspect_project_3mf(project_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project_size = project_path.stat().st_size
        if project_size <= 0 or project_size > MAX_PROJECT_BYTES:
            raise HTTPException(status_code=422, detail="Authority production 3MF is outside the supported project-size limit.")
        if project_inspection["instance_count"] != quantity:
            raise HTTPException(status_code=422, detail="Authority production 3MF quantity does not match the exact request.")
        if not project_inspection["embedded"]["project_settings"]:
            raise HTTPException(status_code=422, detail="Authority production 3MF does not retain embedded project settings.")

        generation_receipt = {
            "source": {"sha256": sha256_file(source_path)},
            "printer": {"key": RATRIG, "temporary_generic": False},
            "profiles": {
                "machine": {"identity": f"{RATRIG}:machine", "sha256": sha256_file(machine_path)},
                "process": {
                    "identity": f"{RATRIG}:{material}:{quality}:{strength}:process",
                    "sha256": sha256_file(process_path),
                },
                "filament": {"identity": f"{RATRIG}:{material}:filament", "sha256": sha256_file(filament_path)},
            },
            "engine": {
                "name": "OrcaSlicer",
                "version": ORCA_VERSION,
                "service_commit": SERVICE_COMMIT_SHA.lower(),
            },
            "project": {"sha256": sha256_file(project_path)},
            "request": {
                "material": material,
                "quality": quality,
                "strength": strength,
                "quantity": quantity,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        }

        try:
            result = build_fdm_authority_candidate(
                source_path=source_path,
                project_path=project_path,
                machine_profile_bytes=machine_path.read_bytes(),
                process_profile_bytes=process_path.read_bytes(),
                filament_profile_bytes=filament_path.read_bytes(),
                generation_receipt=generation_receipt,
                orca_bin=ORCA_BIN,
                timeout_seconds=SLICE_TIMEOUT_SECONDS,
                service_commit=SERVICE_COMMIT_SHA.lower(),
                runtime_image_ref=WORKPIECE_FDM_AUTHORITY_RUNTIME_REF,
                candidate_toolchain_image_ref=WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF,
                toolchain_lock_bytes=FDM_TOOLCHAIN_LOCK.read_bytes(),
                toolchain_manifest_bytes=FDM_TOOLCHAIN_MANIFEST.read_bytes(),
                package_inventory_bytes=FDM_PACKAGE_INVENTORY.read_bytes(),
                orca_runtime_bytes=ORCA_BIN.read_bytes(),
                summarize_gcode=parse_gcode_summary,
                base_env=os.environ,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=f"FDM Authority v2 candidate failed closed: {exc}") from exc

        if len(result.bundle_bytes) > MAX_AUTHORITY_BUNDLE_BYTES:
            raise HTTPException(status_code=422, detail="FDM Authority v2 deterministic bundle exceeds the configured transport limit.")

        return {
            "contractVersion": FDM_AUTHORITY_CANDIDATE_API_VERSION,
            "candidateOnly": True,
            "authorityState": result.authority_state,
            "authorityIssues": list(result.authority_issues),
            "technicalProductionAuthority": False,
            "productionOrderEligible": False,
            "productionEnablementPerformed": False,
            "humanReview": {"required": True, "status": "pending"},
            "source": {
                "filename": Path(filename).name,
                "sha256": generation_receipt["source"]["sha256"],
                "inspection": inspection,
            },
            "project": {
                "filename": "workpiece-production.3mf",
                "bytes": project_size,
                "sha256": generation_receipt["project"]["sha256"],
                "instanceCount": project_inspection["instance_count"],
                "plateCount": len(project_inspection.get("plates") or []),
                "layoutRepairApplied": layout_repair is not None,
            },
            "manifest": result.manifest,
            "pricing": result.pricing,
            "bundle": {
                "filename": result.bundle_filename,
                "mediaType": "application/zip",
                "bytes": len(result.bundle_bytes),
                "sha256": result.bundle_sha256,
                "manifestSha256": result.bundle_manifest_sha256,
                "indexSha256": result.bundle_index_sha256,
                "memberCount": result.bundle_member_count,
                "base64": base64.b64encode(result.bundle_bytes).decode("ascii"),
            },
        }
