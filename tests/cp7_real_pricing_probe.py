from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.fdm_authority import evaluate_fdm_authority
from app.fdm_exact_gcode import execute_exact_project_gcode
from app.fdm_instance_plate_evidence import build_instance_plate_evidence
from app.fdm_pricing import price_exact_fdm_job
from app.fdm_profile_driven_gcode import validate_profile_driven_gcode
from app.main import MATERIALS, build_process_profile, parse_gcode_summary


EXPECTED_CANDIDATE_ISSUES = {"machine_not_production_ready", "missing_immutable_toolchain"}


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def process_job(name: str, *, expect_support: bool) -> dict:
    source_path = Path(f"/tmp/cp7-{name}.stl")
    project_path = Path(f"/tmp/cp7-{name}.3mf")
    generation_path = Path(f"/tmp/cp7-{name}-generation.json")
    toolchain_path = Path("/tmp/cp7-toolchain-receipt.json")
    generation = load_json(generation_path)
    toolchain = load_json(toolchain_path)
    runtime_ref = toolchain["executionEnvironment"]["reference"]

    exact_dir = Path(f"/tmp/cp7-{name}-exact-gcode")
    exact = execute_exact_project_gcode(
        orca_bin=Path(os.environ["ORCA_BIN"]),
        project_path=project_path,
        output_dir=exact_dir,
        timeout_seconds=300,
        generation_receipt=generation,
        base_env=os.environ,
    )
    assert exact["plates"]

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
    machine_bytes = Path("/app/profiles/machine.json").read_bytes()
    process_bytes = process_path.read_bytes()
    filament_bytes = Path("/app/profiles/filament/pla.json").read_bytes()
    profile_bytes = {"machine": machine_bytes, "process": process_bytes, "filament": filament_bytes}
    for kind, payload in profile_bytes.items():
        assert sha(payload) == exact["generation_receipt"]["profiles"][kind]["sha256"]

    artifacts: dict[int, dict] = {}
    validations: dict[int, dict] = {}
    statistics: dict[int, dict] = {}
    for artifact in exact["plates"]:
        plate_id = int(artifact["plate_id"])
        validation = validate_profile_driven_gcode(
            artifact=artifact,
            generation_receipt=exact["generation_receipt"],
            machine_profile_bytes=machine_bytes,
            process_profile_bytes=process_bytes,
            filament_profile_bytes=filament_bytes,
            validator_service_commit=os.environ["SOURCE_COMMIT_SHA"],
            toolchain_ref=runtime_ref,
        )
        assert validation["passed"] is True
        assert validation["authorityCriticalComplete"] is True
        summary = parse_gcode_summary(exact_dir / artifact["filename"])
        assert summary["filament_grams"] and summary["print_time_seconds"] and summary["layer_count"]
        artifacts[plate_id] = artifact
        validations[plate_id] = validation
        statistics[plate_id] = summary

    cp4 = build_instance_plate_evidence(
        project_bytes=project_path.read_bytes(),
        exact_gcode_result=exact,
        cp3_validations=validations,
        machine_profile_bytes=machine_bytes,
    )
    assert cp4["totals"]["instanceCount"] == 1
    assert cp4["totals"]["plateCount"] == len(exact["plates"])

    profile_hashes = {
        kind: exact["generation_receipt"]["profiles"][kind]["sha256"]
        for kind in ("machine", "process", "filament")
    }
    manifest_instances = [
        {
            "id": item["id"],
            "objectId": item["objectId"],
            "plateId": item["plateId"],
            "transform": item["transform"],
            "boundsMm": item["boundsMm"],
        }
        for item in cp4["instances"]
    ]
    manifest_plates = []
    gcode_bytes_by_plate: dict[str, bytes] = {}
    total_filament = 0.0
    total_time = 0
    for cp4_plate in cp4["plates"]:
        numeric_plate_id = int(cp4_plate["index"])
        artifact = artifacts[numeric_plate_id]
        validation = validations[numeric_plate_id]
        summary = statistics[numeric_plate_id]
        total_filament += float(summary["filament_grams"])
        total_time += int(summary["print_time_seconds"])
        manifest_plates.append(
            {
                "id": cp4_plate["id"],
                "index": cp4_plate["index"],
                "instanceIds": cp4_plate["instanceIds"],
                "projectSha256": exact["project_sha256"],
                "profileSha256": dict(profile_hashes),
                "toolchainRef": runtime_ref,
                "gcode": {
                    "filename": artifact["filename"],
                    "bytes": artifact["byte_count"],
                    "sha256": artifact["sha256"],
                },
                "validation": validation,
                "statistics": {
                    "filamentGrams": float(summary["filament_grams"]),
                    "printTimeSeconds": int(summary["print_time_seconds"]),
                    "layerCount": int(summary["layer_count"]),
                    "filamentSource": "reported_from_hashed_gcode",
                    "printTimeSource": "reported_from_hashed_gcode",
                    "layerCountSource": "reported_from_hashed_gcode",
                },
            }
        )
        gcode_bytes_by_plate[cp4_plate["id"]] = bytes(artifact["bytes"])

    source_bytes = source_path.read_bytes()
    project_bytes = project_path.read_bytes()
    manifest = {
        "contractVersion": "fdm-production-manifest/2.0.0",
        "job": {
            "contractVersion": "fdm-job/2.0.0",
            "request": {
                "material": "pla",
                "quality": "balanced",
                "strength": "functional",
                "quantity": 1,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        },
        "source": {
            "filename": source_path.name,
            "bytes": len(source_bytes),
            "sha256": sha(source_bytes),
            "immutable": True,
        },
        "machine": {
            "key": "ratrig_vcore3_300",
            "qualification": {
                "productionReady": False,
                "evidenceId": "cp7-ci-candidate-no-physical-qualification",
            },
        },
        "profiles": exact["generation_receipt"]["profiles"],
        "toolchain": toolchain,
        "project": {
            "filename": project_path.name,
            "bytes": len(project_bytes),
            "sha256": sha(project_bytes),
            "sourceSha256": sha(source_bytes),
            "profileSha256": dict(profile_hashes),
            "toolchainRef": runtime_ref,
        },
        "instances": manifest_instances,
        "plates": manifest_plates,
        "instancePlateEvidence": cp4,
        "totals": {
            "plateCount": len(manifest_plates),
            "instanceCount": len(manifest_instances),
            "filamentGrams": total_filament,
            "printTimeSeconds": total_time,
        },
        "commercial": {"priceAuthoritative": False, "authority": "preview_only"},
        "review": {"required": True, "status": "pending"},
    }

    evaluation = evaluate_fdm_authority(manifest)
    issue_codes = {issue.code for issue in evaluation.issues}
    assert evaluation.state == "evidence_candidate"
    assert issue_codes == EXPECTED_CANDIDATE_ISSUES, issue_codes

    receipt = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcode_bytes_by_plate)
    assert receipt["priceAuthoritative"] is True
    assert receipt["authority"] == "server_exact_manufacturing_evidence"
    assert receipt["manufacturingAuthorityState"] == "evidence_candidate"
    assert set(receipt["manufacturingAuthorityIssues"]) == EXPECTED_CANDIDATE_ISSUES
    assert receipt["productionOrderEligible"] is False
    assert receipt["support"]["used"] is expect_support
    if expect_support:
        assert receipt["support"]["supportExtrusionSegmentCount"] > 0
        assert receipt["breakdownCents"]["supportHandling"] > 0
    else:
        assert receipt["support"]["supportExtrusionSegmentCount"] == 0
        assert receipt["breakdownCents"]["supportHandling"] == 0

    Path(f"/tmp/cp7-{name}-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    Path(f"/tmp/cp7-{name}-pricing.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {
        "name": name,
        "priceCents": receipt["authoritativePriceCents"],
        "supportUsed": receipt["support"]["used"],
        "supportExtrusionSegmentCount": receipt["support"]["supportExtrusionSegmentCount"],
        "filamentGrams": receipt["exactStatistics"]["filamentGrams"],
        "printTimeSeconds": receipt["exactStatistics"]["printTimeSeconds"],
        "pricingReceiptSha256": receipt["pricingReceiptSha256"],
        "manufacturingAuthorityIssues": receipt["manufacturingAuthorityIssues"],
    }


def main() -> None:
    flat = process_job("flat", expect_support=False)
    overhang = process_job("overhang", expect_support=True)
    report = {
        "contract": "fdm-v2-cp7-real-orca-proof/1.0.0",
        "flat": flat,
        "overhang": overhang,
        "priceAuthorityProved": True,
        "productionEnablementPerformed": False,
        "physicalQualificationPerformed": False,
        "toolchainPublicationPerformed": False,
    }
    Path("/tmp/cp7-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
