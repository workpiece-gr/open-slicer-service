import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXPECTED_TOOLCHAIN_DIGEST = "sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739"
EXPECTED_SERVICE_DIGEST = "sha256:206058fa5d476cd3c4363b7f6b16ff68eda473deef32eef7245d9ba146ca9491"
EXPECTED_SERVICE_SOURCE = "dce91058be7b306eaeb3b1ab0ab2fbf5c9081f1f"


def test_authority_runtime_is_parallel_to_existing_live_dockerfile():
    live = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    authority = (ROOT / "Dockerfile.authority").read_text(encoding="utf-8")
    assert "ARG TOOLCHAIN_IMAGE=workpiece-fdm-toolchain:local" in authority
    assert "FROM ${TOOLCHAIN_IMAGE}" in authority
    assert "COPY fdm-toolchain.lock.json" in authority
    assert "COPY fdm-service.lock.json" not in authority
    assert "ARG TOOLCHAIN_IMAGE=" not in live
    assert "FROM ubuntu:24.04" in live


def test_toolchain_recipe_pins_exact_base_and_orca_asset_from_published_lock():
    lock = json.loads((ROOT / "fdm-toolchain.lock.json").read_text(encoding="utf-8"))
    publication = json.loads((ROOT / "fdm-toolchain.publication.json").read_text(encoding="utf-8"))
    recipe = (ROOT / "Dockerfile.toolchain").read_text(encoding="utf-8")
    assert lock["status"] == "published"
    assert lock["digest"] == EXPECTED_TOOLCHAIN_DIGEST
    assert publication["registry_digest"] == lock["digest"]
    assert publication["image"] == lock["image"]
    assert publication["tag"] == lock["tag"]
    assert publication["platform"] == lock["platform"]
    assert publication["package_visibility"] == "private"
    assert publication["digest_pinned_pullback_verified"] is True
    assert publication["deployment_performed"] is False
    assert publication["service_image_published"] is False
    assert publication["machine_qualification_performed"] is False
    assert publication["production_enablement_performed"] is False
    assert lock["base_image"]["reference"] in recipe
    assert lock["orca"]["version"] in recipe
    assert lock["orca"]["asset"] in recipe
    assert lock["orca"]["asset_sha256"] in recipe
    assert "sha256sum -c -" in recipe


def test_final_service_lock_is_published_and_binds_exact_published_toolchain():
    service = json.loads((ROOT / "fdm-service.lock.json").read_text(encoding="utf-8"))
    toolchain = json.loads((ROOT / "fdm-toolchain.lock.json").read_text(encoding="utf-8"))
    assert service["schema"] == "workpiece-fdm-authority-service-lock-v1"
    assert service["status"] == "published"
    assert service["digest"] == EXPECTED_SERVICE_DIGEST
    assert service["source_commit"] == EXPECTED_SERVICE_SOURCE
    assert service["image"] == "ghcr.io/workpiece-gr/fdm-authority-service"
    assert service["platform"] == "linux/amd64"
    assert service["build_recipe"] == "Dockerfile.authority"
    assert service["toolchain"]["image"] == toolchain["image"]
    assert service["toolchain"]["digest"] == toolchain["digest"]


def test_cp5_candidate_ci_cannot_publish_or_write_packages():
    workflow = (ROOT / ".github/workflows/cp5-immutable-toolchain.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in workflow
    assert "packages: write" not in workflow
    assert "docker/login-action" not in workflow
    assert "docker push" not in workflow
    assert "push: true" not in workflow
    assert "ghcr.io" not in workflow


def test_candidate_receipt_remains_non_authoritative_with_published_lock():
    workflow = (ROOT / ".github/workflows/cp5-immutable-toolchain.yml").read_text(encoding="utf-8")
    assert "require_published=False" in workflow
    assert "receipt['authorityState'] == 'evidence_candidate'" in workflow
    assert "receipt['authorityCriticalComplete'] is False" in workflow
    assert "receipt['lockStatus'] == 'published'" in workflow


def test_published_toolchain_verifier_is_read_only():
    workflow = (ROOT / ".github/workflows/cp5-published-toolchain.yml").read_text(encoding="utf-8").lower()
    assert "contents: read" in workflow
    assert "packages: read" in workflow
    assert "packages: write" not in workflow
    assert "docker push" not in workflow
    assert "--push" not in workflow
    assert "docker pull" in workflow


def test_final_service_image_review_is_read_only_and_non_publishing():
    workflow = (ROOT / ".github/workflows/cp5-service-image-review.yml").read_text(encoding="utf-8").lower()
    assert "contents: read" in workflow
    assert "packages: read" in workflow
    assert "packages: write" not in workflow
    assert "docker push" not in workflow
    assert "--push" not in workflow
    assert "docker pull" in workflow
    assert "publication']['performed'] is false" in workflow
    assert "packet['production']['productionauthorityeligible'] is false" in workflow
