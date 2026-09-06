"""Shared STL -> retained 3MF preparation for the separate FDM Authority v2 HTTP app.

This module owns only the pre-authority Orca project generation shared by the
candidate and future production policy wrappers. It does not decide CP5
publication, machine qualification, pricing authority, human review, or
production permission.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .main import (
    MAX_PROJECT_BYTES,
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
    project_profile_paths,
    repair_project_plate_layout,
    run_orca,
    sha256_file,
)

RATRIG_PRINTER_KEY = "ratrig_vcore3_300"


@dataclass(frozen=True)
class PreparedFdmAuthorityProject:
    source_path: Path
    project_path: Path
    machine_profile_path: Path
    process_profile_path: Path
    filament_profile_path: Path
    inspection: dict
    project_inspection: dict
    generation_receipt: dict
    base_env: Mapping[str, str]
    project_bytes: int
    layout_repair_applied: bool


def prepare_fdm_authority_project(
    *,
    source_path: Path,
    job_dir: Path,
    material: str,
    quality: str,
    strength: str,
    quantity: int,
    printer_key: str = RATRIG_PRINTER_KEY,
) -> PreparedFdmAuthorityProject:
    """Generate and inspect the exact retained authority project once.

    The caller owns ``job_dir`` lifetime. The returned project and profile paths
    remain valid only while that directory exists.
    """

    if printer_key != RATRIG_PRINTER_KEY:
        raise ValueError("FDM Authority v2 currently supports only the exact RatRig profile.")
    if not source_path.is_file() or source_path.stat().st_size < 1:
        raise ValueError("FDM Authority v2 requires a non-empty immutable STL source.")
    if quantity < 1:
        raise ValueError("FDM Authority v2 quantity must be positive.")

    inspection = inspect_stl(source_path)
    selected = choose_project_printer(printer_key, material, inspection["dimensions_mm"])
    if selected != RATRIG_PRINTER_KEY or PROJECT_PRINTERS[selected]["temporary_generic"]:
        raise ValueError("FDM Authority v2 routing did not resolve to the exact RatRig profile.")

    machine_path, process_path, filament_path = project_profile_paths(selected, material, job_dir, quality, strength)
    project_path = job_dir / "workpiece-production.3mf"
    generation_env = isolated_orca_env(job_dir)

    sources = [source_path]
    for index in range(2, quantity + 1):
        instance = job_dir / f"source-instance-{index:03d}.stl"
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
    run_orca(command, cwd=job_dir, timeout=SLICE_TIMEOUT_SECONDS, env=generation_env)
    if not project_path.is_file():
        candidates = sorted(job_dir.rglob("*.3mf"), key=lambda item: item.stat().st_size, reverse=True)
        if not candidates:
            raise ValueError("OrcaSlicer completed without exporting an editable authority project 3MF.")
        project_path = candidates[0]

    layout_repair = repair_project_plate_layout(
        project_path,
        envelope_mm=PROJECT_PRINTERS[RATRIG_PRINTER_KEY]["envelope_mm"],
    )
    project_inspection = inspect_project_3mf(project_path)
    project_size = project_path.stat().st_size
    if project_size <= 0 or project_size > MAX_PROJECT_BYTES:
        raise ValueError("Authority production 3MF is outside the supported project-size limit.")
    if project_inspection["instance_count"] != quantity:
        raise ValueError("Authority production 3MF quantity does not match the exact request.")
    embedded = project_inspection.get("embedded") or {}
    if embedded.get("project_settings") is not True or embedded.get("model_settings") is not True:
        raise ValueError("Authority production 3MF does not retain the required embedded project/model settings.")

    generation_receipt = {
        "source": {"sha256": sha256_file(source_path)},
        "printer": {"key": RATRIG_PRINTER_KEY, "temporary_generic": False},
        "profiles": {
            "machine": {"identity": f"{RATRIG_PRINTER_KEY}:machine", "sha256": sha256_file(machine_path)},
            "process": {
                "identity": f"{RATRIG_PRINTER_KEY}:{material}:{quality}:{strength}:process",
                "sha256": sha256_file(process_path),
            },
            "filament": {"identity": f"{RATRIG_PRINTER_KEY}:{material}:filament", "sha256": sha256_file(filament_path)},
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

    return PreparedFdmAuthorityProject(
        source_path=source_path,
        project_path=project_path,
        machine_profile_path=machine_path,
        process_profile_path=process_path,
        filament_profile_path=filament_path,
        inspection=inspection,
        project_inspection=project_inspection,
        generation_receipt=generation_receipt,
        base_env=generation_env,
        project_bytes=project_size,
        layout_repair_applied=layout_repair is not None,
    )
