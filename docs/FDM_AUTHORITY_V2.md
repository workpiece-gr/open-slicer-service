# FDM Authority v2 contract

Status: CP1 contract only. This document does not change the existing FDM production path, enable a new runtime, approve a machine, make pricing authoritative, or bypass human review.

## Purpose

FDM Authority v2 extends Workpiece manufacturing evidence beyond the editable OrcaSlicer 3MF to the exact physical per-plate G-code that will be reviewed for production.

The intended evidence chain is:

`immutable STL -> authoritative FDM configuration -> exact production 3MF -> fresh-process reopen -> exact per-plate G-code -> independent Workpiece G-code validation -> per-plate authority -> production statistics -> deterministic production bundle -> server-authoritative price -> human review -> workshop production`

The existing `fdm-job/1.0.0` website/service path remains valid and unchanged while v2 is developed alongside it.

## Contract versions

CP1 reserves the following independent versions:

- request/manufacturing intent: `fdm-job/2.0.0`
- production evidence manifest: `fdm-production-manifest/2.0.0`
- independent G-code validation receipt: `fdm-gcode-validation/1.0.0`

A v1 request or artifact does not become v2 authority merely because equivalent-looking fields are added around it. The v2 evaluator only grants authority when all v2 evidence relationships are proven.

## Authority state

The evaluator computes authority. A caller-provided string can never self-grant it.

Two CP1 states are defined:

- `evidence_candidate`: evidence exists but one or more production-authority prerequisites are absent, malformed, mismatched, unqualified, or unproven.
- `production_authoritative`: every CP1 production-authority invariant passes.

`production_authoritative` describes technical artifact authority only. It does **not** mean customer payment is authorized, a workshop operator approved the job, a printer has been commanded to print, or a quoted price is server-authoritative.

Human review remains a separate mandatory manufacturing-safety gate.

## Manifest relationships

### `source`

The source is the immutable original STL used to generate the project. It carries at minimum:

- filename;
- retained byte count;
- SHA-256;
- `immutable: true`.

The production 3MF must explicitly bind to this SHA-256.

### `job`

The job carries `fdm-job/2.0.0` and the requested manufacturing intent, including material, quality, strength, quantity, support policy, orientation policy, and arrangement policy.

CP1 makes quantity authority-critical because the physical instance set must reconcile exactly with the requested quantity.

### `machine`

The manifest identifies the selected machine and separately carries machine/profile qualification evidence.

Production authority requires an explicit evidence-backed `productionReady: true` qualification supplied by the machine/profile qualification process. The FDM authority evaluator does not create or infer physical qualification.

A generic or candidate-only printer profile can therefore accumulate useful v2 evidence while remaining `evidence_candidate`.

### `profiles`

Exact machine, process, and filament receipts each carry:

- stable identity;
- SHA-256 of the exact effective profile bytes used for the authoritative project.

The production 3MF, every plate receipt, and every independent G-code validation receipt must refer back to the same three hashes.

### `toolchain`

Production authority requires immutable runtime evidence, not only an OrcaSlicer version string.

The CP1 contract requires:

- OrcaSlicer version;
- SHA-256 of the exact Orca executable/AppImage payload used by the runtime;
- exact service commit;
- digest-pinned execution-environment reference such as `registry/image@sha256:<digest>`;
- matching explicit execution-environment digest.

A mutable tag such as `latest`, `2.4.2`, or a deployment name without an immutable digest is insufficient for production authority.

This requirement is a contract target for CP5. CP1 does not change the current Docker image.

### `project`

The project is the exact retained production `.3mf` generated from the immutable STL.

It carries:

- byte count and SHA-256;
- immutable source SHA-256 relationship;
- exact machine/process/filament profile hashes;
- immutable toolchain reference.

Later slicing must consume this exact retained project. Reconstructing a project from STL during the G-code-authority stage would create a different evidence chain and must fail closed.

### `instances[]`

Every requested physical instance receives a stable Workpiece authority identity and carries at minimum:

- Workpiece instance id;
- project object id/equivalent object relationship;
- physical plate id;
- exact 12-value 3MF build-item transform;
- physical placement bounds in millimetres.

Stable Workpiece identity does not require Orca to expose a globally stable native instance id. CP4 may derive the identity from a validated combination of Orca metadata, project object, transform, plate membership, and deterministic ordering. Whatever mapping is chosen must be provable from the exact retained project.

A count-only test such as `instanceCount === requestedQuantity` is not sufficient.

### `plates[]`

Every non-empty physical plate receives a stable plate id and unique positive plate index.

A plate carries:

- exact instance membership;
- production 3MF SHA-256;
- exact profile hashes;
- immutable toolchain reference;
- exact retained G-code artifact;
- independent validation receipt;
- production statistics with provenance labels.

Every unique physical instance must occur on exactly one plate. Unknown instances, orphan instances, duplicate membership, missing plates, and empty authority plates fail closed.

### `gcode`

Each physical plate must retain the exact `.gcode` bytes produced from the exact retained project and carry byte count plus SHA-256.

A filename or Orca summary without retained and hashed G-code bytes is not production authority.

CP2 will determine and test the exact Orca 2.4.2 plate-to-G-code correspondence rules before this relationship is used in a live authority path.

### `validation`

Independent G-code validation is a Workpiece-owned evidence layer and must not simply trust Orca's result summary.

The CP1 receipt requires:

- `fdm-gcode-validation/1.0.0`;
- Workpiece validator name and version;
- exact validator service commit;
- exact G-code SHA-256;
- exact production 3MF SHA-256;
- exact profile hashes;
- immutable runtime reference;
- `passed: true`;
- `authorityCriticalComplete: true`.

`authorityCriticalComplete` may only be true when CP3 has proved every authority-critical property required by the applicable printer/profile contract. A property that cannot be safely inferred must remain unproven and therefore fail production authority rather than being guessed.

The validator's implementation details are intentionally not defined in CP1.

### `statistics`

Per-plate statistics must include positive filament mass, print time, and layer count plus an explicit evidence-source label for each value.

Source labels matter because a value extracted from an Orca comment in a SHA-bound G-code file is different evidence from a value independently recomputed by Workpiece.

CP1 does not falsely describe Orca-reported print time as an independent kinematic calculation.

### `totals`

Manifest totals must reconcile exactly with the proven plate and instance set:

- physical plate count;
- unique instance count;
- summed filament mass;
- summed print time.

Missing or inconsistent totals fail closed.

### `commercial`

Commercial authority is deliberately independent from manufacturing authority.

Through the early v2 checkpoints the manifest may continue to carry `priceAuthoritative: false` and preview/browser-estimate provenance. CP7 can add server-authoritative pricing without changing the meaning of the technical production evidence.

### `review`

The manifest must explicitly state that human review is required.

The technical evaluator does not turn a pending human review into an approval and does not allow human review to override missing technical authority evidence.

## Production-authority invariants

The v2 evaluator grants `production_authoritative` only when all authority-critical relationships pass, including:

1. correct v2 manifest and job contract versions;
2. valid immutable source receipt;
3. requested quantity is a positive integer;
4. exact machine, process, and filament receipts exist;
5. immutable digest-pinned runtime evidence exists;
6. physical machine/profile qualification is explicitly production-ready and evidence-backed;
7. production 3MF is retained, hashed, and bound to the source, profiles, and runtime;
8. unique physical instance count equals requested quantity;
9. each instance has valid transform, bounds, object relationship, and exactly one plate membership;
10. each plate is uniquely identified and bound to the same project, profiles, and runtime;
11. each plate has exactly one retained hashed G-code artifact;
12. each plate has an identified Workpiece validation receipt bound to that G-code/project/profile/runtime chain;
13. independent validation passes with every authority-critical property proven;
14. every plate has complete production statistics with explicit evidence provenance;
15. aggregate totals reconcile with the complete plate/instance set;
16. human review remains required.

Any failure yields `evidence_candidate`.

## CP1 implementation boundary

`app/fdm_authority.py` implements only the contract evaluator and cross-reference checks above.

CP1 explicitly does **not**:

- call OrcaSlicer;
- modify `/v1/project`;
- alter `fdm-job/1.0.0`;
- enable `verify=true` in the website;
- retain production G-code;
- implement the independent G-code parser;
- change printer routing;
- promote the generic Ender profile;
- alter pricing;
- change approval/checkout/download behavior;
- build a production ZIP;
- publish or deploy a runtime.

## Planned checkpoint mapping

- CP2 will generate and retain exact per-plate G-code from a fresh reopen of the exact retained 3MF and prove plate-to-G-code correspondence.
- CP3 will implement the Workpiece-owned independent G-code validator and define the concrete authority-critical checks per supported machine/profile dialect.
- CP4 will prove stable instance identity, transforms, plate membership, and placement bounds from the exact 3MF rather than relying on quantity alone.
- CP5 will provide the immutable toolchain/runtime evidence required by this contract.
- CP6 will verify retained member hashes/references and create the deterministic production bundle.
- CP7 will make price authority derive from the same authoritative manufacturing output while preserving preview estimates as non-authoritative UX.

Until those checkpoints are integrated and validated, the existing production 3MF path remains the active FDM authority boundary.
