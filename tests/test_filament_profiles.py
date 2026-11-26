"""Every material key has a recorded source, and each profile taken from OrcaSlicer says exactly what was changed.

Failure modes: a material offered by the service without a profile or a source record; a printer listing a key the
service cannot slice; a changed temperature or fan value that is not written down; a TPU temperature outside its
data-sheet range; a carbon-fibre variant that cools or flows harder than the Orca carbon-fibre cap it was derived with.
"""
import json
from pathlib import Path

import pytest

from app.main import MATERIALS

ROOT = Path(__file__).parents[1] / "profiles"


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


SOURCES = load("filament-sources.json")
FROM_ORCA = {key: record for key, record in SOURCES["materials"].items() if record["origin"] == "orca"}


def test_sources_materials_and_printer_list_agree():
    assert set(SOURCES["materials"]) == set(MATERIALS)
    assert set(load("printers.json")["printers"]["ratrig_vcore3_300"]["materials"]) == set(MATERIALS)
    catalogue = [record["catalogue_key"] for record in SOURCES["materials"].values()]
    assert sorted(catalogue) == sorted(["PLA", "PETG", "PCTG", "ABS", "TPU-98A", "ASA", "PC", "ABS-CF", "ASA-CF", "PA-CF", "PETG-CF", "TPU-58D", "TPU-85A"])
    for key, record in SOURCES["materials"].items():
        for kind in ("filament", "process"):
            assert MATERIALS[key][kind].relative_to(MATERIALS[key][kind].parents[1]).as_posix() == record[kind]
            assert (ROOT / record[kind]).is_file(), (key, kind)


def test_the_orca_version_is_the_one_the_image_pins():
    dockerfile = (ROOT.parent / "Dockerfile").read_text(encoding="utf-8")
    assert f'ORCA_VERSION={SOURCES["orca"]["version"]}' in dockerfile
    assert len(SOURCES["orca"]["commit"]) == 40


@pytest.mark.parametrize("key", sorted(FROM_ORCA))
def test_recorded_changes_are_what_the_profile_holds(key):
    record, profile = FROM_ORCA[key], load(FROM_ORCA[key]["filament"])
    assert record["files"] and all(path.endswith(".json") for path in record["files"])
    assert profile["compatible_printers"] == [load("machine.json")["name"]]
    assert "inherits" not in profile and profile["filament_diameter"] == ["1.75"]
    for name, change in record["overrides"].items():
        assert profile[name] == [change["service"]], (key, name)
        assert change["orca"] != change["service"] and change["reason"]


@pytest.mark.parametrize("key, nozzle", [("tpu-58d", (230, 260)), ("tpu-85a", (220, 250))])
def test_tpu_temperatures_stay_inside_the_data_sheet_range(key, nozzle):
    profile = load(f"filament/{key}.json")
    for name in ("nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_low", "nozzle_temperature_range_high"):
        assert nozzle[0] <= int(profile[name][0]) <= nozzle[1], name
    for name, value in profile.items():
        if "_plate_temp" in name and int(value[0]) > 0:
            assert 50 <= int(value[0]) <= 90, name


@pytest.mark.parametrize("key", ["abs-cf", "asa-cf"])
def test_carbon_fibre_variants_cap_flow_and_fan_and_keep_the_preset_temperatures(key):
    profile, overrides = load(f"filament/{key}.json"), FROM_ORCA[key]["overrides"]
    assert not any("temp" in name for name in overrides)
    assert float(profile["filament_max_volumetric_speed"][0]) <= 11.5
    assert float(profile["fan_min_speed"][0]) <= 5
    assert float(profile["fan_max_speed"][0]) <= 40
