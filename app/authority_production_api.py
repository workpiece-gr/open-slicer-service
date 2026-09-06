"""Separate, disabled-by-default HTTP entrypoint for production FDM Authority v2.

This app is intentionally distinct from both ``app.main:app`` and the candidate
Authority-v2 app. Merely merging this module cannot expose a route. A deployment
must deliberately start this app, enable its independent flag/token, run from a
digest-pinned image, use a committed *published* CP5 lock, and mount the exact
machine-qualification receipt plus its retained physical-evidence artifact before
a request can reach the authority pipeline.
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

from .fdm_authority_http_project import FdmAuthoritySourceError, RATRIG_PRINTER_KEY, prepare_fdm_authority_project
from .fdm_authority_production import FDM_AUTHORITY_PRODUCTION_API_VERSION, build_fdm_authority_production
from .fdm_toolchain_provenance import validate_toolchain_lock
from .main import MATERIALS, MAX_PROJECT_QUANTITY, ORCA_BIN, SERVICE_COMMIT_SHA, SLICE_TIMEOUT_SECONDS, profiles_ready, save_upload

ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API = os.getenv("ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API", "0").strip().lower() in {"1", "true", "yes"}
WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN = os.getenv("WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN", "").strip()
WORKPIECE_FDM_AUTHORITY_RUNTIME_REF = os.getenv("WORKPIECE_FDM_AUTHORITY_RUNTIME_REF", "").strip().lower()
FDM_MACHINE_QUALIFICATION_RECEIPT = Path(os.getenv("FDM_MACHINE_QUALIFICATION_RECEIPT", "/app/fdm-machine-qualification.json"))
FDM_MACHINE_QUALIFICATION_EVIDENCE = Path(os.getenv("FDM_MACHINE_QUALIFICATION_EVIDENCE", "/app/fdm-machine-qualification-evidence.bin"))
AUTHORITY_QUEUE_TIMEOUT_SECONDS = max(1, int(os.getenv("FDM_AUTHORITY_V2_QUEUE_TIMEOUT_SECONDS", "30")))
MAX_AUTHORITY_BUNDLE_BYTES = max(1, int(os.getenv("MAX_FDM_AUTHORITY_BUNDLE_BYTES", str(150 * 1024 * 1024))))
FDM_TOOLCHAIN_LOCK = Path(os.getenv("FDM_TOOLCHAIN_LOCK", "/app/fdm-toolchain.lock.json"))
FDM_TOOLCHAIN_MANIFEST = Path(os.getenv("FDM_TOOLCHAIN_MANIFEST", "/opt/workpiece-toolchain/manifest.json"))
FDM_PACKAGE_INVENTORY = Path(os.getenv("FDM_PACKAGE_INVENTORY", "/opt/workpiece-toolchain/packages.txt"))

_DIGEST_REF = re.compile(r"^.+@sha256:[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
AUTHORITY_GENERATION_LOCK = threading.Lock()
RATRIG = RATRIG_PRINTER_KEY

app = FastAPI(
    title="Workpiece FDM Authority v2 Production API",
    version="0.1.0",
    license_info={"name": "GNU AGPL-3.0-or-later", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
)


def _published_lock_ready() -> bool:
    if not FDM_TOOLCHAIN_LOCK.is_file():
        return False
    try:
        validate_toolchain_lock(FDM_TOOLCHAIN_LOCK.read_bytes(), require_published=True)
    except (OSError, ValueError):
        return False
    return True


def production_config_status() -> dict[str, bool]:
    return {
        "enabled": ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API,
        "authenticated": bool(WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN),
        "service_commit": bool(_COMMIT.fullmatch(SERVICE_COMMIT_SHA.lower())),
        "runtime_digest_ref": bool(_DIGEST_REF.fullmatch(WORKPIECE_FDM_AUTHORITY_RUNTIME_REF)),
        "published_toolchain_lock": _published_lock_ready(),
        "machine_qualification_receipt": FDM_MACHINE_QUALIFICATION_RECEIPT.is_file(),
        "machine_qualification_evidence": FDM_MACHINE_QUALIFICATION_EVIDENCE.is_file(),
        "orca_runtime": ORCA_BIN.is_file(),
        "ratrig_profiles": profiles_ready(),
        "toolchain_manifest": FDM_TOOLCHAIN_MANIFEST.is_file(),
        "package_inventory": FDM_PACKAGE_INVENTORY.is_file(),
    }


def production_access(authorization: Annotated[str | None, Header()] = None):
    if not ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API:
        raise HTTPException(status_code=404, detail="The FDM Authority v2 production API is not enabled.")
    if not WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN:
        raise HTTPException(status_code=503, detail="The FDM Authority v2 production service token is not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(
        supplied.strip(), WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid FDM Authority v2 production server token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    status = production_config_status()
    required = (
        "service_commit",
        "runtime_digest_ref",
        "published_toolchain_lock",
        "machine_qualification_receipt",
        "machine_qualification_evidence",
        "orca_runtime",
        "ratrig_profiles",
        "toolchain_manifest",
        "package_inventory",
    )
    missing = [key for key in required if not status[key]]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"FDM Authority v2 production runtime is incomplete: {', '.join(missing)}.",
        )
    if not AUTHORITY_GENERATION_LOCK.acquire(timeout=AUTHORITY_QUEUE_TIMEOUT_SECONDS):
        raise HTTPException(status_code=503, detail="The FDM Authority v2 production queue is busy. Retry later.")
    try:
        yield
    finally:
        AUTHORITY_GENERATION_LOCK.release()


@app.get("/health")
def health() -> dict:
    status = production_config_status()
    ready_keys = tuple(key for key in status if key != "enabled")
    return {
        "ok": status["enabled"] and all(status[key] for key in ready_keys),
        "service": "Workpiece FDM Authority v2 Production API",
        "contractVersion": FDM_AUTHORITY_PRODUCTION_API_VERSION,
        "candidateOnly": False,
        "productionEnablementPerformed": False,
        "humanReviewStillRequired": True,
        "config": status,
    }


def _validate_request(material: str, quality: str, strength: str, quantity: int, printer: str, filename: str) -> None:
    if material not in MATERIALS:
        raise HTTPException(status_code=422, detail=f"material must be one of: {', '.join(MATERIALS)}")
    if quality not in {"draft", "balanced", "fine"}:
        raise HTTPException(status_code=422, detail="quality must be one of: draft, balanced, fine")
    if strength not in {"prototype", "functional", "load_bearing"}:
        raise HTTPException(status_code=422, detail="strength must be one of: prototype, functional, load_bearing")
    if quantity < 1 or quantity > MAX_PROJECT_QUANTITY:
        raise HTTPException(status_code=422, detail=f"quantity must be between 1 and {MAX_PROJECT_QUANTITY}")
    if printer != RATRIG:
        raise HTTPException(status_code=422, detail="Production FDM Authority v2 currently supports only ratrig_vcore3_300.")
    if Path(filename).suffix.lower() != ".stl":
        raise HTTPException(status_code=415, detail="Production FDM Authority v2 accepts STL files only.")


@app.post("/v2/authority-production")
async def build_authority_production(
    _access: Annotated[None, Depends(production_access)],
    file: Annotated[UploadFile, File(description="Single-colour immutable STL")],
    material: Annotated[str, Form()] = "pla",
    quality: Annotated[str, Form()] = "balanced",
    strength: Annotated[str, Form()] = "functional",
    quantity: Annotated[int, Form()] = 1,
    printer: Annotated[str, Form()] = RATRIG,
) -> dict:
    filename = file.filename or "upload.stl"
    _validate_request(material, quality, strength, quantity, printer, filename)

    with tempfile.TemporaryDirectory(prefix="workpiece-authority-v2-production-") as temporary:
        job = Path(temporary)
        source_path = job / "source-original.stl"
        await save_upload(file, source_path)
        try:
            prepared = prepare_fdm_authority_project(
                source_path=source_path,
                job_dir=job,
                material=material,
                quality=quality,
                strength=strength,
                quantity=quantity,
                printer_key=RATRIG,
            )
        except FdmAuthoritySourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=f"Production FDM Authority v2 project preparation failed closed: {exc}") from exc

        try:
            result = build_fdm_authority_production(
                source_path=prepared.source_path,
                project_path=prepared.project_path,
                machine_profile_bytes=prepared.machine_profile_path.read_bytes(),
                process_profile_bytes=prepared.process_profile_path.read_bytes(),
                filament_profile_bytes=prepared.filament_profile_path.read_bytes(),
                generation_receipt=prepared.generation_receipt,
                orca_bin=ORCA_BIN,
                timeout_seconds=SLICE_TIMEOUT_SECONDS,
                service_commit=SERVICE_COMMIT_SHA.lower(),
                runtime_image_ref=WORKPIECE_FDM_AUTHORITY_RUNTIME_REF,
                machine_qualification_evidence_id="",
                toolchain_lock_bytes=FDM_TOOLCHAIN_LOCK.read_bytes(),
                toolchain_manifest_bytes=FDM_TOOLCHAIN_MANIFEST.read_bytes(),
                package_inventory_bytes=FDM_PACKAGE_INVENTORY.read_bytes(),
                orca_runtime_bytes=ORCA_BIN.read_bytes(),
                base_env=os.environ,
                machine_qualification_receipt_bytes=FDM_MACHINE_QUALIFICATION_RECEIPT.read_bytes(),
                machine_qualification_evidence_bytes=FDM_MACHINE_QUALIFICATION_EVIDENCE.read_bytes(),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=f"Production FDM Authority v2 failed closed: {exc}") from exc

        if len(result.bundle_bytes) > MAX_AUTHORITY_BUNDLE_BYTES:
            raise HTTPException(status_code=422, detail="Production FDM Authority v2 deterministic bundle exceeds the configured transport limit.")

        return {
            "contractVersion": FDM_AUTHORITY_PRODUCTION_API_VERSION,
            "candidateOnly": False,
            "authorityState": result.authority_state,
            "authorityIssues": list(result.authority_issues),
            "technicalProductionAuthority": True,
            # Technical authority does not bypass the downstream human-review
            # gate. The website may approve/order only after re-verifying this
            # exact evidence package and recording the human decision.
            "productionOrderEligible": False,
            "productionEnablementPerformed": False,
            "humanReview": {"required": True, "status": "pending"},
            "source": {
                "filename": Path(filename).name,
                "sha256": prepared.generation_receipt["source"]["sha256"],
                "inspection": prepared.inspection,
            },
            "project": {
                "filename": "workpiece-production.3mf",
                "bytes": prepared.project_bytes,
                "sha256": prepared.generation_receipt["project"]["sha256"],
                "instanceCount": prepared.project_inspection["instance_count"],
                "plateCount": len(prepared.project_inspection.get("plates") or []),
                "layoutRepairApplied": prepared.layout_repair_applied,
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
