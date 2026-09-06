# FDM Authority v2 — CP7 server-authoritative pricing

CP7 makes the FDM **item manufacturing price** derive from the same exact retained manufacturing evidence used by the FDM Authority v2 chain.

The pricing stage is downstream of the technical evidence chain:

`immutable STL -> exact production 3MF -> exact per-plate G-code -> CP3 validation -> CP4 instance/plate evidence -> CP5 runtime provenance -> CP6 deterministic production bundle -> CP7 server-authoritative item price`

CP7 does not call OrcaSlicer itself. It consumes an already reconciled production manifest plus the exact retained per-plate G-code bytes.

## Authority boundary

`app/fdm_pricing.py` returns a deterministic `fdm-pricing/1.0.0` receipt with:

- `priceAuthoritative: true`;
- the server pricing policy version and SHA-256;
- exact source/project/profile/G-code evidence bindings;
- exact filament mass and print time used for pricing;
- exact support-use evidence from the retained G-code;
- quantity and quantity-discount factor;
- a cents-level commercial breakdown;
- the authoritative FDM item subtotal before the cart-level minimum;
- a deterministic pricing-receipt SHA-256.

Commercial authority remains separate from technical production authority. A price may be authoritative while a job remains `evidence_candidate` only when the unresolved production blockers are the separately controlled:

- immutable CP5 runtime publication gate; and/or
- physical machine/profile qualification gate.

Any source, project, profile, G-code, validator, plate membership, quantity, or statistics failure blocks authoritative pricing.

## Exact statistics rule

CP7 does not price browser geometry estimates.

The commercial calculation uses the production manifest's exact job totals only after requiring:

- requested quantity equals the exact manufacturing instance count;
- physical plate count equals the retained plate receipts;
- every plate carries positive filament mass and print time;
- per-plate filament mass sums to the manifest total;
- per-plate print time sums to the manifest total;
- every exact G-code payload matches its retained byte count and SHA-256;
- every G-code has a complete passing CP3 validation receipt bound to that exact hash.

The totals already represent the complete multi-instance manufacturing job. CP7 therefore **does not multiply filament mass or print time by quantity again**.

## Support-pricing rule

The current production process intentionally enables automatic supports for FDM projects. That configuration alone is not evidence that support material was actually printed.

CP7 does not surcharge merely because `enable_support=1`, `support_type=...`, or a support-role comment exists.

A support surcharge is applied only when the exact retained G-code:

1. enters an Orca `;TYPE:` role whose name is a support role; and
2. performs a positive extrusion move while that support role is active.

The receipt records support plate IDs, support extrusion-segment count, observed support roles, and exact G-code SHA-256 bindings.

The dedicated CP7 integration workflow proves both sides with real OrcaSlicer 2.4.2 artifacts:

- a support-free model sliced with automatic supports enabled must price with `support.used=false`;
- a support-forcing overhang fixture must contain real support extrusion and price with `support.used=true`.

## Commercial policy baseline

`workpiece-fdm-pricing-policy/1.0.0` intentionally mirrors the FDM commercial inputs used by the Workpiece browser quote model at the start of CP7, except for browser-only heuristics that cannot be manufacturing authority.

Current server policy:

| Input | CP7 value |
| --- | ---: |
| PLA input cost | €24/kg |
| PETG input cost | €26/kg |
| PCTG input cost | €32/kg |
| ABS input cost | €26/kg |
| TPU input cost | €38/kg |
| Material markup | 2.40× |
| FDM machine rate | €4.50/hour |
| Average power | 0.15 kW |
| Electricity | €0.20/kWh |
| FDM setup | €4.50/item |
| Handling | €1.50/part |
| Failure allowance | 10% |
| Support risk multiplier | 1.18× |
| Support handling | €2.00/part |
| Quantity 2+ | 0.98× |
| Quantity 5+ | 0.95× |
| Quantity 10+ | 0.92× |
| Quantity 20+ | 0.88× |

The former browser `complexity="review"` multiplier is **not** copied into authority. It is a browser/manual-review heuristic rather than a property proved by the exact manufacturing artifacts. A job with unresolved authority-critical manufacturing evidence fails authoritative pricing instead of silently receiving that multiplier.

## Order minimum

The €20 minimum is a **whole-cart checkout policy**, not an independent minimum for every FDM line item.

CP7 therefore returns:

- the authoritative FDM item subtotal before the order minimum; and
- `orderMinimum.applied: false`, `scope: "whole_cart_checkout"`.

Checkout remains responsible for applying any cart-level minimum after combining eligible line items.

## Rounding and determinism

Commercial calculations use Python `Decimal`. Intermediate values are not rounded to cents. The final positive item price is rounded half-up to integer euro cents, matching the current positive-price `Math.round` behavior in the browser model.

The pricing policy is canonical-JSON hashed. The final receipt is also canonical-JSON hashed after all evidence and commercial fields have been assembled.

Identical manufacturing evidence and policy therefore produce an identical pricing receipt.

## CP7 does not

CP7 does **not**:

- change `/v1/project`;
- replace the current website/browser FDM request path;
- deploy or publish an authority image;
- publish the CP5 toolchain/runtime lock;
- mark an unqualified printer production-ready;
- promote the temporary generic Ender profile;
- apply the €20 cart minimum per item;
- use the browser `complexity` heuristic as authority;
- bypass human workshop review;
- change approval, checkout, download, or workshop behavior;
- change resin authority or resin physical readiness.

Website/checkout consumption of the CP7 receipt is a separate integration step and must preserve the fail-closed production and human-review gates.
