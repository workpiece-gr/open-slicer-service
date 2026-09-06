# FDM Authority v2 CP6 — deterministic production bundle

Status: implementation checkpoint. CP6 does not publish the CP5 runtime, qualify a printer, change `/v1/project`, alter website behavior, make pricing authoritative, remove human review, or deploy a production path.

## Purpose

CP6 turns the already-proven retained FDM evidence chain into one deterministic workshop/archive artifact without reconstructing manufacturing inputs.

The bundle stage is downstream of the authority evidence chain:

`immutable STL -> exact production 3MF -> exact per-plate G-code -> CP3 validation -> CP4 instance/plate evidence -> CP5 immutable runtime provenance -> CP6 deterministic bundle`

The builder never calls OrcaSlicer. It packages the exact retained bytes that earlier checkpoints already hashed.

## Contract

The bundle contract is `fdm-production-bundle/1.0.0` with layout `workpiece-fdm-production-bundle-v1`.

`build_fdm_production_bundle()` requires:

- the FDM v2 production manifest;
- exact immutable source STL bytes;
- exact retained production 3MF bytes;
- exact effective machine, process, and filament profile bytes;
- exact retained G-code bytes for every physical plate;
- exact CP5 toolchain lock bytes;
- exact CP5 toolchain manifest bytes;
- exact CP5 package inventory bytes.

Before writing a ZIP, CP6 independently re-hashes every retained byte payload against its manifest/CP5 receipt. Missing bytes, drift, unexpected plate G-code, filename path traversal, malformed retained toolchain metadata, or any hash mismatch fail closed.

The exact Orca runtime binary is not duplicated into every job bundle. CP5 already binds that binary by SHA-256 to a digest-pinned execution image. CP6 retains the lock, toolchain manifest, package inventory, and immutable CP5 receipt/reference required to identify that runtime.

## Production versus candidate bundle

The default builder mode requires `evaluate_fdm_authority()` to report `production_authoritative` before packaging.

Current repository CP5 state is intentionally **unpublished**, so the real CP6 CI path uses the explicit candidate mode. Candidate packaging is allowed only when the unresolved technical-authority findings are limited to the separately controlled gates:

- `missing_immutable_toolchain` (the reviewed CP5 image has not been published/digest-locked for production);
- `machine_not_production_ready` (physical machine/profile qualification has not been supplied to that CI job).

Any unrelated authority failure—G-code validation, profile/source/project drift, instance/plate mismatch, incomplete statistics, human-review gate removal, or other contract failure—still blocks candidate packaging.

Candidate ZIPs are marked `evidence_candidate` and `productionEnablementPerformed: false`. They cannot self-grant authority.

## Deterministic ZIP rules

CP6 deliberately uses a simple deterministic ZIP representation:

- fixed member order;
- fixed DOS timestamp `1980-01-01 00:00:00`;
- fixed regular-file permissions;
- `ZIP_STORED` for every member rather than deflate compression;
- canonical UTF-8 JSON (`sort_keys`, compact separators, trailing newline) for generated evidence files;
- normalized archive paths based on physical plate index rather than filesystem discovery order.

Using stored members avoids zlib-version-dependent output and makes identical retained inputs produce identical archive bytes.

The archive ends with:

- `manifest.json` — canonical FDM production manifest plus a non-enabling `bundle` layout block;
- `bundle-index.json` — path, byte count, and SHA-256 for every preceding archive member, including `manifest.json`.

The index intentionally does not hash itself, avoiding a circular self-digest.

## Bundle layout

The current layout contains:

- `source/<original-source-name>`;
- `project/<retained-project-name>`;
- `profiles/machine.json`;
- `profiles/process.json`;
- `profiles/filament.json`;
- `toolchain/lock.json`;
- `toolchain/manifest.json`;
- `toolchain/packages.txt`;
- `evidence/toolchain-receipt.json`;
- `evidence/instance-plate.json`;
- `plates/plate-NNN.gcode` for every physical plate in index order;
- `evidence/plates/plate-NNN-validation.json` for the corresponding CP3 receipt;
- `manifest.json`;
- `bundle-index.json`.

The production manifest's bundle block maps stable plate ids to those normalized archive member paths while preserving the original retained G-code filename as evidence outside the archive naming convention.

## Real integration proof

The CP6 workflow uses the parallel CP5 authority candidate rather than the live service. It:

1. builds the exact CP5 toolchain candidate;
2. records candidate digest-bound CP5 provenance without publishing it;
3. generates a real five-instance, multi-plate RatRig PLA production 3MF through the existing endpoint with `verify=false`;
4. reopens only that exact retained 3MF through CP2;
5. independently validates every exact plate G-code through CP3;
6. proves stable per-instance/per-plate evidence through CP4;
7. builds a truthful FDM v2 manifest with `productionReady: false` and the unpublished CP5 receipt;
8. asserts the only authority issues are the two explicit external readiness gates above;
9. builds the CP6 ZIP twice in the container and requires identical bytes/SHA-256;
10. copies the retained artifacts to the host, rebuilds the bundle independently, and requires byte-for-byte equality;
11. re-hashes every indexed ZIP member on the host.

This proves deterministic packaging of real manufacturing evidence without claiming physical qualification or published runtime readiness.

## CP6 non-goals

CP6 does **not**:

- publish the CP5 toolchain image or edit `fdm-toolchain.lock.json` to a reviewed production digest;
- deploy `Dockerfile.authority`;
- change the current production website/server request path;
- change printer selection or profiles;
- promote the temporary generic Ender route;
- make price server-authoritative (CP7);
- remove or bypass human workshop review;
- send G-code to a printer.

After CP6, the planned software checkpoint is CP7 server-authoritative pricing derived from the same exact manufacturing evidence. Operational publication/physical qualification remain separate gates and must not be inferred from green software CI.
