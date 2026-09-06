from __future__ import annotations

import copy
import hashlib

import pytest

from app.fdm_authority import (
    AUTHORITY_EVIDENCE_CANDIDATE,
    AUTHORITY_PRODUCTION,
    FDM_GCODE_VALIDATION_VERSION,
    FDM_JOB_CONTRACT_VERSION,
    FDM_PRODUCTION_MANIFEST_VERSION,
)
from app.fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION
from app.fdm_pricing import (
    FDM_PRICING_CONTRACT_VERSION,
    FDM_PRICING_POLICY_VERSION,
    FdmPricingError,
    inspect_exact_gcode_support,
    price_exact_fdm_job,
)
from app.fdm_toolchain_provenance import FDM_TOOLCHAIN_LOCK_SCHEMA, FDM_TOOLCHAIN_PROVENANCE_VERSION


SOURCE_SHA = "1" * 64
PROJECT_SHA = "2" * 64
PROFILE_HASHES = {"machine": "3" * 64, "process": "4" * 64, "filament": "5" * 64}
ORCA_SHA = "8" * 64
RUNTIME_SHA = "9" * 64
RUNTIME_REF = f"ghcr.io/workpiece-gr/open-slicer-service@sha256:{RUNTIME_SHA}"


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def gcode(*, support: bool = False, support_comment_only: bool = False) -> bytes:
    lines = [
        "; Orca pricing fixture",
        "G21",
        "G90",
        "M83",
        ";TYPE:Inner wall",
        "G1 X10 Y10 Z0.2 F6000",
        "G1 X30 Y10 E1.2 F1200",
    ]
    if support or support_comment_only:
        lines.extend([";TYPE:Support", "G1 X20 Y20 F6000"])
        if support:
            lines.append("G1 X25 Y20 E0.8 F1200")
    lines.extend([";TYPE:Top surface", "G1 X30 Y30 E0.4 F1200", ""])
    return "\n".join(lines).encode()


def manifest_fixture(
    *,
    material: str = "pla",
    quantity: int = 1,
    filament_grams: float = 100.0,
    print_seconds: int = 3600,
    support: bool = False,
    support_comment_only: bool = False,
) -> tuple[dict, dict[str, bytes]]:
    payload = gcode(support=support, support_comment_only=support_comment_only)
    gcode_sha = sha(payload)
    instances = []
    cp4_instances = []
    instance_ids = []
    object_names = []
    for index in range(1, quantity + 1):
        instance_id = f"instance-{index:03d}"
        object_name = f"object-{index:03d}"
        instance_ids.append(instance_id)
        object_names.append(object_name)
        instance = {
            "id": instance_id,
            "objectId": str(index),
            "plateId": "plate-001",
            "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10 + index, 10 + index, 0],
            "boundsMm": {"min": [10 + index, 10 + index, 0], "max": [30 + index, 30 + index, 20]},
        }
        instances.append(instance)
        cp4_instances.append(
            {
                **copy.deepcopy(instance),
                "gcodeEvidence": {
                    "objectName": object_name,
                    "definitionCenterMm": [20 + index, 20 + index],
                    "definitionBoundsMm": {"min": [10 + index, 10 + index], "max": [30 + index, 30 + index]},
                    "extrusionSegmentCount": 3 if support else 2,
                    "extrusionBoundsMm": {"min": [10 + index, 10 + index, 0.2], "max": [30 + index, 30 + index, 20]},
                    "gcodeSha256": gcode_sha,
                },
            }
        )

    validation = {
        "contractVersion": FDM_GCODE_VALIDATION_VERSION,
        "validator": {"name": "workpiece-gcode-validator", "version": "1.0.0", "serviceCommit": "a" * 40},
        "passed": True,
        "authorityCriticalComplete": True,
        "gcodeSha256": gcode_sha,
        "projectSha256": PROJECT_SHA,
        "profileSha256": dict(PROFILE_HASHES),
        "toolchainRef": RUNTIME_REF,
    }
    plate = {
        "id": "plate-001",
        "index": 1,
        "instanceIds": instance_ids,
        "projectSha256": PROJECT_SHA,
        "profileSha256": dict(PROFILE_HASHES),
        "toolchainRef": RUNTIME_REF,
        "gcode": {"filename": "plate_1.gcode", "bytes": len(payload), "sha256": gcode_sha},
        "validation": validation,
        "statistics": {
            "filamentGrams": filament_grams,
            "printTimeSeconds": print_seconds,
            "layerCount": 100,
            "filamentSource": "reported_from_hashed_gcode",
            "printTimeSource": "reported_from_hashed_gcode",
            "layerCountSource": "reported_from_hashed_gcode",
        },
    }
    manifest = {
        "contractVersion": FDM_PRODUCTION_MANIFEST_VERSION,
        "job": {
            "contractVersion": FDM_JOB_CONTRACT_VERSION,
            "request": {
                "material": material,
                "quality": "balanced",
                "strength": "functional",
                "quantity": quantity,
                "supports": "automatic",
                "orientation": "orca_auto",
                "arrangement": "orca_auto",
            },
        },
        "source": {"filename": "source-original.stl", "bytes": 1000, "sha256": SOURCE_SHA, "immutable": True},
        "machine": {
            "key": "ratrig_vcore3_300",
            "qualification": {"productionReady": True, "evidenceId": "ratrig-production-profile-v1"},
        },
        "profiles": {
            "machine": {"identity": "ratrig_vcore3_300:machine", "sha256": PROFILE_HASHES["machine"]},
            "process": {
                "identity": f"ratrig_vcore3_300:{material}:balanced:functional:process",
                "sha256": PROFILE_HASHES["process"],
            },
            "filament": {"identity": f"ratrig_vcore3_300:{material}:filament", "sha256": PROFILE_HASHES["filament"]},
        },
        "toolchain": {
            "contractVersion": FDM_TOOLCHAIN_PROVENANCE_VERSION,
            "authorityState": AUTHORITY_PRODUCTION,
            "authorityCriticalComplete": True,
            "orcaVersion": "2.4.2",
            "orcaBinarySha256": ORCA_SHA,
            "serviceCommit": "a" * 40,
            "executionEnvironment": {"reference": RUNTIME_REF, "digest": f"sha256:{RUNTIME_SHA}"},
            "toolchainImage": {
                "reference": "ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:" + "b" * 64,
                "digest": "sha256:" + "b" * 64,
            },
            "toolchainManifestSha256": "c" * 64,
            "baseImage": {"reference": "ubuntu:noble@sha256:" + "d" * 64},
            "upstreamOrca": {"asset": "OrcaSlicer.AppImage", "assetSha256": "e" * 64, "releaseAssetId": 123456},
            "packageInventorySha256": "f" * 64,
            "lockSchema": FDM_TOOLCHAIN_LOCK_SCHEMA,
            "lockStatus": "published",
            "lockSha256": "0" * 64,
        },
        "project": {
            "filename": "workpiece-production.3mf",
            "bytes": 5000,
            "sha256": PROJECT_SHA,
            "sourceSha256": SOURCE_SHA,
            "profileSha256": dict(PROFILE_HASHES),
            "toolchainRef": RUNTIME_REF,
        },
        "instances": instances,
        "plates": [plate],
        "instancePlateEvidence": {
            "contractVersion": INSTANCE_PLATE_EVIDENCE_VERSION,
            "authorityState": AUTHORITY_EVIDENCE_CANDIDATE,
            "projectSha256": PROJECT_SHA,
            "sourceSha256": SOURCE_SHA,
            "printerKey": "ratrig_vcore3_300",
            "profileSha256": dict(PROFILE_HASHES),
            "instances": cp4_instances,
            "plates": [
                {
                    "id": "plate-001",
                    "index": 1,
                    "projectSha256": PROJECT_SHA,
                    "gcodeSha256": gcode_sha,
                    "validationGcodeSha256": gcode_sha,
                    "instanceIds": instance_ids,
                    "objectNames": object_names,
                }
            ],
            "totals": {"instanceCount": quantity, "plateCount": 1},
        },
        "totals": {
            "plateCount": 1,
            "instanceCount": quantity,
            "filamentGrams": filament_grams,
            "printTimeSeconds": print_seconds,
        },
        "commercial": {"priceAuthoritative": False, "authority": "preview_only"},
        "review": {"required": True, "status": "pending"},
    }
    return manifest, {"plate-001": payload}


def test_support_requires_positive_extrusion_in_support_role_not_settings_or_comment_alone():
    assert inspect_exact_gcode_support(gcode())["used"] is False
    comment_only = inspect_exact_gcode_support(gcode(support_comment_only=True))
    assert comment_only["used"] is False
    assert "Support" in comment_only["supportRoles"]
    actual = inspect_exact_gcode_support(gcode(support=True))
    assert actual["used"] is True
    assert actual["supportExtrusionSegmentCount"] == 1
    assert actual["supportRoles"] == ["Support"]


def test_standard_reference_case_matches_current_browser_formula_without_cart_minimum():
    manifest, gcodes = manifest_fixture()
    result = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    assert result["contractVersion"] == FDM_PRICING_CONTRACT_VERSION
    assert result["policyVersion"] == FDM_PRICING_POLICY_VERSION
    assert result["priceAuthoritative"] is True
    assert result["productionOrderEligible"] is True
    assert result["support"]["used"] is False
    assert result["authoritativePriceCents"] == 1732
    assert result["orderMinimum"] == {"minimumOrderCents": 2000, "applied": False, "scope": "whole_cart_checkout"}
    assert result["policy"]["manualReviewMultiplierApplied"] is False


def test_real_support_extrusion_matches_current_support_formula():
    manifest, gcodes = manifest_fixture(support=True)
    result = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    assert result["support"]["used"] is True
    assert result["authoritativePriceCents"] == 2136
    assert result["breakdownCents"]["supportHandling"] == 200


def test_exact_job_totals_are_not_multiplied_by_quantity_again():
    manifest, gcodes = manifest_fixture(quantity=5, filament_grams=500, print_seconds=7200)
    result = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    assert result["quantity"] == 5
    assert result["exactStatistics"]["filamentGrams"] == "500.000000"
    assert result["policy"]["quantityDiscountFactor"] == "0.95"
    assert result["authoritativePriceCents"] == 5119


@pytest.mark.parametrize(
    ("material", "expected_cents"),
    [("pla", 1732), ("petg", 1785), ("pctg", 1943), ("abs", 1785), ("tpu", 2102)],
)
def test_current_workpiece_material_input_costs_are_server_policy(material, expected_cents):
    manifest, gcodes = manifest_fixture(material=material)
    result = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    assert result["authoritativePriceCents"] == expected_cents


def test_gcode_byte_drift_fails_closed_before_pricing():
    manifest, gcodes = manifest_fixture()
    gcodes["plate-001"] += b"; drift\n"
    with pytest.raises(FdmPricingError, match="SHA-256"):
        price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)


def test_unrelated_manufacturing_authority_failure_cannot_be_hidden_by_price_receipt():
    manifest, gcodes = manifest_fixture()
    manifest["plates"][0]["validation"]["passed"] = False
    with pytest.raises(FdmPricingError, match="complete exact manufacturing evidence"):
        price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)


def test_physical_qualification_can_remain_separate_from_authoritative_price():
    manifest, gcodes = manifest_fixture()
    manifest["machine"]["qualification"]["productionReady"] = False
    result = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    assert result["priceAuthoritative"] is True
    assert result["manufacturingAuthorityState"] == AUTHORITY_EVIDENCE_CANDIDATE
    assert result["manufacturingAuthorityIssues"] == ["machine_not_production_ready"]
    assert result["productionOrderEligible"] is False


def test_per_plate_statistics_must_reconcile_before_commercial_math():
    manifest, gcodes = manifest_fixture()
    manifest["plates"][0]["statistics"]["filamentGrams"] = 99.0
    with pytest.raises(FdmPricingError, match="incomplete_or_mismatched_totals"):
        price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)


def test_pricing_receipt_is_deterministic_for_identical_evidence():
    manifest, gcodes = manifest_fixture(support=True)
    first = price_exact_fdm_job(manifest=manifest, gcode_bytes_by_plate=gcodes)
    second = price_exact_fdm_job(manifest=copy.deepcopy(manifest), gcode_bytes_by_plate=dict(gcodes))
    assert first == second
    digest = first["pricingReceiptSha256"]
    unsigned = dict(first)
    unsigned.pop("pricingReceiptSha256")
    expected = hashlib.sha256(
        (json_dumps(unsigned) + "\n").encode()
    ).hexdigest()
    assert digest == expected


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
