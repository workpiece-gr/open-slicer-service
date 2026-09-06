from __future__ import annotations

import json
import os
from pathlib import Path

from app.fdm_authority_pipeline import build_fdm_authority_pipeline
from app.main import MATERIALS, build_process_profile


EXPECTED_CANDIDATE_ISSUES = {"machine_not_production_ready", "missing_immutable_toolchain"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def process_job(name: str, *, expect_support: bool) -> dict:
    source_path = Path(f"/tmp/cp7-{name}.stl")
    project_path = Path(f"/tmp/cp7-{name}.3mf")
    generation = load_json(Path(f"/tmp/cp7-{name}-generation.json"))
    toolchain = load_json(Path("/tmp/cp7-toolchain-receipt.json"))
    pricing_service_commit = os.environ["SOURCE_COMMIT_SHA"]

    process_path = Path(f"/tmp/cp7-{name}-process.json")
    build_process_profile(
        Path("/app/profiles/process/pla.json"),
        "balanced",
        "functional",
        process_path,
        str(MATERIALS["pla"]["label"]),
        automatic_supports=True,
        project_reopen_safe=True,
        single_colour_project=True,
    )
    profiles = {
        "machine": Path("/app/profiles/machine.json").read_bytes(),
        "process": process_path.read_bytes(),
        "filament": Path("/app/profiles/filament/pla.json").read_bytes(),
    }

    result = build_fdm_authority_pipeline(
        orca_bin=Path(os.environ["ORCA_BIN"]),
        source_bytes=source_path.read_bytes(),
        source_filename=source_path.name,
        project_path=project_path,
        generation_receipt=generation,
        profile_bytes=profiles,
        toolchain_receipt=toolchain,
        toolchain_lock_bytes=Path("/app/fdm-toolchain.lock.json").read_bytes(),
        toolchain_manifest_bytes=Path("/opt/workpiece-toolchain/manifest.json").read_bytes(),
        package_inventory_bytes=Path("/opt/workpiece-toolchain/packages.txt").read_bytes(),
        output_dir=Path(f"/tmp/cp7-{name}-exact-gcode"),
        material="pla",
        quality="balanced",
        strength="functional",
        quantity=1,
        printer_key="ratrig_vcore3_300",
        machine_production_ready=False,
        machine_qualification_evidence_id="cp7-ci-candidate-no-physical-qualification",
        validator_service_commit=pricing_service_commit,
        pricing_service_commit=pricing_service_commit,
        base_env=os.environ,
        timeout_seconds=300,
        review_status="pending",
        require_production_authority=False,
    )

    manifest = result.manifest
    receipt = result.pricing_receipt
    issue_codes = set(receipt["manufacturingAuthorityIssues"])
    assert issue_codes == EXPECTED_CANDIDATE_ISSUES, issue_codes
    assert result.bundle.authority_state == "evidence_candidate"
    assert result.bundle.bytes
    assert result.bundle.sha256
    assert receipt["priceAuthoritative"] is True
    assert receipt["authority"] == "server_exact_manufacturing_evidence"
    assert receipt["pricingEngine"]["serviceCommit"] == pricing_service_commit.lower()
    assert receipt["manufacturingAuthorityState"] == "evidence_candidate"
    assert receipt["technicalProductionAuthority"] is False
    assert receipt["productionOrderEligible"] is False
    assert receipt["productionEnablementPerformed"] is False
    assert receipt["humanReview"] == {"required": True, "status": "pending"}
    assert receipt["support"]["used"] is expect_support
    if expect_support:
        assert receipt["support"]["supportExtrusionSegmentCount"] > 0
        assert receipt["breakdownCents"]["supportHandling"] > 0
    else:
        assert receipt["support"]["supportExtrusionSegmentCount"] == 0
        assert receipt["breakdownCents"]["supportHandling"] == 0

    Path(f"/tmp/cp7-{name}-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    Path(f"/tmp/cp7-{name}-pricing.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    Path(f"/tmp/cp7-{name}-bundle.zip").write_bytes(result.bundle.bytes)
    return {
        "name": name,
        "priceCents": receipt["authoritativePriceCents"],
        "supportUsed": receipt["support"]["used"],
        "supportExtrusionSegmentCount": receipt["support"]["supportExtrusionSegmentCount"],
        "filamentGrams": receipt["exactStatistics"]["filamentGrams"],
        "printTimeSeconds": receipt["exactStatistics"]["printTimeSeconds"],
        "pricingReceiptSha256": receipt["pricingReceiptSha256"],
        "pricingServiceCommit": receipt["pricingEngine"]["serviceCommit"],
        "bundleSha256": result.bundle.sha256,
        "bundleMemberCount": result.bundle.member_count,
        "manufacturingAuthorityIssues": receipt["manufacturingAuthorityIssues"],
    }


def main() -> None:
    flat = process_job("flat", expect_support=False)
    overhang = process_job("overhang", expect_support=True)
    report = {
        "contract": "fdm-v2-cp7-real-orca-proof/1.1.0",
        "pipeline": "app.fdm_authority_pipeline.build_fdm_authority_pipeline",
        "flat": flat,
        "overhang": overhang,
        "priceAuthorityProved": True,
        "deterministicBundleProduced": True,
        "productionEnablementPerformed": False,
        "physicalQualificationPerformed": False,
        "toolchainPublicationPerformed": False,
    }
    Path("/tmp/cp7-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
