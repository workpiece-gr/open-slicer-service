from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.authority_production_api as production_api
import app.fdm_authority_production as production
from app.fdm_authority import AUTHORITY_PRODUCTION


def _receipt(profile_bytes: dict[str, bytes]) -> dict:
    return {
        "printer": {"key": "ratrig_vcore3_300", "temporary_generic": False},
        "profiles": {
            kind: {"identity": f"ratrig:{kind}", "sha256": hashlib.sha256(payload).hexdigest()}
            for kind, payload in profile_bytes.items()
        },
        "request": {"material": "pla", "quality": "balanced", "strength": "functional", "quantity": 1},
    }


def _paths(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    source = tmp_path / "source-original.stl"
    project = tmp_path / "workpiece-production.3mf"
    source.write_bytes(b"source")
    project.write_bytes(b"project")
    profiles = {"machine": b"machine", "process": b"process", "filament": b"filament"}
    return source, project, profiles


def _kwargs(tmp_path: Path) -> dict:
    source, project, profiles = _paths(tmp_path)
    return {
        "source_path": source,
        "project_path": project,
        "machine_profile_bytes": profiles["machine"],
        "process_profile_bytes": profiles["process"],
        "filament_profile_bytes": profiles["filament"],
        "generation_receipt": _receipt(profiles),
        "orca_bin": tmp_path / "orca",
        "timeout_seconds": 1,
        "service_commit": "a" * 40,
        "runtime_image_ref": "runtime@example@sha256:" + "1" * 64,
        "machine_qualification_evidence_id": "qualification-record-123",
        "toolchain_lock_bytes": Path("fdm-toolchain.lock.json").read_bytes(),
        "toolchain_manifest_bytes": b"{}",
        "package_inventory_bytes": b"packages",
        "orca_runtime_bytes": b"orca",
        "base_env": {},
    }


def test_production_authority_requires_explicit_machine_qualification_before_other_work(tmp_path: Path):
    values = _kwargs(tmp_path)
    values["machine_qualification_evidence_id"] = "   "
    with pytest.raises(ValueError, match="machine qualification evidence id"):
        production.build_fdm_authority_production(**values)


def test_current_unpublished_lock_blocks_production_before_pipeline(monkeypatch, tmp_path: Path):
    values = _kwargs(tmp_path)

    def should_not_run(**_kwargs):
        raise AssertionError("shared pipeline must not run when CP5 publication is absent")

    monkeypatch.setattr(production, "build_fdm_authority_pipeline", should_not_run)
    with pytest.raises(ValueError, match="published toolchain digest"):
        production.build_fdm_authority_production(**values)


def test_production_policy_requires_and_preserves_full_technical_authority(monkeypatch, tmp_path: Path):
    values = _kwargs(tmp_path)
    captured = {}
    toolchain = {
        "authorityState": AUTHORITY_PRODUCTION,
        "authorityCriticalComplete": True,
        "executionEnvironment": {"reference": values["runtime_image_ref"]},
    }
    manifest = {"contractVersion": "fdm-production-manifest/2.0.0"}
    pricing = {
        "priceAuthoritative": True,
        "technicalProductionAuthority": True,
        "productionOrderEligible": False,
        "productionEnablementPerformed": False,
        "authoritativePriceCents": 1234,
    }
    bundle = SimpleNamespace(
        bytes=b"bundle",
        filename="production.zip",
        sha256="2" * 64,
        manifest_sha256="3" * 64,
        index_sha256="4" * 64,
        member_count=14,
        authority_state=AUTHORITY_PRODUCTION,
    )

    def fake_toolchain(**kwargs):
        captured["toolchain"] = kwargs
        return toolchain

    def fake_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return SimpleNamespace(manifest=manifest, pricing_receipt=pricing, bundle=bundle)

    monkeypatch.setattr(production, "build_toolchain_provenance", fake_toolchain)
    monkeypatch.setattr(production, "build_fdm_authority_pipeline", fake_pipeline)
    monkeypatch.setattr(
        production,
        "evaluate_fdm_authority",
        lambda _manifest: SimpleNamespace(state=AUTHORITY_PRODUCTION, production_authoritative=True, issues=()),
    )

    result = production.build_fdm_authority_production(**values)
    assert captured["toolchain"]["require_published"] is True
    assert "candidate_toolchain_image_ref" not in captured["toolchain"]
    assert captured["pipeline"]["machine_production_ready"] is True
    assert captured["pipeline"]["machine_qualification_evidence_id"] == "qualification-record-123"
    assert captured["pipeline"]["require_production_authority"] is True
    assert captured["pipeline"]["review_status"] == "pending"
    assert result.authority_state == AUTHORITY_PRODUCTION
    assert result.authority_issues == ()
    assert result.pricing["technicalProductionAuthority"] is True
    assert result.pricing["productionOrderEligible"] is False


def test_production_api_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(production_api, "ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API", False)
    access = production_api.production_access(None)
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 404


def test_production_api_uses_independent_token(monkeypatch):
    monkeypatch.setattr(production_api, "ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API", True)
    monkeypatch.setattr(production_api, "WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN", "")
    access = production_api.production_access("Bearer anything")
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 503

    monkeypatch.setattr(production_api, "WORKPIECE_FDM_AUTHORITY_V2_PRODUCTION_TOKEN", "correct")
    access = production_api.production_access("Bearer wrong")
    with pytest.raises(HTTPException) as caught:
        next(access)
    assert caught.value.status_code == 401


def test_production_health_rejects_current_unpublished_lock_without_exposing_qualification_value(monkeypatch):
    monkeypatch.setattr(production_api, "FDM_TOOLCHAIN_LOCK", Path("fdm-toolchain.lock.json"))
    monkeypatch.setattr(production_api, "WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID", "secret-qualification-record")
    status = production_api.production_config_status()
    assert status["published_toolchain_lock"] is False
    assert status["machine_qualification_evidence"] is True
    assert "secret-qualification-record" not in repr(status)


def test_production_entrypoint_does_not_replace_normal_or_candidate_apps():
    dockerfile = Path("Dockerfile.authority").read_text(encoding="utf-8")
    main_source = Path("app/main.py").read_text(encoding="utf-8")
    candidate_source = Path("app/authority_candidate_api.py").read_text(encoding="utf-8")
    production_source = Path("app/authority_production_api.py").read_text(encoding="utf-8")

    assert "uvicorn app.main:app" in dockerfile
    assert "/v2/authority-production" not in main_source
    assert "/v2/authority-production" not in candidate_source
    assert '@app.post("/v2/authority-production")' in production_source
    assert "ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API" in production_source
