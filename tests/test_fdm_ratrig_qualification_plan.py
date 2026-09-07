import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
QUALIFICATION_ROOT = ROOT / "qualification" / "ratrig_vcore3_300"
EXPECTED_FIXTURES = {
    "qualification-dimensions-v1.stl": "52f02a382c62e4e659fa7df8df055389f76da89e1265a63fec4ebc6752083bbb",
    "qualification-holes-v1.stl": "ce1237ea41ac74feee93b8e4b5c811f2b329de0f816ec238d89e4847b47a74ec",
    "qualification-bridge-support-v1.stl": "cb4a9639617841e08647c634d62417513d60d39eb12a7a2ff850dcaab261d56a",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ratrig_qualification_fixtures_are_deterministic(tmp_path):
    generator = QUALIFICATION_ROOT / "generate_fixtures.py"
    subprocess.run(
        [sys.executable, str(generator), "--output-dir", str(tmp_path)],
        check=True,
        cwd=ROOT,
    )
    generated = {path.name: sha256(path) for path in tmp_path.glob("*.stl")}
    assert generated == EXPECTED_FIXTURES
    for name in EXPECTED_FIXTURES:
        payload = (tmp_path / name).read_text(encoding="ascii")
        assert payload.startswith("solid workpiece_ratrig_")
        assert payload.rstrip().endswith(tuple([
            "endsolid workpiece_ratrig_dimensions_v1",
            "endsolid workpiece_ratrig_holes_v1",
            "endsolid workpiece_ratrig_bridge_support_v1",
        ]))


def test_evidence_template_is_explicitly_not_evidence():
    template = json.loads((QUALIFICATION_ROOT / "qualification-evidence.template.json").read_text(encoding="utf-8"))
    assert template["schema"] == "workpiece-fdm-qualification-evidence-template-v1"
    assert template["status"] == "template_not_evidence"
    assert template["protocolId"] == "workpiece-ratrig-vcore3-300-qualification-v1"
    assert template["printer"]["printerKey"] == "ratrig_vcore3_300"
    assert template["request"] == {"material": "pla", "quality": "balanced", "strength": "functional"}
    assert template["acceptanceCriteria"]["frozenBeforeRun"] is False
    assert template["runs"] == []
    assert template["review"]["status"] == "pending"
    assert {fixture["filename"]: fixture["sha256"] for fixture in template["fixtures"]} == EXPECTED_FIXTURES


def test_receipt_template_cannot_grant_production_authority():
    receipt = json.loads((QUALIFICATION_ROOT / "qualification-receipt.template.json").read_text(encoding="utf-8"))
    assert receipt["contractVersion"] == "fdm-machine-qualification/1.0.0"
    assert receipt["productionReady"] is False
    assert receipt["qualificationId"] == ""
    assert receipt["printerKey"] == "ratrig_vcore3_300"
    assert receipt["review"]["status"] == "pending"
    assert receipt["evidence"]["bytes"] == 0
    assert receipt["evidence"]["sha256"] == ""


def test_qualification_plan_preserves_human_and_exact_combo_gates():
    plan = (ROOT / "docs" / "FDM_RATRIG_QUALIFICATION_PLAN.md").read_text(encoding="utf-8")
    assert "qualification preparation only" in plan
    assert "One qualification receipt covers **one exact**" in plan
    assert "acceptanceCriteria.frozenBeforeRun" in plan
    assert "The G-code physically printed must be the exact retained G-code" in plan
    assert "every customer order still requires its separate human manufacturing review" in plan
