from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    p.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "app/fdm_production_bundle.py",
    "from .fdm_authority import AUTHORITY_EVIDENCE_CANDIDATE, AUTHORITY_PRODUCTION, evaluate_fdm_authority\n",
    "from .fdm_authority import AUTHORITY_EVIDENCE_CANDIDATE, AUTHORITY_PRODUCTION, evaluate_fdm_authority\nfrom .fdm_machine_qualification import FdmMachineQualificationError, validate_machine_qualification_receipt\n",
)

replace_once(
    "app/fdm_production_bundle.py",
    "    toolchain = _record(manifest.get(\"toolchain\"))\n",
    "    if authority_state == AUTHORITY_PRODUCTION:\n        qualification = _record(_record(manifest.get(\"machine\")).get(\"qualification\"))\n        request = _record(_record(manifest.get(\"job\")).get(\"request\"))\n        expected_profile_sha = {kind: _record(profiles.get(kind)).get(\"sha256\") for kind in _PROFILE_KINDS}\n        try:\n            normalized_qualification = validate_machine_qualification_receipt(\n                receipt_bytes=machine_qualification_receipt_bytes,\n                evidence_bytes=machine_qualification_evidence_bytes,\n                expected_printer_key=_text(_record(manifest.get(\"machine\")).get(\"key\")),\n                expected_request=request,\n                expected_profile_sha256=expected_profile_sha,\n            )\n        except FdmMachineQualificationError as exc:\n            raise FdmProductionBundleError(str(exc)) from exc\n        expected_qualification = {\n            **normalized_qualification,\n            \"evidenceId\": normalized_qualification[\"qualificationId\"],\n        }\n        if dict(qualification) != expected_qualification:\n            raise FdmProductionBundleError(\n                \"Machine qualification manifest summary differs from the exact retained qualification receipt/evidence bytes.\"\n            )\n\n    toolchain = _record(manifest.get(\"toolchain\"))\n",
)

replace_once(
    "tests/test_fdm_production_bundle.py",
    "def test_profile_mutation_fails_closed():\n",
    "def test_machine_qualification_manifest_summary_must_match_exact_receipt():\n    fixture = retained_fixture()\n    fixture[\"manifest\"][\"machine\"][\"qualification\"][\"review\"][\"reviewerId\"] = \"tampered-reviewer\"\n    with pytest.raises(FdmProductionBundleError, match=\"manifest summary differs\"):\n        build(fixture)\n\n\ndef test_profile_mutation_fails_closed():\n",
)

replace_once(
    "docs/FDM_AUTHORITY_V2_PRODUCTION_API.md",
    "- CP6 retains and independently re-hashes the exact qualification receipt and\n  physical-evidence artifact for production-authoritative bundles;\n",
    "- CP6 independently re-parses/revalidates the exact qualification receipt against\n  the manifest request/profile bindings, verifies its normalized summary, and retains\n  both the receipt and physical-evidence artifact in production-authoritative bundles;\n",
)

print("CP6 qualification semantic verification applied")
