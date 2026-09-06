import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_authority_runtime_is_parallel_to_existing_live_dockerfile():
    live = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    authority = (ROOT / "Dockerfile.authority").read_text(encoding="utf-8")
    assert "ARG TOOLCHAIN_IMAGE=workpiece-fdm-toolchain:local" in authority
    assert "FROM ${TOOLCHAIN_IMAGE}" in authority
    assert "COPY fdm-toolchain.lock.json" in authority
    assert "ARG TOOLCHAIN_IMAGE=" not in live
    assert "FROM ubuntu:24.04" in live


def test_toolchain_recipe_pins_exact_base_and_orca_asset_from_lock():
    lock = json.loads((ROOT / "fdm-toolchain.lock.json").read_text(encoding="utf-8"))
    recipe = (ROOT / "Dockerfile.toolchain").read_text(encoding="utf-8")
    assert lock["status"] == "unpublished"
    assert lock["digest"] is None
    assert lock["base_image"]["reference"] in recipe
    assert lock["orca"]["version"] in recipe
    assert lock["orca"]["asset"] in recipe
    assert lock["orca"]["asset_sha256"] in recipe
    assert "sha256sum -c -" in recipe


def test_cp5_ci_cannot_publish_or_write_packages():
    workflow = (ROOT / ".github/workflows/cp5-immutable-toolchain.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in workflow
    assert "packages: write" not in workflow
    assert "docker/login-action" not in workflow
    assert "docker push" not in workflow
    assert "push: true" not in workflow
    assert "ghcr.io" not in workflow


def test_candidate_receipt_is_required_to_remain_non_authoritative_while_lock_unpublished():
    workflow = (ROOT / ".github/workflows/cp5-immutable-toolchain.yml").read_text(encoding="utf-8")
    assert "require_published=False" in workflow
    assert "receipt['authorityState'] == 'evidence_candidate'" in workflow
    assert "receipt['authorityCriticalComplete'] is False" in workflow
