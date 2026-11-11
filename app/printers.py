"""Configured FDM printer list and the single routing rule shared by quoting and projects.

The registry lives in ``profiles/printers.json``. Both ``/v1/slice`` (per-part quote)
and ``/v1/project`` (per-order editable project) call :func:`route_printer`, so a
quoted part and its delivered project resolve to the same printer for the same
inputs. A printer that is ``enabled`` without ``calibrated`` is a configuration
error: the service refuses to start rather than quote from an unmeasured profile.

This module is part of the AGPL-3.0-or-later Open Slicer Service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .fdm_geometry_stability import source_fits_controlled_ratrig_diagonal
from .project_builder import fits_axis_permutation

PRINTERS_SCHEMA = "workpiece-fdm-printers/1"
FIT_RULES = ("axis_permutation", "controlled_ratrig_diagonal")
PROFILE_SOURCES = ("workpiece_profiles", "orca_bundled_creality_generic")
ROUTING_RULES = ("smallest_capable",)
AUTO = "auto"
SELECTION_AUTOMATIC = "automatic_smallest_capable"
SELECTION_REQUESTED = "requested"


class PrinterConfigError(ValueError):
    """``profiles/printers.json`` is missing, malformed or unsafe."""


@dataclass(frozen=True)
class RoutingError(Exception):
    code: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - message formatting only
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class Printer:
    key: str
    label: str
    enabled: bool
    calibrated: bool
    temporary_generic: bool
    envelope_mm: tuple[float, float, float]
    fit_rule: str
    materials: tuple[str, ...]
    profile_source: str
    arrange_rotations: bool
    layout_repair: bool
    note: str = ""

    @property
    def envelope_volume_mm3(self) -> float:
        x, y, z = self.envelope_mm
        return x * y * z

    def fits(self, dimensions_mm: list[float]) -> bool:
        dims = [float(value) for value in dimensions_mm]
        if self.fit_rule == "controlled_ratrig_diagonal":
            return bool(source_fits_controlled_ratrig_diagonal(dims, self.envelope_mm))
        return fits_axis_permutation(dims, self.envelope_mm)

    def public(self) -> dict:
        """Fields safe to expose through ``/health`` and ``/v1/printers`` (no file paths)."""
        return {
            "label": self.label,
            "enabled": self.enabled,
            "calibrated": self.calibrated,
            "temporary_generic": self.temporary_generic,
            "envelope_mm": list(self.envelope_mm),
            "fit_rule": self.fit_rule,
            "materials": list(self.materials),
            "arrange_rotations": self.arrange_rotations,
            "layout_repair": self.layout_repair,
        }


@dataclass(frozen=True)
class PrinterRegistry:
    printers: dict[str, Printer]
    routing_rule: str
    tie_break: tuple[str, ...]
    source_path: Path | None = None

    def enabled(self) -> list[Printer]:
        return [printer for printer in self.printers.values() if printer.enabled]

    def public(self) -> dict:
        return {
            "schema": PRINTERS_SCHEMA,
            "routing": {"rule": self.routing_rule, "tie_break": list(self.tie_break)},
            "printers": {key: printer.public() for key, printer in self.printers.items()},
        }

    def legacy_table(self) -> dict[str, dict]:
        """The ``PROJECT_PRINTERS`` shape older modules import (label/envelope/temporary_generic/materials)."""
        return {
            key: {
                "label": printer.label,
                "envelope_mm": printer.envelope_mm,
                "temporary_generic": printer.temporary_generic,
                "materials": printer.materials,
                "enabled": printer.enabled,
                "calibrated": printer.calibrated,
                "fit_rule": printer.fit_rule,
                "profile_source": printer.profile_source,
                "arrange_rotations": printer.arrange_rotations,
                "layout_repair": printer.layout_repair,
            }
            for key, printer in self.printers.items()
        }


@dataclass(frozen=True)
class RoutingDecision:
    printer: Printer
    selection: str
    candidates: tuple[str, ...]
    rejected: Mapping[str, str] = field(default_factory=dict)


def _bool(record: Mapping, key: str, printer_key: str) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise PrinterConfigError(f"printers.{printer_key}.{key} must be true or false.")
    return value


def _envelope(record: Mapping, printer_key: str) -> tuple[float, float, float]:
    value = record.get("envelope_mm")
    if not isinstance(value, list) or len(value) != 3:
        raise PrinterConfigError(f"printers.{printer_key}.envelope_mm must list three dimensions in mm.")
    dims = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not item > 0:
            raise PrinterConfigError(f"printers.{printer_key}.envelope_mm must contain positive numbers.")
        dims.append(float(item))
    return dims[0], dims[1], dims[2]


def parse_registry(document: Mapping, *, source_path: Path | None = None) -> PrinterRegistry:
    if not isinstance(document, Mapping) or document.get("schema") != PRINTERS_SCHEMA:
        raise PrinterConfigError(f"printers.json must declare schema {PRINTERS_SCHEMA!r}.")
    routing = document.get("routing")
    if not isinstance(routing, Mapping) or routing.get("rule") not in ROUTING_RULES:
        raise PrinterConfigError(f"printers.json routing.rule must be one of: {', '.join(ROUTING_RULES)}.")
    tie_break = routing.get("tie_break", ["envelope_volume_asc", "key_asc"])
    if not isinstance(tie_break, list) or not all(isinstance(item, str) for item in tie_break):
        raise PrinterConfigError("printers.json routing.tie_break must be a list of strings.")
    raw_printers = document.get("printers")
    if not isinstance(raw_printers, Mapping) or not raw_printers:
        raise PrinterConfigError("printers.json must define at least one printer.")

    printers: dict[str, Printer] = {}
    for key, record in raw_printers.items():
        if not isinstance(key, str) or not key or not key.replace("_", "").isalnum() or key != key.lower():
            raise PrinterConfigError("Printer keys must be lowercase alphanumeric/underscore identifiers.")
        if not isinstance(record, Mapping):
            raise PrinterConfigError(f"printers.{key} must be an object.")
        label = record.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PrinterConfigError(f"printers.{key}.label must be a non-empty string.")
        enabled = _bool(record, "enabled", key)
        calibrated = _bool(record, "calibrated", key)
        temporary_generic = _bool(record, "temporary_generic", key)
        if enabled and not calibrated:
            raise PrinterConfigError(
                f"printers.{key} is enabled but not calibrated. A printer without a measured, reviewed profile set "
                "must stay disabled so it never produces a live quote or project."
            )
        fit_rule = record.get("fit_rule")
        if fit_rule not in FIT_RULES:
            raise PrinterConfigError(f"printers.{key}.fit_rule must be one of: {', '.join(FIT_RULES)}.")
        profile_source = record.get("profile_source")
        if profile_source not in PROFILE_SOURCES:
            raise PrinterConfigError(f"printers.{key}.profile_source must be one of: {', '.join(PROFILE_SOURCES)}.")
        materials = record.get("materials")
        if (
            not isinstance(materials, list)
            or not materials
            or not all(isinstance(item, str) and item and item == item.lower() for item in materials)
            or len(set(materials)) != len(materials)
        ):
            raise PrinterConfigError(f"printers.{key}.materials must be a non-empty list of unique lowercase material keys.")
        note = record.get("note", "")
        if not isinstance(note, str):
            raise PrinterConfigError(f"printers.{key}.note must be a string.")
        printers[key] = Printer(
            key=key,
            label=label.strip(),
            enabled=enabled,
            calibrated=calibrated,
            temporary_generic=temporary_generic,
            envelope_mm=_envelope(record, key),
            fit_rule=fit_rule,
            materials=tuple(materials),
            profile_source=profile_source,
            arrange_rotations=_bool(record, "arrange_rotations", key),
            layout_repair=_bool(record, "layout_repair", key),
            note=note,
        )
    return PrinterRegistry(printers=printers, routing_rule=routing["rule"], tie_break=tuple(tie_break), source_path=source_path)


def load_registry(path: Path) -> PrinterRegistry:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PrinterConfigError(f"Cannot read the printer registry {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PrinterConfigError(f"The printer registry {path} is not valid JSON: {exc}") from exc
    return parse_registry(document, source_path=path)


def _sort_key(registry: PrinterRegistry):
    def key(printer: Printer):
        parts: list = []
        for rule in registry.tie_break:
            if rule == "envelope_volume_asc":
                parts.append(printer.envelope_volume_mm3)
            elif rule == "key_asc":
                parts.append(printer.key)
        parts.append(printer.key)
        return tuple(parts)

    return key


def route_printer(
    registry: PrinterRegistry,
    requested: str,
    material: str,
    dimensions_mm: list[float],
) -> RoutingDecision:
    """Pick the printer for one part.

    ``requested`` is ``"auto"`` (smallest enabled printer whose material list and fit
    rule accept the part) or an explicit enabled printer key. Disabled printers are
    never candidates, whatever the request says.
    """

    if requested != AUTO:
        printer = registry.printers.get(requested)
        if printer is None:
            raise RoutingError("PRINTER_UNKNOWN", f"printer must be {AUTO} or one of: {', '.join(registry.printers)}.")
        if not printer.enabled:
            raise RoutingError("PRINTER_DISABLED", f"{requested} is present but disabled until a calibrated profile is enabled.")
        if material not in printer.materials:
            raise RoutingError("MATERIAL_UNSUPPORTED", f"{material.upper()} is not supported by {requested}.")
        if not printer.fits(dimensions_mm):
            raise RoutingError(
                "NO_PRINTER_FITS",
                f"The STL does not fit the configured {requested} envelope, including the controlled RatRig diagonal check where applicable.",
            )
        return RoutingDecision(printer=printer, selection=SELECTION_REQUESTED, candidates=(printer.key,))

    rejected: dict[str, str] = {}
    candidates: list[Printer] = []
    for printer in registry.printers.values():
        if not printer.enabled:
            rejected[printer.key] = "disabled"
            continue
        if material not in printer.materials:
            rejected[printer.key] = "material_unsupported"
            continue
        if not printer.fits(dimensions_mm):
            rejected[printer.key] = "does_not_fit"
            continue
        candidates.append(printer)
    if not candidates:
        enabled_keys = [printer.key for printer in registry.enabled()]
        if not enabled_keys:
            detail = "No printer is enabled in the service configuration."
        else:
            detail = (
                "The STL does not fit any enabled Workpiece FDM printer for this material "
                f"({', '.join(enabled_keys)}), including the controlled RatRig 45-degree rod-fit check."
            )
        raise RoutingError("NO_PRINTER_FITS", detail)
    candidates.sort(key=_sort_key(registry))
    return RoutingDecision(
        printer=candidates[0],
        selection=SELECTION_AUTOMATIC,
        candidates=tuple(printer.key for printer in candidates),
        rejected=rejected,
    )
