import pytest

from app.fdm_production_bundle import FdmProductionBundleError, _resolve_plate_gcode


def test_distinct_plate_id_and_index_aliases_cannot_both_be_supplied():
    with pytest.raises(FdmProductionBundleError, match="ambiguous G-code aliases"):
        _resolve_plate_gcode(
            {"plate-a": b"id-payload", "1": b"index-payload"},
            plate_id="plate-a",
            plate_index=1,
        )


def test_numeric_plate_id_equal_to_index_is_one_unambiguous_key():
    payload, key = _resolve_plate_gcode({"1": b"exact-gcode"}, plate_id="1", plate_index=1)
    assert key == "1"
    assert payload == b"exact-gcode"


def test_plate_can_use_index_alias_when_stable_id_is_not_transport_key():
    payload, key = _resolve_plate_gcode({"7": b"exact-gcode"}, plate_id="plate-seven", plate_index=7)
    assert key == "7"
    assert payload == b"exact-gcode"
