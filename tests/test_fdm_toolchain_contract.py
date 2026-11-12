import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXPECTED_TOOLCHAIN_DIGEST = "sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739"
EXPECTED_SERVICE_DIGEST = "sha256:462041d8e8305797aea7042bbdb1682c084994dd4c825fcfe67311c63c873707"
EXPECTED_SERVICE_SOURCE = "d09a8f368099b723d55d43bb0640c9426b1362ac"


def test_authority_runtime_is_parallel_to_existing_live_dockerfile():
    live = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    authority = (ROOT / "Dockerfile.authority").read_text(encoding="utf-8")
    toolchain = json.loads((ROOT / "fdm-toolchain.lock.json").read_text(encoding="utf-8"))
    assert "ARG TOOLCHAIN_IMAGE=workpiece-fdm-toolchain:local" in authority
    assert "FROM ${TOOLCHAIN_IMAGE}" in authority
    assert "COPY fdm-toolchain.lock.json" in authority
    assert "COPY fdm-service.lock.json" not in authority
    assert "COPY qualification/ratrig_vcore3_300/ratrig-pla-balanced-functional-qualification-receipt.json ./fdm-machine-qualification.json" in authority
    assert "COPY qualification/ratrig_vcore3_300/ratrig-pla-balanced-functional-qualification-evidence.json ./fdm-machine-qualification-evidence.bin" in authority
    assert "ARG TOOLCHAIN_IMAGE=" not in live
    assert f"FROM {toolchain['base_image']['reference']}" in live
    # The live service moved to OrcaSlicer 2.5.0 while the published authority
    # toolchain record stays at the version it was published with, so the live
    # Dockerfile is no longer required to pin the lock's Orca asset. It must still
    # pin one exact Orca release consistently: label, ENV, download URL, asset name
    # and verified SHA-256 all name the same version and digest.
    version = re.search(r'workpiece\.orcaslicer\.version="([0-9]+\.[0-9]+\.[0-9]+)"', live).group(1)
    digest = re.search(r'workpiece\.orcaslicer\.asset\.sha256="([0-9a-f]{64})"', live).group(1)
    asset = f"OrcaSlicer_Linux_AppImage_Ubuntu2404_V{version}.AppImage"
    assert f"ORCA_VERSION={version}" in live
    assert f"https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v{version}/{asset}" in live
    assert f'echo "{digest}  /tmp/orca.AppImage" | sha256sum -c -' in live
    assert live.count("OrcaSlicer_Linux_AppImage_Ubuntu2404_V") == 1
    assert len(re.findall(r"[0-9a-f]{64}", live.replace(toolchain["base_image"]["reference"], ""))) == 2
    assert "sha256sum -c -" in live
    arg_lines = [
        line.strip()
        for line in live.splitlines()
        if line.strip() and line.strip().split(maxsplit=1)[0].upper() == "ARG"
    ]
    assert arg_lines == [], f"Legacy Dockerfile must not expose caller-overridable build args: {arg_lines}"


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





