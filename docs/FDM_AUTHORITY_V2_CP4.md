# FDM Authority v2 — CP4 instance and plate evidence

CP4 extends the retained FDM authority chain from exact per-plate G-code to explicit physical instance and plate membership evidence.

This checkpoint remains additive. It does not change `/v1/project`, website requests, checkout, workshop approval, printer routing, pricing, or deployment.

## Input authority

CP4 consumes only evidence already produced by earlier checkpoints:

1. exact retained production 3MF bytes and SHA-256;
2. the CP2 generation receipt and exact per-plate G-code bytes;
3. fully passed CP3 validation receipts for every physical plate;
4. the exact machine-profile bytes whose SHA-256 is carried by the CP2/CP3 receipts.

CP4 never invokes OrcaSlicer.

## Stable Workpiece instance identity

OrcaSlicer 2.4.2 cannot currently be trusted to provide a stable native instance identifier for the repaired RatRig project. The deterministic RatRig layout repair writes `instance_id="0"` for multiple physical instances.

CP4 therefore derives a Workpiece instance ID from exact project evidence:

- exact project SHA-256;
- deterministic build-item index;
- project object ID;
- proven physical plate ID;
- exact 12-value build-item transform.

The resulting ID is namespaced as `fdm-inst-<sha256>` and must be unique within the exact production project.

The ID is stable for that exact retained production 3MF. Regenerating a different project is allowed to produce different instance IDs because it is a different manufacturing artifact.

## Physical plate membership proof

RatRig multi-plate projects use Orca's virtual plate grid. Workpiece's deterministic layout repair places build-item transforms into that virtual grid while retaining explicit plate metadata.

CP4 independently recomputes the virtual plate origins from:

- the exact machine printable envelope;
- the exact physical plate count;
- the same documented 20% Orca virtual-grid stride used by the deterministic RatRig repair.

Each transformed build item must fit exactly one physical plate after subtracting one virtual origin. Zero or multiple candidate plates fail closed.

The geometrically assigned instances are then reconciled against `Metadata/model_settings.config`:

- plate IDs must be positive, unique, contiguous, and start at 1;
- every plate must contain at least one `model_instance`;
- `identify_id` values must be present and unique;
- total plate membership must equal the exact 3MF build-item count;
- each plate's multiset of object IDs must agree with the geometrically assigned build items.

The repeated Orca `instance_id` field is intentionally not used as authority evidence.

## G-code instance proof

CP4 requires CP3 to have passed first. It then re-reads the exact retained G-code bytes and extracts the already-validated `EXCLUDE_OBJECT_DEFINE`, `EXCLUDE_OBJECT_START`, and `EXCLUDE_OBJECT_END` annotations.

Every physical project instance must match exactly one G-code object definition on its proven plate. Every G-code object definition must match exactly one project instance.

Matching uses the XY bounds of the exact 3MF instance and the XY bounds of Orca's object-definition polygon.

`geometrySerializationToleranceMm = 0.05` is strictly a software representation tolerance for comparing lower-precision G-code coordinates with higher-precision 3MF transforms. It is not printer calibration, dimensional compensation, clearance, or a manufacturing safety allowance.

For each matched object, CP4 also requires at least one extrusion segment while that object annotation is active. It records:

- object name;
- definition center;
- definition polygon bounds;
- per-object extrusion segment count;
- per-object extrusion bounds;
- exact G-code SHA-256.

Skirts, brims, purge moves, and other global extrusion may exist outside object annotations. CP3 remains responsible for whole-plate command and printable-envelope validation.

## Plate evidence

For every plate CP4 records:

- stable plate ID/index;
- exact project SHA-256;
- exact G-code SHA-256;
- CP3 receipt G-code SHA-256;
- exact instance ID membership;
- exact matched G-code object names.

The project plate set, CP2 G-code artifact plate set, and CP3 validation plate set must be identical.

## Authority state

CP4 output remains `evidence_candidate`.

A successful CP4 receipt does not grant production authority by itself. CP5 still needs to provide immutable/digest-pinned execution-toolchain provenance, and later checkpoints still need the deterministic production bundle and server-authoritative commercial price. Human workshop review remains required.

## Printer scope

CP4 production-instance evidence currently supports only `ratrig_vcore3_300` with `temporary_generic=false`.

`ender3_generic_235` remains explicitly excluded because its current routing profile is temporary and not physically qualified for production authority.

## Real integration gate

The CP4 RatRig workflow creates a thin 132 × 132 × 0.8 mm fixture at quantity 5. The large XY footprint forces deterministic RatRig layout across at least two physical plates while keeping slicing work small.

The workflow proves:

1. exact five-instance production 3MF generation;
2. more than one physical plate;
3. fresh CP2 re-slice of only the exact retained 3MF;
4. CP3 independent validation of every exact plate G-code;
5. CP4 unique Workpiece instance IDs;
6. one-to-one 3MF instance ↔ physical plate membership;
7. one-to-one instance ↔ G-code object-definition matching;
8. positive per-instance extrusion activity;
9. host-side re-hashing of every retained G-code artifact;
10. exactly-once instance membership across the final plate evidence.

## Non-goals

CP4 does not:

- modify source STL handling;
- change orientation, support, arrangement, or printer selection;
- alter any FDM material/printer profile;
- promote the generic Ender route;
- change the website request contract;
- change approval, checkout, download, or workshop behavior;
- pin the Docker/runtime digest (CP5);
- create the deterministic production ZIP/bundle (CP6);
- make price server-authoritative (CP7);
- deploy or change the live production path.
