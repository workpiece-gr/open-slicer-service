"""Immutable physical-machine qualification receipt for FDM Authority v2.

This module does not qualify a printer or decide physical acceptance criteria.
It verifies that a human-approved qualification declaration is immutable,
bound to the exact printer/request/profile hashes, and backed by exact retained
physical-evidence bytes. Production authority may consume such a receipt only
after the real-world qualification work has actually been performed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION = "fdm-machine-qualification/1.0.0"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE_KINDS = ("machine", "process", "filament")
_MAX_RECEIPT_BYTES = 1024 * 1024
_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


class FdmMachineQualificationError(ValueError):
    pass


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha(value: Any) -> str:
    text = _text(value).lower()
    return text if _SHA256_RE.fullmatch(text) else ""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _simple_filename(value: Any) -> str:
    name = _text(value)
    if not name or name in {".", ".."} or "/" in name or "\\" in name or Path(name).name != name:
        raise FdmMachineQualificationError("Qualification evidence filename must be a simple retained filename.")
    return name


def _identifier(value: Any, label: str) -> str:
    text = _text(value)
    if not _ID_RE.fullmatch(text):
        raise FdmMachineQualificationError(f"{label} must be a stable 1-128 character identifier.")
    return text


def _completed_at(value: Any) -> str:
    text = _text(value)
    if not text.endswith("Z"):
        raise FdmMachineQualificationError("Qualification review completedAt must be an explicit UTC timestamp ending in Z.")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise FdmMachineQualificationError("Qualification review completedAt is not a valid ISO-8601 UTC timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FdmMachineQualificationError("Qualification review completedAt must be timezone-aware UTC.")
    return text


def validate_machine_qualification_receipt(
    *,
    receipt_bytes: bytes,
    evidence_bytes: bytes,
    expected_printer_key: str,
    expected_request: Mapping[str, Any],
    expected_profile_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Validate and bind one real physical-qualification receipt.

    The receipt is deliberately declarative: physical criteria and measurements
    belong in the retained evidence artifact and human review process. Software
    verifies identity, immutability and exact configuration binding only.
    """

    if not isinstance(receipt_bytes, bytes) or not receipt_bytes or len(receipt_bytes) > _MAX_RECEIPT_BYTES:
        raise FdmMachineQualificationError("Qualification receipt must contain exact non-empty bytes within the 1 MiB limit.")
    if not isinstance(evidence_bytes, bytes) or not evidence_bytes or len(evidence_bytes) > _MAX_EVIDENCE_BYTES:
        raise FdmMachineQualificationError("Qualification physical evidence must contain exact non-empty bytes within the 64 MiB limit.")
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FdmMachineQualificationError("Qualification receipt must be valid UTF-8 JSON.") from exc
    if not isinstance(receipt, dict):
        raise FdmMachineQualificationError("Qualification receipt must be a JSON object.")
    if _canonical_json(receipt) != receipt_bytes:
        raise FdmMachineQualificationError("Qualification receipt must use canonical deterministic JSON bytes.")
    if receipt.get("contractVersion") != FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION:
        raise FdmMachineQualificationError(
            f"Qualification receipt must declare contractVersion={FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION}."
        )
    if receipt.get("productionReady") is not True:
        raise FdmMachineQualificationError("Production qualification receipt must explicitly declare productionReady=true.")

    printer_key = _text(receipt.get("printerKey"))
    if not printer_key or printer_key != _text(expected_printer_key):
        raise FdmMachineQualificationError("Qualification receipt printerKey does not match the exact production printer.")
    qualification_id = _identifier(receipt.get("qualificationId"), "Qualification id")
    protocol_id = _identifier(receipt.get("protocolId"), "Qualification protocol id")

    request = _record(receipt.get("request"))
    expected = {
        "material": _text(expected_request.get("material")).lower(),
        "quality": _text(expected_request.get("quality")).lower(),
        "strength": _text(expected_request.get("strength")).lower(),
    }
    actual = {kind: _text(request.get(kind)).lower() for kind in expected}
    if not all(expected.values()) or actual != expected:
        raise FdmMachineQualificationError("Qualification receipt request does not match the exact material/quality/strength profile selection.")

    profiles = _record(receipt.get("profileSha256"))
    normalized_profiles: dict[str, str] = {}
    for kind in _PROFILE_KINDS:
        expected_sha = _sha(expected_profile_sha256.get(kind))
        actual_sha = _sha(profiles.get(kind))
        if not expected_sha or actual_sha != expected_sha:
            raise FdmMachineQualificationError(f"Qualification receipt {kind} profile SHA-256 does not match the exact retained profile bytes.")
        normalized_profiles[kind] = actual_sha

    evidence = _record(receipt.get("evidence"))
    evidence_name = _simple_filename(evidence.get("filename"))
    evidence_media_type = _text(evidence.get("mediaType"))
    if not evidence_media_type or len(evidence_media_type) > 120:
        raise FdmMachineQualificationError("Qualification evidence mediaType is required and must be at most 120 characters.")
    expected_evidence_bytes = evidence.get("bytes")
    if isinstance(expected_evidence_bytes, bool) or not isinstance(expected_evidence_bytes, int) or expected_evidence_bytes < 1:
        raise FdmMachineQualificationError("Qualification evidence byte count must be a positive integer.")
    evidence_sha = _sha(evidence.get("sha256"))
    if not evidence_sha or expected_evidence_bytes != len(evidence_bytes) or evidence_sha != _digest(evidence_bytes):
        raise FdmMachineQualificationError("Qualification evidence bytes do not match the receipt SHA-256/byte-count binding.")

    review = _record(receipt.get("review"))
    if _text(review.get("status")).lower() != "approved":
        raise FdmMachineQualificationError("Qualification receipt must retain an approved human review decision.")
    reviewer_id = _identifier(review.get("reviewerId"), "Qualification reviewer id")
    completed_at = _completed_at(review.get("completedAt"))

    return {
        "contractVersion": FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION,
        "productionReady": True,
        "qualificationId": qualification_id,
        "protocolId": protocol_id,
        "printerKey": printer_key,
        "request": actual,
        "profileSha256": normalized_profiles,
        "receiptSha256": _digest(receipt_bytes),
        "receiptBytes": len(receipt_bytes),
        "evidence": {
            "filename": evidence_name,
            "mediaType": evidence_media_type,
            "bytes": len(evidence_bytes),
            "sha256": evidence_sha,
        },
        "review": {
            "status": "approved",
            "reviewerId": reviewer_id,
            "completedAt": completed_at,
        },
    }
