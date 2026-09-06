from __future__ import annotations

import copy

from app.fdm_authority import (
    AUTHORITY_EVIDENCE_CANDIDATE,
    AUTHORITY_PRODUCTION,
    FDM_GCODE_VALIDATION_VERSION,
    FDM_JOB_CONTRACT_VERSION,
    FDM_PRODUCTION_MANIFEST_VERSION,
    evaluate_fdm_authority,
)
from app.fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION
from app.fdm_toolchain_provenance import FDM_TOOLCHAIN_LOCK_SCHEMA, FDM_TOOLCHAIN_PROVENANCE_VERSION


SHA = {
    "source": "1" * 64,
    "project": "2" * 64,
    "machine": "3" * 64,
    "process": "4" * 64,
    "filament": "5" * 64,
    "gcode1": "6" * 64,
    "gcode2": "7" * 64,
    "orca": "8" * 64,
    "runtime": "9" * 64,
}
RUNTIME_REF = f"ghcr.io/workpiece-gr/open-slicer-service@sha256:{SHA['runtime']}"
PROFILE_HASHES = {"machine": SHA["machine"], "process": SHA["process"], "filament": SHA["filament"]}


def valid_manifest() -> dict:
    def validation(gcode_sha: str) -> dict:
        return {
            "contractVersion": FDM_GCODE_VALIDATION_VERSION,
            "validator": {"name": "workpiece-gcode-validator", "version": "1.0.0", "serviceCommit": "a" * 40},
            "passed": True,
            "authorityCriticalComplete": True,
            "gcodeSha256": gcode_sha,
            "projectSha256": SHA["project"],
            "profileSha256": dict(PROFILE_HASHES),
            "toolchainRef": RUNTIME_REF,
        }

    manifest = {
        "contractVersion": FDM_PRODUCTION_MANIFEST_VERSION,
        "job": {
            "contractVersion": FDM_JOB_CONTRACT_VERSION,
            "request": {
                "material": "pla",
                "quality": "balanced",
                "strength": "functional",
                "quantity": 2,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        },
        "source": {"filename": "source-original.stl", "bytes": 1000, "sha256": SHA["source"], "immutable": True},
        "machine": {
            "key": "ratrig_vcore3_300",
            "qualification": {
                "contractVersion": "fdm-machine-qualification/1.0.0",
                "productionReady": True,
                "qualificationId": "ratrig-production-profile-v1",
                "evidenceId": "ratrig-production-profile-v1",
                "protocolId": "workpiece-ratrig-qualification-v1",
                "printerKey": "ratrig_vcore3_300",
                "request": {"material": "pla", "quality": "balanced", "strength": "functional"},
                "profileSha256": dict(PROFILE_HASHES),
                "receiptSha256": "a" * 64,
                "receiptBytes": 512,
                "evidence": {"filename": "qualification-evidence.pdf", "mediaType": "application/pdf", "bytes": 1024, "sha256": "b" * 64},
                "review": {"status": "approved", "reviewerId": "workpiece-reviewer", "completedAt": "2026-09-06T00:00:00Z"},
            },
        },
        "profiles": {
            "machine": {"identity": "ratrig_vcore3_300:machine", "sha256": SHA["machine"]},
            "process": {"identity": "ratrig_vcore3_300:pla:balanced:functional:process", "sha256": SHA["process"]},
            "filament": {"identity": "ratrig_vcore3_300:pla:filament", "sha256": SHA["filament"]},
        },
        "toolchain": {
            "contractVersion": FDM_TOOLCHAIN_PROVENANCE_VERSION,
            "authorityState": AUTHORITY_PRODUCTION,
            "authorityCriticalComplete": True,
            "orcaVersion": "2.4.2",
            "orcaBinarySha256": SHA["orca"],
            "serviceCommit": "a" * 40,
            "executionEnvironment": {"reference": RUNTIME_REF, "digest": f"sha256:{SHA['runtime']}"},
            "toolchainImage": {
                "reference": "ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:" + "b" * 64,
                "digest": "sha256:" + "b" * 64,
            },
            "toolchainManifestSha256": "c" * 64,
            "baseImage": {"reference": "ubuntu:noble-20260810@sha256:" + "d" * 64},
            "upstreamOrca": {
                "asset": "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage",
                "assetSha256": "e" * 64,
                "releaseAssetId": 468754790,
            },
            "packageInventorySha256": "f" * 64,
            "lockSchema": FDM_TOOLCHAIN_LOCK_SCHEMA,
            "lockStatus": "published",
            "lockSha256": "0" * 64,
        },
        "project": {
            "filename": "workpiece-production.3mf",
            "bytes": 5000,
            "sha256": SHA["project"],
            "sourceSha256": SHA["source"],
            "profileSha256": dict(PROFILE_HASHES),
            "toolchainRef": RUNTIME_REF,
        },
        "instances": [
            {
                "id": "instance-001",
                "objectId": "1",
                "plateId": "plate-001",
                "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 10, 0],
                "boundsMm": {"min": [10, 10, 0], "max": [30, 30, 20]},
            },
            {
                "id": "instance-002",
                "objectId": "1",
                "plateId": "plate-002",
                "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, 12, 12, 0],
                "boundsMm": {"min": [12, 12, 0], "max": [32, 32, 20]},
            },
        ],
        "plates": [
            {
                "id": "plate-001",
                "index": 1,
                "instanceIds": ["instance-001"],
                "projectSha256": SHA["project"],
                "profileSha256": dict(PROFILE_HASHES),
                "toolchainRef": RUNTIME_REF,
                "gcode": {"filename": "plate-001.gcode", "bytes": 10000, "sha256": SHA["gcode1"]},
                "validation": validation(SHA["gcode1"]),
                "statistics": {
                    "filamentGrams": 12.5,
                    "printTimeSeconds": 1200,
                    "layerCount": 100,
                    "filamentSource": "reported_from_hashed_gcode",
                    "printTimeSource": "reported_from_hashed_gcode",
                    "layerCountSource": "reported_from_hashed_gcode",
                },
            },
            {
                "id": "plate-002",
                "index": 2,
                "instanceIds": ["instance-002"],
                "projectSha256": SHA["project"],
                "profileSha256": dict(PROFILE_HASHES),
                "toolchainRef": RUNTIME_REF,
                "gcode": {"filename": "plate-002.gcode", "bytes": 11000, "sha256": SHA["gcode2"]},
                "validation": validation(SHA["gcode2"]),
                "statistics": {
                    "filamentGrams": 13.25,
                    "printTimeSeconds": 1300,
                    "layerCount": 101,
                    "filamentSource": "reported_from_hashed_gcode",
                    "printTimeSource": "reported_from_hashed_gcode",
                    "layerCountSource": "reported_from_hashed_gcode",
                },
            },
        ],
        "totals": {"plateCount": 2, "instanceCount": 2, "filamentGrams": 25.75, "printTimeSeconds": 2500},
        "commercial": {"priceAuthoritative": False, "authority": "preview_only"},
        "review": {"required": True, "status": "pending"},
    }
    manifest["instancePlateEvidence"] = {
        "contractVersion": INSTANCE_PLATE_EVIDENCE_VERSION,
        "authorityState": AUTHORITY_EVIDENCE_CANDIDATE,
        "projectSha256": SHA["project"],
        "sourceSha256": SHA["source"],
        "printerKey": "ratrig_vcore3_300",
        "profileSha256": dict(PROFILE_HASHES),
        "instances": [
            {
                **copy.deepcopy(manifest["instances"][0]),
                "gcodeEvidence": {
                    "objectName": "object-001",
                    "definitionCenterMm": [20, 20],
                    "definitionBoundsMm": {"min": [10, 10], "max": [30, 30]},
                    "extrusionSegmentCount": 100,
                    "extrusionBoundsMm": {"min": [10.2, 10.2, 0.2], "max": [29.8, 29.8, 20]},
                    "gcodeSha256": SHA["gcode1"],
                },
            },
            {
                **copy.deepcopy(manifest["instances"][1]),
                "gcodeEvidence": {
                    "objectName": "object-002",
                    "definitionCenterMm": [22, 22],
                    "definitionBoundsMm": {"min": [12, 12], "max": [32, 32]},
                    "extrusionSegmentCount": 101,
                    "extrusionBoundsMm": {"min": [12.2, 12.2, 0.2], "max": [31.8, 31.8, 20]},
                    "gcodeSha256": SHA["gcode2"],
                },
            },
        ],
        "plates": [
            {
                "id": "plate-001",
                "index": 1,
                "projectSha256": SHA["project"],
                "gcodeSha256": SHA["gcode1"],
                "validationGcodeSha256": SHA["gcode1"],
                "instanceIds": ["instance-001"],
                "objectNames": ["object-001"],
            },
            {
                "id": "plate-002",
                "index": 2,
                "projectSha256": SHA["project"],
                "gcodeSha256": SHA["gcode2"],
                "validationGcodeSha256": SHA["gcode2"],
                "instanceIds": ["instance-002"],
                "objectNames": ["object-002"],
            },
        ],
        "totals": {"instanceCount": 2, "plateCount": 2},
    }
    return manifest


def assert_candidate_with(manifest: dict, code: str) -> None:
    result = evaluate_fdm_authority(manifest)
    assert result.state == AUTHORITY_EVIDENCE_CANDIDATE
    assert result.production_authoritative is False
    assert code in {issue.code for issue in result.issues}


def test_complete_manifest_is_production_authoritative_while_price_and_review_remain_separate():
    result = evaluate_fdm_authority(valid_manifest())
    assert result.state == AUTHORITY_PRODUCTION
    assert result.production_authoritative is True
    assert result.issues == ()


def test_source_mismatch_fails_closed():
    manifest = valid_manifest()
    manifest["project"]["sourceSha256"] = "f" * 64
    assert_candidate_with(manifest, "project_source_mismatch")


def test_project_mismatch_fails_closed():
    manifest = valid_manifest()
    manifest["plates"][0]["projectSha256"] = "f" * 64
    assert_candidate_with(manifest, "plate_project_mismatch")


def test_profile_identity_must_match_requested_configuration():
    manifest = valid_manifest()
    manifest["job"]["request"]["material"] = "abs"
    assert_candidate_with(manifest, "profile_configuration_mismatch")


def test_current_v2_policy_is_fail_closed_to_exact_automatic_path():
    manifest = valid_manifest()
    manifest["job"]["request"]["orientation"] = "browser_orientation"
    assert_candidate_with(manifest, "unsupported_manufacturing_policy")


def test_missing_profile_receipt_fails_closed():
    manifest = valid_manifest()
    del manifest["profiles"]["filament"]
    assert_candidate_with(manifest, "missing_profile_receipt")


def test_missing_digest_pinned_toolchain_fails_closed():
    manifest = valid_manifest()
    manifest["toolchain"]["executionEnvironment"]["reference"] = "ghcr.io/workpiece-gr/open-slicer-service:latest"
    assert_candidate_with(manifest, "missing_immutable_toolchain")


def test_cp5_candidate_toolchain_cannot_self_grant_production_authority():
    manifest = valid_manifest()
    manifest["toolchain"]["authorityState"] = AUTHORITY_EVIDENCE_CANDIDATE
    manifest["toolchain"]["authorityCriticalComplete"] = False
    assert_candidate_with(manifest, "missing_immutable_toolchain")


def test_cp5_unpublished_lock_cannot_satisfy_production_authority():
    manifest = valid_manifest()
    manifest["toolchain"]["lockStatus"] = "unpublished"
    assert_candidate_with(manifest, "missing_immutable_toolchain")


def test_cp5_toolchain_image_digest_must_match_reference():
    manifest = valid_manifest()
    manifest["toolchain"]["toolchainImage"]["digest"] = "sha256:" + "c" * 64
    assert_candidate_with(manifest, "missing_immutable_toolchain")


def test_cp5_manifest_and_package_inventory_hashes_are_required():
    manifest = valid_manifest()
    manifest["toolchain"]["toolchainManifestSha256"] = ""
    manifest["toolchain"]["packageInventorySha256"] = ""
    assert_candidate_with(manifest, "missing_immutable_toolchain")


def test_orphan_instance_fails_closed():
    manifest = valid_manifest()
    manifest["instances"][0]["plateId"] = "plate-404"
    assert_candidate_with(manifest, "orphan_instance")


def test_duplicate_instance_membership_fails_closed():
    manifest = valid_manifest()
    manifest["plates"][1]["instanceIds"].append("instance-001")
    assert_candidate_with(manifest, "duplicate_instance_membership")


def test_missing_plate_fails_closed():
    manifest = valid_manifest()
    manifest["plates"] = manifest["plates"][:1]
    manifest["totals"] = {"plateCount": 1, "instanceCount": 2, "filamentGrams": 12.5, "printTimeSeconds": 1200}
    assert_candidate_with(manifest, "orphan_instance")


def test_missing_gcode_fails_closed():
    manifest = valid_manifest()
    manifest["plates"][0]["gcode"] = {}
    assert_candidate_with(manifest, "missing_gcode_artifact")


def test_validator_gcode_mismatch_fails_closed():
    manifest = valid_manifest()
    manifest["plates"][0]["validation"]["gcodeSha256"] = "f" * 64
    assert_candidate_with(manifest, "validator_gcode_mismatch")


def test_incomplete_totals_fail_closed():
    manifest = valid_manifest()
    del manifest["totals"]["printTimeSeconds"]
    assert_candidate_with(manifest, "incomplete_or_mismatched_totals")


def test_unqualified_machine_cannot_become_production_authoritative():
    manifest = valid_manifest()
    manifest["machine"] = {
        "key": "ender3_generic_235",
        "qualification": {"productionReady": False, "evidenceId": "temporary-generic-orca-profile"},
    }
    assert_candidate_with(manifest, "machine_not_production_ready")


def test_no_manifest_declared_state_can_override_missing_evidence():
    manifest = valid_manifest()
    manifest["declaredAuthority"] = "production_authoritative"
    manifest["plates"][0]["validation"]["passed"] = False
    assert_candidate_with(manifest, "gcode_validation_incomplete")


def test_duplicate_instance_definition_fails_closed():
    manifest = valid_manifest()
    duplicate = copy.deepcopy(manifest["instances"][0])
    manifest["instances"].append(duplicate)
    manifest["job"]["request"]["quantity"] = 3
    assert_candidate_with(manifest, "duplicate_instance_id")


def test_cp4_instance_plate_evidence_is_required_for_production_authority():
    manifest = valid_manifest()
    del manifest["instancePlateEvidence"]
    assert_candidate_with(manifest, "missing_instance_plate_evidence")


def test_cp4_instance_transform_must_match_manifest_instance():
    manifest = valid_manifest()
    manifest["instancePlateEvidence"]["instances"][0]["transform"][9] = 11
    assert_candidate_with(manifest, "instance_plate_evidence_transform_mismatch")


def test_cp4_instance_gcode_must_match_exact_manifest_plate_gcode():
    manifest = valid_manifest()
    manifest["instancePlateEvidence"]["instances"][0]["gcodeEvidence"]["gcodeSha256"] = "f" * 64
    assert_candidate_with(manifest, "instance_gcode_evidence_mismatch")


def test_cp4_instance_set_must_exactly_match_manifest_instances():
    manifest = valid_manifest()
    manifest["instancePlateEvidence"]["instances"] = manifest["instancePlateEvidence"]["instances"][:1]
    assert_candidate_with(manifest, "instance_plate_evidence_instance_set_mismatch")


def test_cp4_plate_membership_must_match_manifest_order():
    manifest = valid_manifest()
    manifest["instancePlateEvidence"]["plates"][0]["instanceIds"] = ["instance-002"]
    assert_candidate_with(manifest, "instance_plate_evidence_membership_mismatch")
