from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    p.write_text(text.replace(old, new), encoding="utf-8")


# Shared pipeline: validate real qualification bytes only for production-ready jobs.
replace_once(
    "app/fdm_authority_pipeline.py",
    "from .fdm_instance_plate_evidence import build_instance_plate_evidence\n",
    "from .fdm_instance_plate_evidence import build_instance_plate_evidence\nfrom .fdm_machine_qualification import FdmMachineQualificationError, validate_machine_qualification_receipt\n",
)
replace_once(
    "app/fdm_authority_pipeline.py",
    "    review_status: str = \"pending\",\n    require_production_authority: bool = False,\n) -> FdmAuthorityPipelineResult:\n",
    "    review_status: str = \"pending\",\n    require_production_authority: bool = False,\n    machine_qualification_receipt_bytes: bytes | None = None,\n    machine_qualification_evidence_bytes: bytes | None = None,\n) -> FdmAuthorityPipelineResult:\n",
)
replace_once(
    "app/fdm_authority_pipeline.py",
    "    profile_hashes = _verify_profile_bytes(generation_receipt, profile_bytes)\n    normalized_profiles = {kind: bytes(profile_bytes[kind]) for kind in (\"machine\", \"process\", \"filament\")}\n",
    "    profile_hashes = _verify_profile_bytes(generation_receipt, profile_bytes)\n    normalized_profiles = {kind: bytes(profile_bytes[kind]) for kind in (\"machine\", \"process\", \"filament\")}\n\n    qualification_manifest: dict[str, Any] = {\n        \"productionReady\": False,\n        \"evidenceId\": qualification_evidence,\n    }\n    if machine_production_ready:\n        if not isinstance(machine_qualification_receipt_bytes, bytes) or not isinstance(machine_qualification_evidence_bytes, bytes):\n            raise FdmAuthorityPipelineError(\n                \"Production-ready machine authority requires exact qualification receipt and physical-evidence bytes.\"\n            )\n        try:\n            qualification = validate_machine_qualification_receipt(\n                receipt_bytes=machine_qualification_receipt_bytes,\n                evidence_bytes=machine_qualification_evidence_bytes,\n                expected_printer_key=printer_key,\n                expected_request={\"material\": material, \"quality\": quality, \"strength\": strength},\n                expected_profile_sha256=profile_hashes,\n            )\n        except FdmMachineQualificationError as exc:\n            raise FdmAuthorityPipelineError(str(exc)) from exc\n        if qualification[\"qualificationId\"] != qualification_evidence:\n            raise FdmAuthorityPipelineError(\n                \"Machine qualification evidence id does not match the immutable qualification receipt.\"\n            )\n        qualification_manifest = {**qualification, \"evidenceId\": qualification[\"qualificationId\"]}\n    elif machine_qualification_receipt_bytes is not None or machine_qualification_evidence_bytes is not None:\n        raise FdmAuthorityPipelineError(\n            \"Candidate machine state must not attach production qualification receipt/evidence bytes.\"\n        )\n",
)
replace_once(
    "app/fdm_authority_pipeline.py",
    "            \"qualification\": {\n                \"productionReady\": machine_production_ready,\n                \"evidenceId\": qualification_evidence,\n            },\n",
    "            \"qualification\": qualification_manifest,\n",
)
replace_once(
    "app/fdm_authority_pipeline.py",
    "        package_inventory_bytes=package_inventory_bytes,\n        require_production_authority=require_production_authority,\n",
    "        package_inventory_bytes=package_inventory_bytes,\n        machine_qualification_receipt_bytes=machine_qualification_receipt_bytes,\n        machine_qualification_evidence_bytes=machine_qualification_evidence_bytes,\n        require_production_authority=require_production_authority,\n",
)

# Authority evaluator: a bare non-empty evidence id is no longer production authority.
replace_once(
    "app/fdm_authority.py",
    "from .fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION\n",
    "from .fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION\nfrom .fdm_machine_qualification import FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION\n",
)
replace_once(
    "app/fdm_authority.py",
    "    qualification = _record(machine.get(\"qualification\"))\n    if qualification.get(\"productionReady\") is not True or not _text(qualification.get(\"evidenceId\")):\n        _issue(issues, \"machine_not_production_ready\", \"machine.qualification\", \"Physical machine/profile qualification must explicitly be production-ready and evidence-backed.\")\n",
    "    qualification = _record(machine.get(\"qualification\"))\n    qualification_request = _record(qualification.get(\"request\"))\n    qualification_profiles = _record(qualification.get(\"profileSha256\"))\n    qualification_evidence = _record(qualification.get(\"evidence\"))\n    qualification_review = _record(qualification.get(\"review\"))\n    qualification_valid = (\n        qualification.get(\"productionReady\") is True\n        and qualification.get(\"contractVersion\") == FDM_MACHINE_QUALIFICATION_CONTRACT_VERSION\n        and bool(_text(qualification.get(\"qualificationId\")))\n        and _text(qualification.get(\"evidenceId\")) == _text(qualification.get(\"qualificationId\"))\n        and bool(_text(qualification.get(\"protocolId\")))\n        and _text(qualification.get(\"printerKey\")) == machine_key\n        and _sha(qualification.get(\"receiptSha256\")) != \"\"\n        and _pos_int(qualification.get(\"receiptBytes\")) is not None\n        and _text(qualification_request.get(\"material\")).lower() == material\n        and _text(qualification_request.get(\"quality\")).lower() == quality\n        and _text(qualification_request.get(\"strength\")).lower() == strength\n        and all(profile_hashes.get(kind) and _sha(qualification_profiles.get(kind)) == profile_hashes[kind] for kind in _PROFILE_KINDS)\n        and bool(_text(qualification_evidence.get(\"filename\")))\n        and bool(_text(qualification_evidence.get(\"mediaType\")))\n        and _pos_int(qualification_evidence.get(\"bytes\")) is not None\n        and _sha(qualification_evidence.get(\"sha256\")) != \"\"\n        and _text(qualification_review.get(\"status\")).lower() == \"approved\"\n        and bool(_text(qualification_review.get(\"reviewerId\")))\n        and bool(_text(qualification_review.get(\"completedAt\")))\n    )\n    if not qualification_valid:\n        _issue(\n            issues,\n            \"machine_not_production_ready\",\n            \"machine.qualification\",\n            \"Physical machine/profile qualification must be backed by an immutable approved receipt bound to the exact request/profile hashes and retained physical evidence.\",\n        )\n",
)

# CP6: production-authoritative bundles must contain the exact receipt and physical evidence.
replace_once(
    "app/fdm_production_bundle.py",
    "    package_inventory_bytes: bytes,\n    require_production_authority: bool = True,\n",
    "    package_inventory_bytes: bytes,\n    machine_qualification_receipt_bytes: bytes | None = None,\n    machine_qualification_evidence_bytes: bytes | None = None,\n    require_production_authority: bool = True,\n",
)
replace_once(
    "app/fdm_production_bundle.py",
    "    authority_state = _authority_state(manifest, require_production_authority=require_production_authority)\n\n    source = _record(manifest.get(\"source\"))\n",
    "    authority_state = _authority_state(manifest, require_production_authority=require_production_authority)\n\n    qualification_paths: dict[str, str] | None = None\n    if authority_state == AUTHORITY_PRODUCTION:\n        qualification = _record(_record(manifest.get(\"machine\")).get(\"qualification\"))\n        _exact_bytes(\n            \"machine qualification receipt\",\n            machine_qualification_receipt_bytes,\n            qualification.get(\"receiptSha256\"),\n            qualification.get(\"receiptBytes\"),\n        )\n        qualification_evidence = _record(qualification.get(\"evidence\"))\n        evidence_name = _simple_filename(qualification_evidence.get(\"filename\"), \"Machine qualification evidence filename\")\n        _exact_bytes(\n            \"machine qualification physical evidence\",\n            machine_qualification_evidence_bytes,\n            qualification_evidence.get(\"sha256\"),\n            qualification_evidence.get(\"bytes\"),\n        )\n        qualification_paths = {\n            \"receipt\": \"evidence/machine-qualification-receipt.json\",\n            \"physicalEvidence\": f\"evidence/machine-qualification/{evidence_name}\",\n        }\n    elif machine_qualification_receipt_bytes is not None or machine_qualification_evidence_bytes is not None:\n        raise FdmProductionBundleError(\n            \"Candidate CP6 bundles must not retain production-only machine qualification bytes.\"\n        )\n\n    source = _record(manifest.get(\"source\"))\n",
)
replace_once(
    "app/fdm_production_bundle.py",
    "        (\"evidence/instance-plate.json\", _canonical_json(_record(manifest.get(\"instancePlateEvidence\")))),\n    ]\n\n    plate_file_map",
    "        (\"evidence/instance-plate.json\", _canonical_json(_record(manifest.get(\"instancePlateEvidence\")))),\n    ]\n    if qualification_paths is not None:\n        retained.extend(\n            [\n                (qualification_paths[\"receipt\"], machine_qualification_receipt_bytes),\n                (qualification_paths[\"physicalEvidence\"], machine_qualification_evidence_bytes),\n            ]\n        )\n\n    plate_file_map",
)
replace_once(
    "app/fdm_production_bundle.py",
    "        \"productionEnablementPerformed\": False,\n    }\n    manifest_payload = _canonical_json(bundle_manifest)\n",
    "        \"productionEnablementPerformed\": False,\n    }\n    if qualification_paths is not None:\n        bundle_manifest[\"bundle\"][\"machineQualificationPaths\"] = dict(qualification_paths)\n    manifest_payload = _canonical_json(bundle_manifest)\n",
)

# Production wrapper: validate receipt bytes before the shared authority pipeline.
replace_once(
    "app/fdm_authority_production.py",
    "* the selected physical RatRig/profile combination must have an explicit,\n  non-empty qualification evidence identifier supplied by configuration.\n",
    "* the selected physical RatRig/profile combination must have an immutable,\n  approved qualification receipt plus the exact retained physical-evidence bytes.\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "from .fdm_authority_pipeline import FdmAuthorityPipelineError, build_fdm_authority_pipeline\n",
    "from .fdm_authority_pipeline import FdmAuthorityPipelineError, build_fdm_authority_pipeline\nfrom .fdm_machine_qualification import FdmMachineQualificationError, validate_machine_qualification_receipt\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "def _verify_profile_bytes(generation_receipt: Mapping[str, Any], profile_bytes: Mapping[str, bytes]) -> None:\n",
    "def _verify_profile_bytes(generation_receipt: Mapping[str, Any], profile_bytes: Mapping[str, bytes]) -> dict[str, str]:\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "    for kind in (\"machine\", \"process\", \"filament\"):\n        payload = profile_bytes.get(kind)\n        receipt = receipts.get(kind)\n",
    "    hashes: dict[str, str] = {}\n    for kind in (\"machine\", \"process\", \"filament\"):\n        payload = profile_bytes.get(kind)\n        receipt = receipts.get(kind)\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "        if hashlib.sha256(payload).hexdigest() != expected:\n            raise ValueError(f\"Exact {kind} profile bytes do not match the project generation receipt.\")\n\n\ndef build_fdm_authority_production",
    "        if hashlib.sha256(payload).hexdigest() != expected:\n            raise ValueError(f\"Exact {kind} profile bytes do not match the project generation receipt.\")\n        hashes[kind] = expected\n    return hashes\n\n\ndef build_fdm_authority_production",
)
replace_once(
    "app/fdm_authority_production.py",
    "    base_env: Mapping[str, str],\n) -> FdmAuthorityProductionResult:\n",
    "    base_env: Mapping[str, str],\n    machine_qualification_receipt_bytes: bytes | None = None,\n    machine_qualification_evidence_bytes: bytes | None = None,\n) -> FdmAuthorityProductionResult:\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "    qualification_id = machine_qualification_evidence_id.strip() if isinstance(machine_qualification_evidence_id, str) else \"\"\n    if not qualification_id:\n        raise ValueError(\"Production FDM authority requires an explicit machine qualification evidence id.\")\n",
    "    qualification_id = machine_qualification_evidence_id.strip() if isinstance(machine_qualification_evidence_id, str) else \"\"\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "    _verify_profile_bytes(generation_receipt, profile_bytes)\n\n    # This is intentionally evaluated before any CP2/Orca reopen work.",
    "    profile_hashes = _verify_profile_bytes(generation_receipt, profile_bytes)\n    if not isinstance(machine_qualification_receipt_bytes, bytes) or not isinstance(machine_qualification_evidence_bytes, bytes):\n        raise ValueError(\"Production FDM authority requires exact machine qualification receipt and physical-evidence bytes.\")\n    try:\n        qualification = validate_machine_qualification_receipt(\n            receipt_bytes=machine_qualification_receipt_bytes,\n            evidence_bytes=machine_qualification_evidence_bytes,\n            expected_printer_key=RATRIG_PRINTER_KEY,\n            expected_request={\"material\": material, \"quality\": quality, \"strength\": strength},\n            expected_profile_sha256=profile_hashes,\n        )\n    except FdmMachineQualificationError as exc:\n        raise ValueError(str(exc)) from exc\n    if qualification_id and qualification_id != qualification[\"qualificationId\"]:\n        raise ValueError(\"Configured machine qualification evidence id differs from the immutable qualification receipt.\")\n    qualification_id = qualification[\"qualificationId\"]\n\n    # This is intentionally evaluated before any CP2/Orca reopen work.",
)
replace_once(
    "app/fdm_authority_production.py",
    "            machine_qualification_evidence_id=qualification_id,\n            validator_service_commit=service_commit,\n",
    "            machine_qualification_evidence_id=qualification_id,\n            validator_service_commit=service_commit,\n",
)
replace_once(
    "app/fdm_authority_production.py",
    "            require_production_authority=True,\n        )\n",
    "            require_production_authority=True,\n            machine_qualification_receipt_bytes=machine_qualification_receipt_bytes,\n            machine_qualification_evidence_bytes=machine_qualification_evidence_bytes,\n        )\n",
)

# Production HTTP entrypoint: readiness is based on immutable files, not a free-form id.
replace_once(
    "app/authority_production_api.py",
    "digest-pinned image, use a committed *published* CP5 lock, and configure a real\nmachine-qualification evidence id before a request can reach the authority\npipeline.\n",
    "digest-pinned image, use a committed *published* CP5 lock, and mount the exact\nmachine-qualification receipt plus its retained physical-evidence artifact before\na request can reach the authority pipeline.\n",
)
replace_once(
    "app/authority_production_api.py",
    "WORKPIECE_FDM_AUTHORITY_RUNTIME_REF = os.getenv(\"WORKPIECE_FDM_AUTHORITY_RUNTIME_REF\", \"\").strip().lower()\nWORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID = os.getenv(\"WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID\", \"\").strip()\n",
    "WORKPIECE_FDM_AUTHORITY_RUNTIME_REF = os.getenv(\"WORKPIECE_FDM_AUTHORITY_RUNTIME_REF\", \"\").strip().lower()\nFDM_MACHINE_QUALIFICATION_RECEIPT = Path(os.getenv(\"FDM_MACHINE_QUALIFICATION_RECEIPT\", \"/app/fdm-machine-qualification.json\"))\nFDM_MACHINE_QUALIFICATION_EVIDENCE = Path(os.getenv(\"FDM_MACHINE_QUALIFICATION_EVIDENCE\", \"/app/fdm-machine-qualification-evidence.bin\"))\n",
)
replace_once(
    "app/authority_production_api.py",
    "        \"machine_qualification_evidence\": bool(WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID),\n",
    "        \"machine_qualification_receipt\": FDM_MACHINE_QUALIFICATION_RECEIPT.is_file(),\n        \"machine_qualification_evidence\": FDM_MACHINE_QUALIFICATION_EVIDENCE.is_file(),\n",
)
replace_once(
    "app/authority_production_api.py",
    "        \"published_toolchain_lock\",\n        \"machine_qualification_evidence\",\n",
    "        \"published_toolchain_lock\",\n        \"machine_qualification_receipt\",\n        \"machine_qualification_evidence\",\n",
)
replace_once(
    "app/authority_production_api.py",
    "                machine_qualification_evidence_id=WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID,\n                toolchain_lock_bytes=FDM_TOOLCHAIN_LOCK.read_bytes(),\n",
    "                machine_qualification_evidence_id=\"\",\n                toolchain_lock_bytes=FDM_TOOLCHAIN_LOCK.read_bytes(),\n",
)
replace_once(
    "app/authority_production_api.py",
    "                orca_runtime_bytes=ORCA_BIN.read_bytes(),\n                base_env=os.environ,\n",
    "                orca_runtime_bytes=ORCA_BIN.read_bytes(),\n                base_env=os.environ,\n                machine_qualification_receipt_bytes=FDM_MACHINE_QUALIFICATION_RECEIPT.read_bytes(),\n                machine_qualification_evidence_bytes=FDM_MACHINE_QUALIFICATION_EVIDENCE.read_bytes(),\n",
)

# Synthetic authority fixture gains the complete immutable qualification summary.
qualification_summary_authority = '''            "qualification": {
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
            },'''
replace_once(
    "tests/test_fdm_authority.py",
    '            "qualification": {"productionReady": True, "evidenceId": "ratrig-production-profile-v1"},',
    qualification_summary_authority,
)

qualification_summary_pricing = '''            "qualification": {
                "contractVersion": "fdm-machine-qualification/1.0.0",
                "productionReady": True,
                "qualificationId": "ratrig-production-profile-v1",
                "evidenceId": "ratrig-production-profile-v1",
                "protocolId": "workpiece-ratrig-qualification-v1",
                "printerKey": "ratrig_vcore3_300",
                "request": {"material": material, "quality": "balanced", "strength": "functional"},
                "profileSha256": dict(PROFILE_HASHES),
                "receiptSha256": "a" * 64,
                "receiptBytes": 512,
                "evidence": {"filename": "qualification-evidence.pdf", "mediaType": "application/pdf", "bytes": 1024, "sha256": "b" * 64},
                "review": {"status": "approved", "reviewerId": "workpiece-reviewer", "completedAt": "2026-09-06T00:00:00Z"},
            },'''
replace_once(
    "tests/test_fdm_pricing.py",
    '            "qualification": {"productionReady": True, "evidenceId": "ratrig-production-profile-v1"},',
    qualification_summary_pricing,
)

# CP6 fixture uses real canonical receipt/evidence bytes when qualified=True.
replace_once(
    "tests/test_fdm_production_bundle.py",
    "from app.fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION\n",
    "from app.fdm_instance_plate_evidence import INSTANCE_PLATE_EVIDENCE_VERSION\nfrom app.fdm_machine_qualification import validate_machine_qualification_receipt\n",
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    "    profile_hashes = {\"machine\": sha(machine), \"process\": sha(process), \"filament\": sha(filament)}\n    gcode_sha = sha(gcode)\n",
    "    profile_hashes = {\"machine\": sha(machine), \"process\": sha(process), \"filament\": sha(filament)}\n    qualification_evidence_bytes = b\"real physical qualification evidence fixture\\n\"\n    qualification_receipt_bytes = canonical(\n        {\n            \"contractVersion\": \"fdm-machine-qualification/1.0.0\",\n            \"productionReady\": True,\n            \"qualificationId\": \"test-only-qualification-state\",\n            \"protocolId\": \"workpiece-ratrig-qualification-v1\",\n            \"printerKey\": \"ratrig_vcore3_300\",\n            \"request\": {\"material\": \"pla\", \"quality\": \"balanced\", \"strength\": \"functional\"},\n            \"profileSha256\": dict(profile_hashes),\n            \"evidence\": {\n                \"filename\": \"qualification-evidence.txt\",\n                \"mediaType\": \"text/plain\",\n                \"bytes\": len(qualification_evidence_bytes),\n                \"sha256\": sha(qualification_evidence_bytes),\n            },\n            \"review\": {\n                \"status\": \"approved\",\n                \"reviewerId\": \"workpiece-test-reviewer\",\n                \"completedAt\": \"2026-09-06T00:00:00Z\",\n            },\n        }\n    )\n    qualification_summary = validate_machine_qualification_receipt(\n        receipt_bytes=qualification_receipt_bytes,\n        evidence_bytes=qualification_evidence_bytes,\n        expected_printer_key=\"ratrig_vcore3_300\",\n        expected_request={\"material\": \"pla\", \"quality\": \"balanced\", \"strength\": \"functional\"},\n        expected_profile_sha256=profile_hashes,\n    )\n    qualification_summary = {**qualification_summary, \"evidenceId\": qualification_summary[\"qualificationId\"]}\n    gcode_sha = sha(gcode)\n",
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    '            "qualification": {"productionReady": qualified, "evidenceId": "test-only-qualification-state"},',
    '            "qualification": qualification_summary if qualified else {"productionReady": False, "evidenceId": "test-only-qualification-state"},',
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    '        "package_inventory_bytes": package_inventory,\n    }\n',
    '        "package_inventory_bytes": package_inventory,\n        **({\n            "machine_qualification_receipt_bytes": qualification_receipt_bytes,\n            "machine_qualification_evidence_bytes": qualification_evidence_bytes,\n        } if qualified else {}),\n    }\n',
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    '            "evidence/instance-plate.json",\n            "plates/plate-001.gcode",',
    '            "evidence/instance-plate.json",\n            "evidence/machine-qualification-receipt.json",\n            "evidence/machine-qualification/qualification-evidence.txt",\n            "plates/plate-001.gcode",',
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    "    assert first.member_count == 14\n",
    "    assert first.member_count == 16\n",
)
replace_once(
    "tests/test_fdm_production_bundle.py",
    "def test_profile_mutation_fails_closed():\n",
    "def test_machine_qualification_evidence_mutation_fails_closed():\n    fixture = retained_fixture()\n    fixture[\"machine_qualification_evidence_bytes\"] += b\"drift\"\n    with pytest.raises(FdmProductionBundleError, match=\"qualification physical evidence\"):\n        build(fixture)\n\n\ndef test_machine_qualification_receipt_mutation_fails_closed():\n    fixture = retained_fixture()\n    fixture[\"machine_qualification_receipt_bytes\"] += b\" \"\n    with pytest.raises(FdmProductionBundleError, match=\"qualification receipt\"):\n        build(fixture)\n\n\ndef test_profile_mutation_fails_closed():\n",
)

# Production wrapper/API tests get a valid canonical receipt fixture.
replace_once(
    "tests/test_fdm_authority_production.py",
    "import hashlib\n",
    "import hashlib\nimport json\n",
)
replace_once(
    "tests/test_fdm_authority_production.py",
    "def _kwargs(tmp_path: Path) -> dict:\n    source, project, profiles = _paths(tmp_path)\n    return {\n",
    "def _kwargs(tmp_path: Path) -> dict:\n    source, project, profiles = _paths(tmp_path)\n    generation_receipt = _receipt(profiles)\n    profile_hashes = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in profiles.items()}\n    evidence_bytes = b\"real physical qualification evidence fixture\\n\"\n    receipt = {\n        \"contractVersion\": \"fdm-machine-qualification/1.0.0\",\n        \"productionReady\": True,\n        \"qualificationId\": \"qualification-record-123\",\n        \"protocolId\": \"workpiece-ratrig-qualification-v1\",\n        \"printerKey\": \"ratrig_vcore3_300\",\n        \"request\": {\"material\": \"pla\", \"quality\": \"balanced\", \"strength\": \"functional\"},\n        \"profileSha256\": profile_hashes,\n        \"evidence\": {\n            \"filename\": \"qualification-evidence.txt\",\n            \"mediaType\": \"text/plain\",\n            \"bytes\": len(evidence_bytes),\n            \"sha256\": hashlib.sha256(evidence_bytes).hexdigest(),\n        },\n        \"review\": {\"status\": \"approved\", \"reviewerId\": \"workpiece-test-reviewer\", \"completedAt\": \"2026-09-06T00:00:00Z\"},\n    }\n    receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(\",\", \":\")) + \"\\n\").encode()\n    return {\n",
)
replace_once(
    "tests/test_fdm_authority_production.py",
    '        "generation_receipt": _receipt(profiles),\n',
    '        "generation_receipt": generation_receipt,\n',
)
replace_once(
    "tests/test_fdm_authority_production.py",
    '        "base_env": {},\n',
    '        "base_env": {},\n        "machine_qualification_receipt_bytes": receipt_bytes,\n        "machine_qualification_evidence_bytes": evidence_bytes,\n',
)
replace_once(
    "tests/test_fdm_authority_production.py",
    "def test_production_authority_requires_explicit_machine_qualification_before_other_work(tmp_path: Path):\n    values = _kwargs(tmp_path)\n    values[\"machine_qualification_evidence_id\"] = \"   \"\n    with pytest.raises(ValueError, match=\"machine qualification evidence id\"):\n        production.build_fdm_authority_production(**values)\n",
    "def test_production_authority_requires_exact_machine_qualification_receipt_before_other_work(tmp_path: Path):\n    values = _kwargs(tmp_path)\n    values[\"machine_qualification_receipt_bytes\"] = None\n    with pytest.raises(ValueError, match=\"qualification receipt and physical-evidence bytes\"):\n        production.build_fdm_authority_production(**values)\n\n\ndef test_production_authority_rejects_free_form_id_that_disagrees_with_receipt(tmp_path: Path):\n    values = _kwargs(tmp_path)\n    values[\"machine_qualification_evidence_id\"] = \"different-record\"\n    with pytest.raises(ValueError, match=\"differs from the immutable qualification receipt\"):\n        production.build_fdm_authority_production(**values)\n",
)
replace_once(
    "tests/test_fdm_authority_production.py",
    "    assert captured[\"pipeline\"][\"machine_qualification_evidence_id\"] == \"qualification-record-123\"\n",
    "    assert captured[\"pipeline\"][\"machine_qualification_evidence_id\"] == \"qualification-record-123\"\n    assert captured[\"pipeline\"][\"machine_qualification_receipt_bytes\"] == values[\"machine_qualification_receipt_bytes\"]\n    assert captured[\"pipeline\"][\"machine_qualification_evidence_bytes\"] == values[\"machine_qualification_evidence_bytes\"]\n",
)
replace_once(
    "tests/test_fdm_authority_production.py",
    "def test_production_health_rejects_current_unpublished_lock_without_exposing_qualification_value(monkeypatch):\n    monkeypatch.setattr(production_api, \"FDM_TOOLCHAIN_LOCK\", Path(\"fdm-toolchain.lock.json\"))\n    monkeypatch.setattr(production_api, \"WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID\", \"secret-qualification-record\")\n    status = production_api.production_config_status()\n    assert status[\"published_toolchain_lock\"] is False\n    assert status[\"machine_qualification_evidence\"] is True\n    assert \"secret-qualification-record\" not in repr(status)\n",
    "def test_production_health_requires_qualification_receipt_and_evidence_files(monkeypatch, tmp_path: Path):\n    receipt = tmp_path / \"qualification.json\"\n    evidence = tmp_path / \"qualification-evidence.bin\"\n    receipt.write_bytes(b\"receipt\")\n    evidence.write_bytes(b\"evidence\")\n    monkeypatch.setattr(production_api, \"FDM_TOOLCHAIN_LOCK\", Path(\"fdm-toolchain.lock.json\"))\n    monkeypatch.setattr(production_api, \"FDM_MACHINE_QUALIFICATION_RECEIPT\", receipt)\n    monkeypatch.setattr(production_api, \"FDM_MACHINE_QUALIFICATION_EVIDENCE\", evidence)\n    status = production_api.production_config_status()\n    assert status[\"published_toolchain_lock\"] is False\n    assert status[\"machine_qualification_receipt\"] is True\n    assert status[\"machine_qualification_evidence\"] is True\n",
)

# Documentation: make the production gate state explicit.
replace_once(
    "docs/FDM_AUTHORITY_V2_PRODUCTION_API.md",
    "Likewise, the machine qualification evidence ID must come from a real physical\nqualification record. It must never be invented to make software authority pass.\n",
    "Likewise, production machine qualification now requires two exact retained files: a canonical `fdm-machine-qualification/1.0.0` receipt and the physical-evidence artifact hashed by that receipt. The receipt binds the RatRig, material/quality/strength selection, exact machine/process/filament profile SHA-256 values, qualification protocol/review metadata, and evidence SHA/byte count. A free-form evidence ID is not sufficient and must never be invented to make software authority pass.\n",
)
replace_once(
    "docs/FDM_AUTHORITY_V2_PRODUCTION_API.md",
    "- `WORKPIECE_FDM_MACHINE_QUALIFICATION_EVIDENCE_ID`\n",
    "- `FDM_MACHINE_QUALIFICATION_RECEIPT` (path to the exact canonical receipt JSON)\n- `FDM_MACHINE_QUALIFICATION_EVIDENCE` (path to the exact physical-evidence artifact referenced by the receipt)\n",
)

print("machine qualification integration patch applied")
