import hashlib
import json

import pytest

from app.fdm_machine_qualification import (
    FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION,
    FdmMachineQualificationError,
    validate_machine_qualification_receipt,
)


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def fixture():
    evidence = b"synthetic qualification evidence for contract testing only\n"
    profiles = {"machine": "a" * 64, "process": "b" * 64, "filament": "c" * 64}
    request = {"material": "pla", "quality": "balanced", "strength": "functional"}
    receipt = {
        "contractVersion": FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION,
        "qualificationId": "synthetic-ratrig-qualification-v1",
        "protocolId": "synthetic-policy-boundary-v1",
        "printerKey": "ratrig_vcore3_300",
        "productionReady": True,
        "request": dict(request),
        "profileSha256": dict(profiles),
        "evidence": {
            "filename": "synthetic-qualification-report.txt",
            "mediaType": "text/plain",
            "bytes": len(evidence),
            "sha256": hashlib.sha256(evidence).hexdigest(),
        },
        "review": {
            "status": "approved",
            "reviewerId": "synthetic-reviewer",
            "completedAt": "2026-09-06T12:00:00Z",
        },
    }
    return receipt, evidence, request, profiles


def validate(receipt: dict, evidence: bytes, request: dict, profiles: dict):
    return validate_machine_qualification_receipt(
        receipt_bytes=canonical(receipt),
        evidence_bytes=evidence,
        expected_printer_key="ratrig_vcore3_300",
        expected_request=request,
        expected_profile_sha256=profiles,
    )


def test_receipt_binds_exact_printer_request_profiles_and_evidence():
    receipt, evidence, request, profiles = fixture()
    result = validate(receipt, evidence, request, profiles)
    assert result["productionReady"] is True
    assert result["qualificationId"] == "synthetic-ratrig-qualification-v1"
    assert result["printerKey"] == "ratrig_vcore3_300"
    assert result["request"] == request
    assert result["profileSha256"] == profiles
    assert result["evidence"]["sha256"] == hashlib.sha256(evidence).hexdigest()
    assert result["receiptSha256"] == hashlib.sha256(canonical(receipt)).hexdigest()
    assert result["review"]["status"] == "approved"


def test_receipt_requires_canonical_exact_bytes():
    receipt, evidence, request, profiles = fixture()
    noncanonical = json.dumps(receipt, indent=2).encode()
    with pytest.raises(FdmMachineQualificationError, match="canonical deterministic JSON"):
        validate_machine_qualification_receipt(
            receipt_bytes=noncanonical,
            evidence_bytes=evidence,
            expected_printer_key="ratrig_vcore3_300",
            expected_request=request,
            expected_profile_sha256=profiles,
        )


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update(productionReady=False), "productionReady=true"),
        (lambda value: value.update(printerKey="other_printer"), "printerKey"),
        (lambda value: value["request"].update(material="petg"), "request does not match"),
        (lambda value: value["profileSha256"].update(process="d" * 64), "process profile SHA-256"),
        (lambda value: value["review"].update(status="pending"), "approved human review"),
    ],
)
def test_receipt_rejects_authority_drift(mutation, match):
    receipt, evidence, request, profiles = fixture()
    mutation(receipt)
    with pytest.raises(FdmMachineQualificationError, match=match):
        validate(receipt, evidence, request, profiles)


def test_receipt_rejects_mutated_physical_evidence_bytes():
    receipt, evidence, request, profiles = fixture()
    with pytest.raises(FdmMachineQualificationError, match="evidence bytes do not match"):
        validate(receipt, evidence + b"drift", request, profiles)


def test_receipt_does_not_accept_an_identifier_without_real_retained_evidence_bytes():
    receipt, _evidence, request, profiles = fixture()
    with pytest.raises(FdmMachineQualificationError, match="physical evidence must contain exact non-empty bytes"):
        validate_machine_qualification_receipt(
            receipt_bytes=canonical(receipt),
            evidence_bytes=b"",
            expected_printer_key="ratrig_vcore3_300",
            expected_request=request,
            expected_profile_sha256=profiles,
        )
