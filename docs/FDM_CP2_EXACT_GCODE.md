# FDM Authority v2 CP2 — exact production G-code

Status: development checkpoint; not deployed and not part of the current Workpiece production path.

CP2 extends the CP1 evidence contract from the retained production 3MF to the exact per-plate G-code bytes emitted by a fresh OrcaSlicer process.  It does **not** make those G-code files production-authoritative by itself; CP3 independent validation is still required.

## Exact downstream-artifact rule

The CP2 slicer input is the exact retained/generated production 3MF.  CP2 must not rebuild the project from STL, reload a different machine/process/filament recipe, re-run orientation, or re-run arrangement before slicing.

The required sequence is:

1. retain/hash the production 3MF;
2. inspect its physical plate metadata;
3. start a fresh OrcaSlicer process with clean XDG runtime state;
4. open that exact 3MF without external profile overrides;
5. slice all physical plates;
6. identify each emitted G-code by Orca's physical plate identifier, not discovery order;
7. retain the exact G-code bytes that were hashed;
8. bind each G-code receipt back to the production 3MF SHA-256 and its physical plate id;
9. fail closed on any missing, duplicate, unexpected, empty, or ambiguously named output.

## Plate identity rule

For the pinned OrcaSlicer 2.4.2 toolchain, CP2 is testing the CLI convention that slicing all plates emits canonical files named:

- `plate_1.gcode`
- `plate_2.gcode`
- ...

The numeric suffix is accepted only when the exact set matches the `plater_id` values parsed from `Metadata/model_settings.config` in the retained production 3MF.  Sorting arbitrary filenames and assigning plate numbers afterward is explicitly not authority evidence.

The branch-only `FDM v2 CP2 Orca plate probe` workflow exists to prove this relationship against the real pinned Orca 2.4.2 container before the rule is wired into the service.  The temporary workflow must be removed before CP2 is merged.

## Exact G-code artifact receipt

The CP2 collector retains, for each plate:

- physical `plate_id`;
- canonical Orca filename;
- exact G-code bytes;
- exact byte count;
- SHA-256 of those exact bytes;
- SHA-256 of the retained production 3MF from which they were sliced.

Later CP2 wiring will additionally carry the existing machine/process/filament and runtime evidence alongside these receipts.

## Fresh-runtime rule

A new process alone is not enough.  The downstream slice must use a separate clean XDG config/cache/data root from project generation.  No external `--load-settings` or `--load-filaments` argument may be supplied while reopening the retained 3MF.

This proves the downstream slice is driven by the retained project rather than job-local Orca state created during project generation.

## Fail-closed conditions

CP2 must reject the exact-G-code result when any of these occur:

- the production 3MF has no physical plate metadata;
- a `plater_id` is malformed or duplicated;
- a declared physical plate has no proven instance;
- fresh Orca produces no G-code;
- a G-code filename does not carry the canonical physical plate id;
- a G-code refers to a plate absent from the 3MF;
- an expected 3MF plate has no G-code;
- more than one G-code maps to one physical plate;
- any retained G-code is empty;
- the production-project digest used for the receipt is invalid;
- exact returned/persisted bytes do not match their receipt hash.

## Authority boundary after CP2

A successful CP2 result means:

`immutable STL -> exact production 3MF -> fresh exact-project slice -> exact per-plate G-code bytes + hashes`

The result remains `evidence_candidate`.  CP3 must independently inspect each exact G-code and produce the validation receipts required by `fdm-production-manifest/2.0.0` before FDM v2 may claim `production_authoritative`.

## Explicit non-goals

CP2 does not:

- replace the current `verify=false` binary-3MF website request;
- change pricing authority;
- deploy a new service runtime;
- qualify the temporary generic Ender profile;
- independently validate G-code safety or semantics;
- send G-code to a printer;
- change resin readiness.
