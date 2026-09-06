from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import app.authority_candidate_api as candidate_api
from app.fdm_authority_candidate import build_fdm_authority_candidate


def test_authority_access_is_404_when_candidate_api_is_disabled(monkeypatch):
    monkeypatch.setattr(candidate_api, "ENABLE_FDM_AUTHORITY_V2_API", False)
    access = candidate_api.authority_access(None)
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 404


def test_authority_access_requires_independent_server_token(monkeypatch):
    monkeypatch.setattr(candidate_api, "ENABLE_FDM_AUTHORITY_V2_API", True)
    monkeypatch.setattr(candidate_api, "WORKPIECE_FDM_AUTHORITY_V2_TOKEN", "")
    access = candidate_api.authority_access("Bearer anything")
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 503

    monkeypatch.setattr(candidate_api, "WORKPIECE_FDM_AUTHORITY_V2_TOKEN", "correct")
    access = candidate_api.authority_access("Bearer wrong")
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 401


def test_authority_config_requires_digest_pinned_refs_without_exposing_values(monkeypatch):
    monkeypatch.setattr(candidate_api, "WORKPIECE_FDM_AUTHORITY_RUNTIME_REF", "image:tag")
    monkeypatch.setattr(candidate_api, "WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF", "toolchain:tag")
    status = candidate_api.authority_config_status()
    assert status["runtime_digest_ref"] is False
    assert status["toolchain_digest_ref"] is False
    assert "image:tag" not in repr(status)
    assert "toolchain:tag" not in repr(status)


def test_candidate_orchestrator_rejects_temporary_generic_printer_before_authority_work(tmp_path: Path):
    source = tmp_path / "source.stl"
    project = tmp_path / "project.3mf"
    source.write_bytes(b"source")
    project.write_bytes(b"project")
    receipt = {
        "printer": {"key": "ender3_generic_235", "temporary_generic": True},
        "profiles": {},
    }
    with pytest.raises(ValueError, match="RatRig profile only"):
        build_fdm_authority_candidate(
            source_path=source,
            project_path=project,
            machine_profile_bytes=b"{}",
            process_profile_bytes=b"{}",
            filament_profile_bytes=b"{}",
            generation_receipt=receipt,
            orca_bin=tmp_path / "orca",
            timeout_seconds=1,
            service_commit="a" * 40,
            runtime_image_ref="runtime@example@sha256:" + "1" * 64,
            candidate_toolchain_image_ref="toolchain@example@sha256:" + "2" * 64,
            toolchain_lock_bytes=b"{}",
            toolchain_manifest_bytes=b"{}",
            package_inventory_bytes=b"packages",
            orca_runtime_bytes=b"orca",
            summarize_gcode=lambda _: {},
            base_env={},
        )


def test_candidate_orchestrator_rejects_profile_byte_drift_before_orca(tmp_path: Path):
    source = tmp_path / "source.stl"
    project = tmp_path / "project.3mf"
    source.write_bytes(b"source")
    project.write_bytes(b"project")
    receipt = {
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": False},
        "profiles": {
            "machine": {"sha256": "0" * 64},
            "process": {"sha256": "0" * 64},
            "filament": {"sha256": "0" * 64},
        },
        "request": {
            "material": "pla",
            "quality": "balanced",
            "strength": "functional",
            "quantity": 1,
        },
    }
    with pytest.raises(ValueError, match="machine profile bytes"):
        build_fdm_authority_candidate(
            source_path=source,
            project_path=project,
            machine_profile_bytes=b"machine",
            process_profile_bytes=b"process",
            filament_profile_bytes=b"filament",
            generation_receipt=receipt,
            orca_bin=tmp_path / "orca",
            timeout_seconds=1,
            service_commit="a" * 40,
            runtime_image_ref="runtime@example@sha256:" + "1" * 64,
            candidate_toolchain_image_ref="toolchain@example@sha256:" + "2" * 64,
            toolchain_lock_bytes=b"{}",
            toolchain_manifest_bytes=b"{}",
            package_inventory_bytes=b"packages",
            orca_runtime_bytes=b"orca",
            summarize_gcode=lambda _: {},
            base_env={},
        )


def test_candidate_api_does_not_replace_normal_service_entrypoint_or_v1_project_route():
    dockerfile = Path("Dockerfile.authority").read_text(encoding="utf-8")
    main_source = Path("app/main.py").read_text(encoding="utf-8")
    candidate_source = Path("app/authority_candidate_api.py").read_text(encoding="utf-8")

    assert "uvicorn app.main:app" in dockerfile
    assert '@app.post("/v1/project"' in main_source
    assert "/v2/authority-candidate" not in main_source
    assert '@app.post("/v2/authority-candidate")' in candidate_source