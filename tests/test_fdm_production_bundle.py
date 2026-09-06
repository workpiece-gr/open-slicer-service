from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import pytest

from app.fdm_authority import (
    AUTHORITY_EVIDENCE_CANDIDATE,
    AUTHORITY_PRODUCTION,
    FDM_GCODE_VALIDATION_VERSION,
    FDM_JOB_CONTRACT_VERSION,
    FDM_PRODUCTION_MANIFEST_VERSION,
)
from app.fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION
from app.fdm_machine_qualification import validate_machine_qualification_receipt
from app.fdm_production_bundle import (
    FDM_PRODUCTION_BUNDLE_LAYOUT,
    FDM_PRODUCTION_BUNDLE_VERSION,
    FdmProductionBundleError,
    build_fdm_production_bundle,
)
from app.fdm_toolchain_provenance import (
    FDM_TOOLCHAIN_LOCK_SCHEMA,
    FDM_TOOLCHAIN_MANIFEST_SCHEMA,
    FDM_TOOLCHAIN_PROVENANCE_VERSION,
)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def retained_fixture(*, published: bool = True, qualified: bool = True) -> dict:
    source = b"solid workpiece fixture\nendsolid workpiece fixture\n"
    project = b"PK\x03\x04cp6-exact-production-3mf-bytes"
    machine = b'{"type":"machine","name":"RatRig"}\n'
    process = b'{"type":"process","name":"PLA balanced functional"}\n'
    filament = b'{"type":"filament","name":"PLA"}\n'
    gcode = b"; exact retained gcode\nG90\nM83\nG1 X10 Y10 Z0.2\nG1 X20 Y10 E1\n"
    package_inventory = b"bash=5.2\npython3=3.12\n"

    runtime_digest = "9" * 64
    runtime_ref = f"ghcr.io/workpiece-gr/fdm-authority@sha256:{runtime_digest}"
    toolchain_digest = "a" * 64
    toolchain_ref = f"ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:{toolchain_digest}"
    base_ref = "ubuntu:noble@sha256:" + "b" * 64
    orca_asset_sha = "c" * 64

    lock = {
        "schema": FDM_TOOLCHAIN_LOCK_SCHEMA,
        "status": "published" if published else "unpublished",
        "image": "ghcr.io/workpiece-gr/fdm-slicer-toolchain",
        "tag": "cp5-reviewed",
        "digest": f"sha256:{toolchain_digest}" if published else None,
        "build_recipe": "Dockerfile.toolchain",
        "service_recipe": "Dockerfile.authority",
        "platform": "linux/amd64",
        "base_image": {"reference": base_ref},
        "orca": {
            "version": "2.4.2",
            "asset": "OrcaSlicer.AppImage",
            "release_tag": "v2.4.2",
            "asset_sha256": orca_asset_sha,
            "release_asset_id": 123456,
            "runtime_path": "/opt/orca/squashfs-root/AppRun",
        },
    }
    lock_bytes = canonical(lock)
    toolchain_manifest = {
        "schema": FDM_TOOLCHAIN_MANIFEST_SCHEMA,
        "platform": "linux/amd64",
        "base_image": base_ref,
        "orca_version": "2.4.2",
        "orca_asset": "OrcaSlicer.AppImage",
        "orca_asset_sha256": orca_asset_sha,
        "orca_runtime_path": "/opt/orca/squashfs-root/AppRun",
        "orca_runtime_sha256": "d" * 64,
        "packages_sha256": sha(package_inventory),
    }
    toolchain_manifest_bytes = canonical(toolchain_manifest)

    profile_hashes = {"machine": sha(machine), "process": sha(process), "filament": sha(filament)}
    qualification_evidence_bytes = b"real physical qualification evidence fixture\n"
    qualification_receipt_bytes = canonical(
        {
            "contractVersion": "fdm-machine-qualification/1.0.0",
            "productionReady": True,
            "qualificationId": "test-only-qualification-state",
            "protocolId": "workpiece-ratrig-qualification-v1",
            "printerKey": "ratrig_vcore3_300",
            "request": {"material": "pla", "quality": "balanced", "strength": "functional"},
            "profileSha256": dict(profile_hashes),
            "evidence": {
                "filename": "qualification-evidence.txt",
                "mediaType": "text/plain",
                "bytes": len(qualification_evidence_bytes),
                "sha256": sha(qualification_evidence_bytes),
            },
            "review": {
                "status": "approved",
                "reviewerId": "workpiece-test-reviewer",
                "completedAt": "2026-09-06T00:00:00Z",
            },
        }
    )
    qualification_summary = validate_machine_qualification_receipt(
        receipt_bytes=qualification_receipt_bytes,
        evidence_bytes=qualification_evidence_bytes,
        expected_printer_key="ratrig_vcore3_300",
        expected_request={"material": "pla", "quality": "balanced", "strength": "functional"},
        expected_profile_sha256=profile_hashes,
    )
    qualification_summary = {**qualification_summary, "evidenceId": qualification_summary["qualificationId"]}
    gcode_sha = sha(gcode)
    project_sha = sha(project)
    source_sha = sha(source)

    toolchain_receipt = {
        "contractVersion": FDM_TOOLCHAIN_PROVENANCE_VERSION,
        "authorityState": AUTHORITY_PRODUCTION if published else AUTHORITY_EVIDENCE_CANDIDATE,
        "authorityCriticalComplete": published,
        "serviceCommit": "e" * 40,
        "orcaVersion": "2.4.2",
        "orcaBinarySha256": "d" * 64,
        "executionEnvironment": {"reference": runtime_ref, "digest": f"sha256:{runtime_digest}"},
        "toolchainImage": {"reference": toolchain_ref, "digest": f"sha256:{toolchain_digest}"},
        "toolchainManifestSha256": sha(toolchain_manifest_bytes),
        "baseImage": {"reference": base_ref},
        "upstreamOrca": {"asset": "OrcaSlicer.AppImage", "assetSha256": orca_asset_sha, "releaseAssetId": 123456},
        "packageInventorySha256": sha(package_inventory),
        "lockSchema": FDM_TOOLCHAIN_LOCK_SCHEMA,
        "lockStatus": "published" if published else "unpublished",
        "lockSha256": sha(lock_bytes),
    }

    validation = {
        "contractVersion": FDM_GCODE_VALIDATION_VERSION,
        "validator": {"name": "workpiece-gcode-validator", "version": "1.0.0", "serviceCommit": "f" * 40},
        "passed": True,
        "authorityCriticalComplete": True,
        "gcodeSha256": gcode_sha,
        "projectSha256": project_sha,
        "profileSha256": dict(profile_hashes),
        "toolchainRef": runtime_ref,
    }
    instance = {
        "id": "fdm-inst-001",
        "objectId": "1",
        "plateId": "1",
        "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 10, 0],
        "boundsMm": {"min": [10, 10, 0], "max": [30, 30, 20]},
    }
    plate = {
        "id": "1",
        "index": 1,
        "instanceIds": [instance["id"]],
        "projectSha256": project_sha,
        "profileSha256": dict(profile_hashes),
        "toolchainRef": runtime_ref,
        "gcode": {"filename": "plate_1.gcode", "bytes": len(gcode), "sha256": gcode_sha},
        "validation": validation,
        "statistics": {
            "filamentGrams": 1.25,
            "printTimeSeconds": 120,
            "layerCount": 10,
            "filamentSource": "reported_from_hashed_gcode",
            "printTimeSource": "reported_from_hashed_gcode",
            "layerCountSource": "reported_from_hashed_gcode",
        },
    }
    cp4_instance = {
        **copy.deepcopy(instance),
        "gcodeEvidence": {
            "objectName": "object-1",
            "definitionCenterMm": [20, 20],
            "definitionBoundsMm": {"min": [10, 10], "max": [30, 30]},
            "extrusionSegmentCount": 20,
            "extrusionBoundsMm": {"min": [10.2, 10.2, 0.2], "max": [29.8, 29.8, 20]},
            "gcodeSha256": gcode_sha,
        },
    }

    manifest = {
        "contractVersion": FDM_PRODUCTION_MANIFEST_VERSION,
        "job": {
            "contractVersion": FDM_JOB_CONTRACT_VERSION,
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
        "source": {"filename": "fixture.stl", "bytes": len(source), "sha256": source_sha, "immutable": True},
        "machine": {
            "key": "ratrig_vcore3_300",
            "qualification": qualification_summary if qualified else {"productionReady": False, "evidenceId": "test-only-qualification-state"},
        },
        "profiles": {
            "machine": {"identity": "ratrig_vcore3_300:machine", "sha256": profile_hashes["machine"]},
            "process": {"identity": "ratrig_vcore3_300:pla:balanced:functional:process", "sha256": profile_hashes["process"]},
            "filament": {"identity": "ratrig_vcore3_300:pla:filament", "sha256": profile_hashes["filament"]},
        },
        "toolchain": toolchain_receipt,
        "project": {
            "filename": "workpiece-production.3mf",
            "bytes": len(project),
            "sha256": project_sha,
            "sourceSha256": source_sha,
            "profileSha256": dict(profile_hashes),
            "toolchainRef": runtime_ref,
        },
        "instances": [instance],
        "plates": [plate],
        "instancePlateEvidence": {
            "contractVersion": INSTANCE_PLATE_EVIDENCE_VERSION,
            "authorityState": AUTHORITY_EVIDENCE_CANDIDATE,
            "projectSha256": project_sha,
            "sourceSha256": source_sha,
            "printerKey": "ratrig_vcore3_300",
            "profileSha256": dict(profile_hashes),
            "instances": [cp4_instance],
            "plates": [
                {
                    "id": "1",
                    "index": 1,
                    "projectSha256": project_sha,
                    "gcodeSha256": gcode_sha,
                    "validationGcodeSha256": gcode_sha,
                    "instanceIds": [instance["id"]],
                    "objectNames": ["object-1"],
                }
            ],
            "totals": {"instanceCount": 1, "plateCount": 1},
        },
        "totals": {"plateCount": 1, "instanceCount": 1, "filamentGrams": 1.25, "printTimeSeconds": 120},
        "commercial": {"priceAuthoritative": False, "authority": "preview_only"},
        "review": {"required": True, "status": "pending"},
    }
    return {
        "manifest": manifest,
        "source_bytes": source,
        "project_bytes": project,
        "profile_bytes": {"machine": machine, "process": process, "filament": filament},
        "gcode_bytes_by_plate": {"1": gcode},
        "toolchain_lock_bytes": lock_bytes,
        "toolchain_manifest_bytes": toolchain_manifest_bytes,
        "package_inventory_bytes": package_inventory,
        **({
            "machine_qualification_receipt_bytes": qualification_receipt_bytes,
            "machine_qualification_evidence_bytes": qualification_evidence_bytes,
        } if qualified else {}),
    }


def build(fixture: dict, *, require_production_authority: bool = True):
    return build_fdm_production_bundle(**fixture, require_production_authority=require_production_authority)


def test_authoritative_bundle_is_byte_for_byte_deterministic_and_self_indexed():
    fixture = retained_fixture()
    first = build(fixture)
    second = build(copy.deepcopy(fixture))
    assert first.bytes == second.bytes
    assert first.sha256 == sha(first.bytes)
    assert first.authority_state == AUTHORITY_PRODUCTION
    assert first.filename == "fixture-workpiece-fdm-production.zip"

    with zipfile.ZipFile(io.BytesIO(first.bytes), "r") as archive:
        names = archive.namelist()
        assert names == [
            "source/fixture.stl",
            "project/workpiece-production.3mf",
            "profiles/machine.json",
            "profiles/process.json",
            "profiles/filament.json",
            "toolchain/lock.json",
            "toolchain/manifest.json",
            "toolchain/packages.txt",
            "evidence/toolchain-receipt.json",
            "evidence/instance-plate.json",
            "evidence/machine-qualification-receipt.json",
            "evidence/machine-qualification/qualification-evidence.txt",
            "plates/plate-001.gcode",
            "evidence/plates/plate-001-validation.json",
            "manifest.json",
            "bundle-index.json",
        ]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        bundle_manifest = json.loads(archive.read("manifest.json"))
        assert bundle_manifest["bundle"]["contractVersion"] == FDM_PRODUCTION_BUNDLE_VERSION
        assert bundle_manifest["bundle"]["layout"] == FDM_PRODUCTION_BUNDLE_LAYOUT
        assert bundle_manifest["bundle"]["productionEnablementPerformed"] is False
        index = json.loads(archive.read("bundle-index.json"))
        assert len(index["members"]) == len(names) - 1
        for member in index["members"]:
            payload = archive.read(member["path"])
            assert len(payload) == member["bytes"]
            assert sha(payload) == member["sha256"]

    assert first.manifest_sha256 == sha(first.manifest_bytes)
    assert first.index_sha256 == sha(first.index_bytes)
    assert first.member_count == 16


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("source_bytes", lambda value: value + b"x"),
        ("project_bytes", lambda value: value + b"x"),
        ("package_inventory_bytes", lambda value: value + b"x"),
    ],
)
def test_exact_retained_member_mutation_fails_closed(field, mutate):
    fixture = retained_fixture()
    fixture[field] = mutate(fixture[field])
    with pytest.raises(FdmProductionBundleError):
        build(fixture)


def test_machine_qualification_evidence_mutation_fails_closed():
    fixture = retained_fixture()
    fixture["machine_qualification_evidence_bytes"] += b"drift"
    with pytest.raises(FdmProductionBundleError, match="qualification physical evidence"):
        build(fixture)


def test_machine_qualification_receipt_mutation_fails_closed():
    fixture = retained_fixture()
    fixture["machine_qualification_receipt_bytes"] += b" "
    with pytest.raises(FdmProductionBundleError, match="qualification receipt"):
        build(fixture)


def test_profile_mutation_fails_closed():
    fixture = retained_fixture()
    fixture["profile_bytes"]["process"] += b" "
    with pytest.raises(FdmProductionBundleError, match="process profile"):
        build(fixture)


def test_gcode_mutation_fails_closed():
    fixture = retained_fixture()
    fixture["gcode_bytes_by_plate"]["1"] += b"; drift\n"
    with pytest.raises(FdmProductionBundleError, match="G-code"):
        build(fixture)


def test_extra_gcode_plate_fails_closed():
    fixture = retained_fixture()
    fixture["gcode_bytes_by_plate"]["99"] = b"unexpected"
    with pytest.raises(FdmProductionBundleError, match="not present"):
        build(fixture)


def test_path_traversal_retained_filename_fails_closed():
    fixture = retained_fixture()
    fixture["manifest"]["source"]["filename"] = "../fixture.stl"
    with pytest.raises(FdmProductionBundleError, match="simple retained filename"):
        build(fixture)


def test_candidate_bundle_may_defer_only_toolchain_publication_and_physical_qualification():
    fixture = retained_fixture(published=False, qualified=False)
    result = build(fixture, require_production_authority=False)
    assert result.authority_state == AUTHORITY_EVIDENCE_CANDIDATE
    with zipfile.ZipFile(io.BytesIO(result.bytes), "r") as archive:
        bundled = json.loads(archive.read("manifest.json"))
    assert bundled["bundle"]["authorityState"] == AUTHORITY_EVIDENCE_CANDIDATE
    assert bundled["bundle"]["productionEnablementPerformed"] is False


def test_candidate_bundle_cannot_hide_unrelated_validator_failure():
    fixture = retained_fixture(published=False, qualified=False)
    fixture["manifest"]["plates"][0]["validation"]["passed"] = False
    with pytest.raises(FdmProductionBundleError, match="outside the explicitly deferred"):
        build(fixture, require_production_authority=False)


def test_production_bundle_rejects_unpublished_candidate_toolchain():
    fixture = retained_fixture(published=False)
    with pytest.raises(FdmProductionBundleError, match="production-authoritative"):
        build(fixture)


def test_retained_toolchain_manifest_schema_is_checked_after_hash_binding():
    fixture = retained_fixture()
    malformed = canonical({"schema": "wrong-schema"})
    fixture["toolchain_manifest_bytes"] = malformed
    fixture["manifest"]["toolchain"]["toolchainManifestSha256"] = sha(malformed)
    with pytest.raises(FdmProductionBundleError, match="must declare schema"):
        build(fixture)
