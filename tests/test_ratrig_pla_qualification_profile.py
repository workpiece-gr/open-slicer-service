import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pla_qualification_profile_uses_reviewed_adhesion_and_seam_policy():
    profile = json.loads((ROOT / "profiles" / "process" / "pla.json").read_text(encoding="utf-8"))

    assert profile["brim_type"] == "no_brim"
    assert profile["seam_position"] == "aligned"
    assert profile["elefant_foot_compensation"] == "0.1"
    assert profile["elefant_foot_compensation_layers"] == "2"
