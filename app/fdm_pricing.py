"""Server-authoritative FDM pricing for Authority v2 CP7.

CP7 prices exact retained manufacturing evidence. It does not call OrcaSlicer,
change the production endpoint, apply the cart-level order minimum, qualify a
printer, publish the CP5 runtime, or bypass human review.

The commercial price can be authoritative while manufacturing remains an
``evidence_candidate`` only when the remaining manufacturing blockers are the
separately controlled immutable-toolchain publication and/or physical machine
qualification gates. Any source/project/profile/G-code/validator/plate/statistics
failure blocks pricing.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from .fdm_authority import AUTHORITY_PRODUCTION, evaluate_fdm_authority

FDM_PRICING_CONTRACT_VERSION = "fdm-pricing/1.0.0"
FDM_PRICING_POLICY_VERSION = "workpiece-fdm-pricing-policy/1.0.0"

# Mirrors the current Workpiece browser commercial policy at the start of CP7.
# CP7 intentionally does NOT carry the browser-only manual-review complexity
# multiplier into authority. Support pricing is derived from exact G-code roles.
FDM_PRICING_POLICY = {
    "contractVersion": FDM_PRICING_POLICY_VERSION,
    "currency": "EUR",
    "materialCostEurPerKg": {
        "pla": "24",
        "petg": "26",
        "pctg": "32",
        "abs": "26",
        "tpu": "38",
    },
    "materialMarkup": "2.4",
    "averagePowerKw": "0.15",
    "electricityEurPerKwh": "0.20",
    "machineEurPerHour": "4.5",
    "setupEurPerItem": "4.5",
    "handlingEurPerPart": "1.5",
    "supportHandlingEurPerPart": "2.0",
    "failureAllowance": "0.10",
    "supportMultiplier": "1.18",
    "quantityDiscounts": [
        {"minimumQuantity": 20, "factor": "0.88"},
        {"minimumQuantity": 10, "factor": "0.92"},
        {"minimumQuantity": 5, "factor": "0.95"},
        {"minimumQuantity": 2, "factor": "0.98"},
        {"minimumQuantity": 1, "factor": "1"},
    ],
    # This is a cart policy owned by checkout. An individual CP7 FDM item must
    # never apply it independently.
    "minimumOrderEur": "20",
    "orderMinimumScope": "whole_cart",
    "manualReviewMultiplierAuthority": "excluded_browser_heuristic",
}

_ALLOWED_MANUFACTURING_CANDIDATE_ISSUES = frozenset(
    {"missing_immutable_toolchain", "machine_not_production_ready"}
)
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SUPPORT_ROLE_RE = re.compile(r"support", re.IGNORECASE)
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class FdmPricingError(ValueError):
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


def _decimal(value: Any, label: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise FdmPricingError(f"{label} must be numeric.")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # Decimal raises several conversion exceptions.
        raise FdmPricingError(f"{label} must be numeric.") from exc
    if not result.is_finite():
        raise FdmPricingError(f"{label} must be finite.")
    if positive and result <= 0:
        raise FdmPricingError(f"{label} must be greater than zero.")
    if non_negative and result < 0:
        raise FdmPricingError(f"{label} must not be negative.")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FdmPricingError(f"{label} must be a positive integer.")
    return value


def _eur(value: Decimal) -> str:
    # Six decimal places are more than sufficient for the current policy while
    # keeping receipts stable and human-auditable. Final authority is integer cents.
    return format(value.quantize(Decimal("0.000001")), "f")


def _cents(value: Decimal) -> int:
    # JavaScript Math.round is half-up for positive prices. Workpiece prices are
    # non-negative, so ROUND_HALF_UP preserves current browser final-cent behavior.
    return int((value * _HUNDRED).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _quantity_discount(quantity: int) -> Decimal:
    for tier in FDM_PRICING_POLICY["quantityDiscounts"]:
        if quantity >= int(tier["minimumQuantity"]):
            return Decimal(str(tier["factor"]))
    raise AssertionError("Pricing policy must contain a quantity-1 tier.")


def _validate_manufacturing_evidence(manifest: Mapping[str, Any]) -> tuple[str, list[str]]:
    evaluation = evaluate_fdm_authority(manifest)
    issue_codes = sorted({issue.code for issue in evaluation.issues})
    unexpected = set(issue_codes) - _ALLOWED_MANUFACTURING_CANDIDATE_ISSUES
    if unexpected:
        raise FdmPricingError(
            "Authoritative FDM pricing requires complete exact manufacturing evidence; "
            f"unresolved={sorted(unexpected)}."
        )
    return evaluation.state, issue_codes


def _command_and_args(code: str) -> tuple[str, list[str]]:
    tokens = code.strip().split()
    return (tokens[0].upper(), tokens[1:]) if tokens else ("", [])


def _e_value(args: list[str]) -> Decimal | None:
    for token in args:
        if len(token) > 1 and token[0].upper() == "E":
            try:
                value = Decimal(token[1:])
            except Exception as exc:
                raise FdmPricingError(f"Unreadable extrusion value in exact G-code token {token!r}.") from exc
            if not value.is_finite():
                raise FdmPricingError("Exact G-code contains a non-finite extrusion value.")
            return value
    return None


def inspect_exact_gcode_support(gcode_bytes: bytes) -> dict[str, Any]:
    """Return support-use evidence from exact G-code bytes.

    Merely enabling support settings is not evidence that support material was
    printed. A support surcharge requires at least one positive extrusion move
    while Orca's current ``;TYPE:...`` role is a support role.
    """

    if not isinstance(gcode_bytes, bytes) or not gcode_bytes:
        raise FdmPricingError("Support inspection requires exact non-empty G-code bytes.")
    try:
        text = gcode_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FdmPricingError("Exact G-code must be valid UTF-8 for CP7 support inspection.") from exc

    extrusion_mode: str | None = None
    absolute_e = _ZERO
    current_role = ""
    support_roles: set[str] = set()
    observed_roles: set[str] = set()
    support_segments = 0
    total_extrusion_segments = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(";TYPE:"):
            current_role = line.partition(":")[2].strip()
            if current_role:
                observed_roles.add(current_role)
                if _SUPPORT_ROLE_RE.search(current_role):
                    support_roles.add(current_role)
            continue
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        command, args = _command_and_args(code)
        if command == "M82":
            extrusion_mode = "absolute"
            continue
        if command == "M83":
            extrusion_mode = "relative"
            continue
        if command == "G92":
            e = _e_value(args)
            if e is not None:
                absolute_e = e
            continue
        if command not in {"G0", "G1"}:
            continue
        e = _e_value(args)
        if e is None:
            continue
        if extrusion_mode is None:
            raise FdmPricingError("Exact G-code extrudes before declaring M82/M83 extrusion mode.")
        if extrusion_mode == "relative":
            delta = e
        else:
            delta = e - absolute_e
            absolute_e = e
        if delta <= 0:
            continue
        total_extrusion_segments += 1
        if current_role and _SUPPORT_ROLE_RE.search(current_role):
            support_segments += 1
            support_roles.add(current_role)

    return {
        "used": support_segments > 0,
        "supportExtrusionSegmentCount": support_segments,
        "totalExtrusionSegmentCount": total_extrusion_segments,
        "supportRoles": sorted(support_roles),
        "observedRoles": sorted(observed_roles),
        "source": "exact_hashed_gcode_type_roles_with_positive_extrusion",
    }


def _support_evidence(
    manifest: Mapping[str, Any],
    gcode_bytes_by_plate: Mapping[str, bytes] | Any,
) -> dict[str, Any]:
    if not isinstance(gcode_bytes_by_plate, Mapping):
        raise FdmPricingError("CP7 requires exact per-plate G-code bytes.")
    supplied: dict[str, bytes] = {}
    for raw_key, payload in gcode_bytes_by_plate.items():
        key = str(raw_key).strip()
        if not key or key in supplied or not isinstance(payload, bytes) or not payload:
            raise FdmPricingError("CP7 G-code mapping contains a duplicate, empty, or malformed artifact.")
        supplied[key] = payload

    plates = manifest.get("plates")
    if not isinstance(plates, list) or not plates:
        raise FdmPricingError("CP7 requires at least one physical plate receipt.")
    used_keys: set[str] = set()
    plate_evidence: list[dict[str, Any]] = []
    support_plate_ids: list[str] = []
    total_support_segments = 0
    total_extrusion_segments = 0

    for raw_plate in plates:
        plate = _record(raw_plate)
        plate_id = _text(plate.get("id"))
        if not plate_id or plate_id in used_keys:
            raise FdmPricingError("CP7 plate ids must be non-empty and unique.")
        payload = supplied.get(plate_id)
        if payload is None:
            raise FdmPricingError(f"CP7 is missing exact G-code bytes for plate {plate_id}.")
        gcode = _record(plate.get("gcode"))
        expected_sha = _sha(gcode.get("sha256"))
        if not expected_sha or _digest(payload) != expected_sha:
            raise FdmPricingError(f"Plate {plate_id} exact G-code bytes do not match the retained SHA-256 receipt.")
        expected_bytes = gcode.get("bytes")
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 1 or len(payload) != expected_bytes:
            raise FdmPricingError(f"Plate {plate_id} exact G-code byte count does not match the retained receipt.")
        validation = _record(plate.get("validation"))
        if validation.get("passed") is not True or validation.get("authorityCriticalComplete") is not True:
            raise FdmPricingError(f"Plate {plate_id} lacks a complete passing CP3 validation receipt.")
        if _sha(validation.get("gcodeSha256")) != expected_sha:
            raise FdmPricingError(f"Plate {plate_id} CP3 validation is not bound to the exact G-code bytes.")

        evidence = inspect_exact_gcode_support(payload)
        evidence = {"plateId": plate_id, "gcodeSha256": expected_sha, **evidence}
        plate_evidence.append(evidence)
        if evidence["used"]:
            support_plate_ids.append(plate_id)
        total_support_segments += int(evidence["supportExtrusionSegmentCount"])
        total_extrusion_segments += int(evidence["totalExtrusionSegmentCount"])
        used_keys.add(plate_id)

    if set(supplied) != used_keys:
        raise FdmPricingError("CP7 received exact G-code bytes for a plate not present in the manufacturing manifest.")
    return {
        "used": bool(support_plate_ids),
        "plateIds": support_plate_ids,
        "supportExtrusionSegmentCount": total_support_segments,
        "totalExtrusionSegmentCount": total_extrusion_segments,
        "source": "exact_hashed_gcode_type_roles_with_positive_extrusion",
        "plates": plate_evidence,
    }


def price_exact_fdm_job(
    *,
    manifest: Mapping[str, Any] | Any,
    gcode_bytes_by_plate: Mapping[str, bytes] | Any,
) -> dict[str, Any]:
    """Calculate one authoritative FDM item subtotal from exact job evidence."""

    if not isinstance(manifest, Mapping):
        raise FdmPricingError("CP7 requires an FDM production manifest object.")
    manufacturing_state, manufacturing_issues = _validate_manufacturing_evidence(manifest)

    request = _record(_record(manifest.get("job")).get("request"))
    material = _text(request.get("material")).lower()
    material_cost_text = FDM_PRICING_POLICY["materialCostEurPerKg"].get(material)
    if material_cost_text is None:
        raise FdmPricingError(f"No authoritative FDM pricing policy exists for material {material!r}.")
    quantity = _positive_int(request.get("quantity"), "FDM quantity")

    totals = _record(manifest.get("totals"))
    instance_count = _positive_int(totals.get("instanceCount"), "Manifest instance count")
    if instance_count != quantity:
        raise FdmPricingError("Pricing quantity must equal the exact manufacturing instance count.")
    plate_count = _positive_int(totals.get("plateCount"), "Manifest plate count")
    plates = manifest.get("plates")
    if not isinstance(plates, list) or len(plates) != plate_count:
        raise FdmPricingError("Pricing plate count must equal the exact physical plate receipts.")
    filament_grams = _decimal(totals.get("filamentGrams"), "Exact total filament grams", positive=True)
    print_seconds = _positive_int(totals.get("printTimeSeconds"), "Exact total print time seconds")

    # Independently require the plate statistics to reconcile with the exact job
    # totals before commercial math uses them.
    plate_filament = _ZERO
    plate_seconds = 0
    for raw_plate in plates:
        stats = _record(_record(raw_plate).get("statistics"))
        plate_filament += _decimal(stats.get("filamentGrams"), "Plate filament grams", positive=True)
        plate_seconds += _positive_int(stats.get("printTimeSeconds"), "Plate print time seconds")
    if abs(plate_filament - filament_grams) > Decimal("0.000001"):
        raise FdmPricingError("Exact per-plate filament totals do not reconcile with the production manifest.")
    if plate_seconds != print_seconds:
        raise FdmPricingError("Exact per-plate print-time totals do not reconcile with the production manifest.")

    support = _support_evidence(manifest, gcode_bytes_by_plate)

    material_cost_per_kg = Decimal(str(material_cost_text))
    material_markup = Decimal(str(FDM_PRICING_POLICY["materialMarkup"]))
    average_power_kw = Decimal(str(FDM_PRICING_POLICY["averagePowerKw"]))
    electricity_per_kwh = Decimal(str(FDM_PRICING_POLICY["electricityEurPerKwh"]))
    machine_per_hour = Decimal(str(FDM_PRICING_POLICY["machineEurPerHour"]))
    setup_eur = Decimal(str(FDM_PRICING_POLICY["setupEurPerItem"]))
    handling_per_part = Decimal(str(FDM_PRICING_POLICY["handlingEurPerPart"]))
    support_handling_per_part = Decimal(str(FDM_PRICING_POLICY["supportHandlingEurPerPart"]))
    failure_allowance = Decimal(str(FDM_PRICING_POLICY["failureAllowance"]))
    support_multiplier = Decimal(str(FDM_PRICING_POLICY["supportMultiplier"]))

    hours = Decimal(print_seconds) / Decimal(3600)
    material_eur = filament_grams / Decimal(1000) * material_cost_per_kg * material_markup
    energy_eur = hours * average_power_kw * electricity_per_kwh
    machine_eur = hours * machine_per_hour
    manufacturing_base_eur = material_eur + energy_eur + machine_eur
    risk_multiplier = (Decimal(1) + failure_allowance) * (support_multiplier if support["used"] else Decimal(1))
    risk_uplift_eur = manufacturing_base_eur * (risk_multiplier - Decimal(1))
    handling_eur = Decimal(quantity) * handling_per_part
    support_handling_eur = Decimal(quantity) * support_handling_per_part if support["used"] else _ZERO
    gross_before_discount_eur = manufacturing_base_eur + risk_uplift_eur + handling_eur + support_handling_eur
    discount_factor = _quantity_discount(quantity)
    discount_eur = gross_before_discount_eur * (Decimal(1) - discount_factor)
    discounted_variable_eur = gross_before_discount_eur * discount_factor
    item_subtotal_eur = discounted_variable_eur + setup_eur
    if item_subtotal_eur <= 0:
        raise FdmPricingError("Authoritative FDM item subtotal must be positive.")
    item_subtotal_cents = _cents(item_subtotal_eur)

    component_eur = {
        "material": material_eur,
        "energy": energy_eur,
        "machineTime": machine_eur,
        "riskUplift": risk_uplift_eur,
        "handling": handling_eur,
        "supportHandling": support_handling_eur,
        "discount": -discount_eur,
        "setup": setup_eur,
    }
    component_cents = {name: _cents(value) if value >= 0 else -_cents(-value) for name, value in component_eur.items()}
    rounding_adjustment_cents = item_subtotal_cents - sum(component_cents.values())

    policy_sha = _digest(_canonical_json(FDM_PRICING_POLICY))
    evidence_binding = {
        "sourceSha256": _sha(_record(manifest.get("source")).get("sha256")),
        "projectSha256": _sha(_record(manifest.get("project")).get("sha256")),
        "profileSha256": {
            kind: _sha(_record(_record(manifest.get("profiles")).get(kind)).get("sha256"))
            for kind in ("machine", "process", "filament")
        },
        "plateGcodeSha256": [
            {"plateId": _text(_record(plate).get("id")), "sha256": _sha(_record(_record(plate).get("gcode")).get("sha256"))}
            for plate in plates
        ],
    }

    receipt = {
        "contractVersion": FDM_PRICING_CONTRACT_VERSION,
        "policyVersion": FDM_PRICING_POLICY_VERSION,
        "policySha256": policy_sha,
        "currency": "EUR",
        "priceAuthoritative": True,
        "authority": "server_exact_manufacturing_evidence",
        "manufacturingAuthorityState": manufacturing_state,
        "manufacturingAuthorityIssues": manufacturing_issues,
        "productionOrderEligible": manufacturing_state == AUTHORITY_PRODUCTION,
        "material": material,
        "quantity": quantity,
        "exactStatistics": {
            "filamentGrams": _eur(filament_grams),
            "printTimeSeconds": print_seconds,
            "printHours": _eur(hours),
            "plateCount": plate_count,
            "instanceCount": instance_count,
            "source": "production_manifest_totals_reconciled_to_per_plate_hashed_gcode_statistics",
        },
        "support": support,
        "policy": {
            **FDM_PRICING_POLICY,
            "quantityDiscountFactor": format(discount_factor, "f"),
            "orderMinimumApplied": False,
            "manualReviewMultiplierApplied": False,
        },
        "breakdownEur": {name: _eur(value) for name, value in component_eur.items()},
        "breakdownCents": {
            **component_cents,
            "roundingAdjustment": rounding_adjustment_cents,
            "itemSubtotalBeforeOrderMinimum": item_subtotal_cents,
        },
        "authoritativePriceCents": item_subtotal_cents,
        "orderMinimum": {
            "minimumOrderCents": _cents(Decimal(str(FDM_PRICING_POLICY["minimumOrderEur"]))),
            "applied": False,
            "scope": "whole_cart_checkout",
        },
        "evidenceBinding": evidence_binding,
    }
    receipt["pricingReceiptSha256"] = _digest(_canonical_json(receipt))
    return receipt
