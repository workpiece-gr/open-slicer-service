from __future__ import annotations

import hashlib
import json
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


def _unpublished_toolchain_lock_bytes() -> bytes:
    value = json.loads(Path("fdm-toolchain.lock.json").read_text(encoding="utf-8"))
    value["status"] = "unpublished"
    value["digest"] = None
    return (json.dumps(value, indent=2) + "\n").encode()


def _published_service_lock_bytes(source_commit: str = "a" * 40, digest_hex: str = "1" * 64) -> bytes:
    value = json.loads(Path("fdm-service.lock.json").read_text(encoding="utf-8"))
    value["status"] = "published"
    value["digest"] = "sha256:" + digest_hex
    value["source_commit"] = source_commit
    return (json.dumps(value, indent=2) + "\n").encode()


def _kwargs(tmp_path: Path) -> dict:
    source, project, profiles = _paths(tmp_path)
    generation_receipt = _receipt(profiles)
    profile_hashes = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in profiles.items()}
    evidence_bytes = b"real physical qualification evidence fixture\n"
    receipt = {
        "contractVersion": "fdm-machine-qualification/1.0.0",
        "productionReady": True,
        "qualificationId": "qualification-record-123",
        "protocolId": "workpiece-ratrig-qualification-v1",
        "printerKey": "ratrig_vcore3_300",
        "request": {"material": "pla", "quality": "balanced", "strength": "functional"},
        "profileSha256": profile_hashes,
        "evidence": {
            "filename": "qualification-evidence.txt",
            "mediaType": "text/plain",
            "bytes": len(evidence_bytes),
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        },
        "review": {"status": "approved", "reviewerId": "workpiece-test-reviewer", "completedAt": "2026-09-06T00:00:00Z"},
    }
    receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return {
        "source_path": source,
        "project_path": project,
        "machine_profile_bytes": profiles["machine"],
        "process_profile_bytes": profiles["process"],
        "filament_profile_bytes": profiles["filament"],
        "generation_receipt": generation_receipt,
        "orca_bin": tmp_path / "orca",
        "timeout_seconds": 1,
        "service_commit": "a" * 40,
        "runtime_image_ref": "ghcr.io/workpiece-gr/fdm-authority-service@sha256:" + "1" * 64,
        "service_lock_bytes": _published_service_lock_bytes(),
        "machine_qualification_evidence_id": "qualification-record-123",
        "toolchain_lock_bytes": Path("fdm-toolchain.lock.json").read_bytes(),
        "toolchain_manifest_bytes": b"{}",
        "package_inventory_bytes": b"packages",
        "orca_runtime_bytes": b"orca",
        "base_env": {},
        "machine_qualification_receipt_bytes": receipt_bytes,
        "machine_qualification_evidence_bytes": evidence_bytes,
    }


def test_production_authority_requires_exact_machine_qualification_receipt_before_other_work(tmp_path: Path):
    values = _kwargs(tmp_path)
    values["machine_qualification_receipt_bytes"] = None
    with pytest.raises(ValueError, match="qualification receipt and physical-evidence bytes"):
        production.build_fdm_authority_production(**values)


def test_production_authority_rejects_free_form_id_that_disagrees_with_receipt(tmp_path: Path):
    values = _kwargs(tmp_path)
    values["machine_qualification_evidence_id"] = "different-record"
    with pytest.raises(ValueError, match="differs from the immutable qualification receipt"):
        production.build_fdm_authority_production(**values)


def test_synthetic_unpublished_toolchain_lock_blocks_production_before_pipeline(monkeypatch, tmp_path: Path):
    values = _kwargs(tmp_path)
    values["toolchain_lock_bytes"] = _unpublished_toolchain_lock_bytes()

    def should_not_run(**_kwargs):
        raise AssertionError("shared pipeline must not run when CP5 publication is absent")

    monkeypatch.setattr(production, "build_fdm_authority_pipeline", should_not_run)
    with pytest.raises(ValueError, match="published toolchain"):
        production.build_fdm_authority_production(**values)


def test_committed_unpublished_service_lock_blocks_production_even_with_digest_shaped_runtime(monkeypatch, tmp_path: Path):
    values = _kwargs(tmp_path)
    values["service_lock_bytes"] = Path("fdm-service.lock.json").read_bytes()

    def should_not_run(**_kwargs):
        raise AssertionError("shared pipeline must not run when final service publication is absent")

    monkeypatch.setattr(production, "build_fdm_authority_pipeline", should_not_run)
    with pytest.raises(ValueError, match="published final service image"):
        production.build_fdm_authority_production(**values)


def test_local_service_image_id_cannot_self_grant_production_runtime_identity(monkeypatch, tmp_path: Path):
    values = _kwargs(tmp_path)
    values["runtime_image_ref"] = "workpiece-fdm-authority:candidate@sha256:" + "1" * 64

    def should_not_run(**_kwargs):
        raise AssertionError("shared pipeline must not run for a local Docker image identity")

    monkeypatch.setattr(production, "build_fdm_authority_pipeline", should_not_run)
    with pytest.raises(ValueError, match="exactly match"):
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
    assert captured["toolchain"]["runtime_image_ref"] == values["runtime_image_ref"]
    assert captured["pipeline"]["machine_production_ready"] is True
    assert captured["pipeline"]["machine_qualification_evidence_id"] == "qualification-record-123"
    assert captured["pipeline"]["machine_qualification_receipt_bytes"] == values["machine_qualification_receipt_bytes"]
    assert captured["pipeline"]["machine_qualification_evidence_bytes"] == values["machine_qualification_evidence_bytes"]
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


def test_production_health_reports_toolchain_published_but_service_publication_unresolved(monkeypatch, tmp_path: Path):
    receipt = tmp_path / "qualification.json"
    evidence = tmp_path / "qualification-evidence.bin"
    receipt.write_bytes(b"receipt")
    evidence.write_bytes(b"evidence")
    monkeypatch.setattr(production_api, "FDM_TOOLCHAIN_LOCK", Path("fdm-toolchain.lock.json"))
    monkeypatch.setattr(production_api, "FDM_SERVICE_LOCK", Path("fdm-service.lock.json"))
    monkeypatch.setattr(production_api, "FDM_MACHINE_QUALIFICATION_RECEIPT", receipt)
    monkeypatch.setattr(production_api, "FDM_MACHINE_QUALIFICATION_EVIDENCE", evidence)
    status = production_api.production_config_status()
    assert status["published_toolchain_lock"] is True
    assert status["published_service_runtime"] is False
    assert status["machine_qualification_receipt"] is True
    assert status["machine_qualification_evidence"] is True


def test_production_health_requires_exact_service_lock_runtime_and_commit(monkeypatch, tmp_path: Path):
    service_lock = tmp_path / "fdm-service.lock.json"
    service_lock.write_bytes(_published_service_lock_bytes())
    monkeypatch.setattr(production_api, "FDM_TOOLCHAIN_LOCK", Path("fdm-toolchain.lock.json"))
    monkeypatch.setattr(production_api, "FDM_SERVICE_LOCK", service_lock)
    monkeypatch.setattr(
        production_api,
        "WORKPIECE_FDM_AUTHORITY_RUNTIME_REF",
        "ghcr.io/workpiece-gr/fdm-authority-service@sha256:" + "1" * 64,
    )
    monkeypatch.setattr(production_api, "SERVICE_COMMIT_SHA", "a" * 40)
    assert production_api.production_config_status()["published_service_runtime"] is True

    monkeypatch.setattr(production_api, "SERVICE_COMMIT_SHA", "b" * 40)
    assert production_api.production_config_status()["published_service_runtime"] is False


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
