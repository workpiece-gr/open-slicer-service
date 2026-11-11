import copy
import json
from pathlib import Path

import pytest

from app.printers import (
    AUTO,
    PrinterConfigError,
    RoutingError,
    SELECTION_AUTOMATIC,
    SELECTION_REQUESTED,
    load_registry,
    parse_registry,
    route_printer,
)

REGISTRY_PATH = Path(__file__).parents[1] / "profiles" / "printers.json"


def document() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_committed_registry_has_ratrig_enabled_and_ender_present_but_disabled():
    registry = load_registry(REGISTRY_PATH)
    ratrig = registry.printers["ratrig_vcore3_300"]
    ender = registry.printers["ender3_generic_235"]
    assert ratrig.enabled and ratrig.calibrated and not ratrig.temporary_generic
    assert ratrig.fit_rule == "controlled_ratrig_diagonal" and ratrig.layout_repair and not ratrig.arrange_rotations
    assert not ender.enabled and not ender.calibrated and ender.temporary_generic
    assert ender.envelope_mm == (235.0, 235.0, 235.0)
    assert [printer.key for printer in registry.enabled()] == ["ratrig_vcore3_300"]
    public = registry.public()
    assert public["routing"]["rule"] == "smallest_capable"
    assert "profile_source" not in public["printers"]["ratrig_vcore3_300"]
    legacy = registry.legacy_table()
    assert legacy["ratrig_vcore3_300"]["envelope_mm"] == (300.0, 300.0, 300.0)
    assert legacy["ender3_generic_235"]["temporary_generic"] is True


def test_enabled_without_calibrated_is_refused():
    doc = document()
    doc["printers"]["ender3_generic_235"]["enabled"] = True
    with pytest.raises(PrinterConfigError, match="enabled but not calibrated"):
        parse_registry(doc)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.__setitem__("schema", "other"), "schema"),
        (lambda d: d["routing"].__setitem__("rule", "largest_first"), "routing.rule"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("envelope_mm", [300, 300]), "envelope_mm"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("envelope_mm", [300, 0, 300]), "positive"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("fit_rule", "magic"), "fit_rule"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("materials", []), "materials"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("materials", ["PLA"]), "materials"),
        (lambda d: d["printers"]["ratrig_vcore3_300"].__setitem__("enabled", "yes"), "enabled"),
        (lambda d: d["printers"].__setitem__("Bad-Key", d["printers"]["ratrig_vcore3_300"]), "keys"),
        (lambda d: d.__setitem__("printers", {}), "at least one"),
    ],
)
def test_registry_validation_rejects_malformed_documents(mutate, message):
    doc = document()
    mutate(doc)
    with pytest.raises(PrinterConfigError, match=message):
        parse_registry(doc)


def test_auto_routing_picks_the_only_enabled_printer_when_it_fits():
    registry = load_registry(REGISTRY_PATH)
    decision = route_printer(registry, AUTO, "pla", [20, 20, 20])
    assert decision.printer.key == "ratrig_vcore3_300"
    assert decision.selection == SELECTION_AUTOMATIC
    assert decision.candidates == ("ratrig_vcore3_300",)
    assert decision.rejected == {"ender3_generic_235": "disabled"}


def test_auto_routing_refuses_when_nothing_fits():
    registry = load_registry(REGISTRY_PATH)
    with pytest.raises(RoutingError) as info:
        route_printer(registry, AUTO, "pla", [400, 20, 20])
    assert info.value.code == "NO_PRINTER_FITS"
    assert "ratrig_vcore3_300" in info.value.detail


def test_explicit_requests_are_checked_for_existence_enablement_material_and_fit():
    registry = load_registry(REGISTRY_PATH)
    with pytest.raises(RoutingError) as unknown:
        route_printer(registry, "prusa_mk4", "pla", [20, 20, 20])
    assert unknown.value.code == "PRINTER_UNKNOWN"
    with pytest.raises(RoutingError) as disabled:
        route_printer(registry, "ender3_generic_235", "pla", [20, 20, 20])
    assert disabled.value.code == "PRINTER_DISABLED"
    with pytest.raises(RoutingError) as too_big:
        route_printer(registry, "ratrig_vcore3_300", "pla", [400, 20, 20])
    assert too_big.value.code == "NO_PRINTER_FITS"
    decision = route_printer(registry, "ratrig_vcore3_300", "pla", [20, 20, 20])
    assert decision.selection == SELECTION_REQUESTED


def enabled_ender_registry():
    doc = document()
    doc["printers"]["ender3_generic_235"].update({"enabled": True, "calibrated": True})
    return parse_registry(doc)


def test_smallest_capable_prefers_the_smaller_enabled_printer_and_falls_back_by_fit_and_material():
    registry = enabled_ender_registry()
    small = route_printer(registry, AUTO, "pla", [20, 20, 20])
    assert small.printer.key == "ender3_generic_235"
    assert small.candidates == ("ender3_generic_235", "ratrig_vcore3_300")
    large = route_printer(registry, AUTO, "pla", [250, 20, 20])
    assert large.printer.key == "ratrig_vcore3_300"
    assert large.rejected == {"ender3_generic_235": "does_not_fit"}
    pctg = route_printer(registry, AUTO, "pctg", [20, 20, 20])
    assert pctg.printer.key == "ratrig_vcore3_300"
    assert pctg.rejected == {"ender3_generic_235": "material_unsupported"}
    with pytest.raises(RoutingError) as unsupported:
        route_printer(registry, "ender3_generic_235", "pctg", [20, 20, 20])
    assert unsupported.value.code == "MATERIAL_UNSUPPORTED"


def test_registry_tie_break_falls_back_to_key_order():
    doc = document()
    doc["printers"]["ender3_generic_235"].update({"enabled": True, "calibrated": True, "envelope_mm": [300.0, 300.0, 300.0], "fit_rule": "controlled_ratrig_diagonal"})
    registry = parse_registry(doc)
    decision = route_printer(registry, AUTO, "pla", [20, 20, 20])
    assert decision.printer.key == "ender3_generic_235"
    assert decision.candidates == ("ender3_generic_235", "ratrig_vcore3_300")


def test_parse_registry_does_not_mutate_input():
    doc = document()
    snapshot = copy.deepcopy(doc)
    parse_registry(doc)
    assert doc == snapshot
