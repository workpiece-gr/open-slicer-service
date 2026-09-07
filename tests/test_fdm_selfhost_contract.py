from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_selfhost_authority_reuses_exact_published_toolchain_inputs():
    toolchain = (ROOT / "Dockerfile.toolchain").read_text(encoding="utf-8")
    selfhost = (ROOT / "Dockerfile.selfhost-authority").read_text(encoding="utf-8")
    for marker in (
        "ubuntu:noble-20260810@sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316",
        "ORCA_VERSION=2.4.2",
        "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage",
        "d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd",
        "sha256sum -c -",
    ):
        assert marker in toolchain
        assert marker in selfhost


def test_selfhost_authority_contains_runtime_evidence_and_starts_production_app_fail_closed():
    selfhost = (ROOT / "Dockerfile.selfhost-authority").read_text(encoding="utf-8")
    assert "COPY fdm-toolchain.lock.json ./fdm-toolchain.lock.json" in selfhost
    assert "COPY fdm-service.lock.json ./fdm-service.lock.json" in selfhost
    assert "FDM_TOOLCHAIN_MANIFEST=/opt/workpiece-toolchain/manifest.json" in selfhost
    assert "FDM_PACKAGE_INVENTORY=/opt/workpiece-toolchain/packages.txt" in selfhost
    assert "app.authority_production_api:app" in selfhost
    assert "ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API=1" not in selfhost
    assert "ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API=true" not in selfhost.lower()


def test_selfhost_authority_never_claims_published_service_image_digest():
    selfhost = (ROOT / "Dockerfile.selfhost-authority").read_text(encoding="utf-8")
    assert "sha256:206058fa5d476cd3c4363b7f6b16ff68eda473deef32eef7245d9ba146ca9491" not in selfhost
    assert "must never claim that registry digest" in selfhost
