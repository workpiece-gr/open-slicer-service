# FDM Authority v2 production API

This checkpoint adds **source support only** for a production-authoritative FDM Authority v2 request. It does not deploy, enable, publish the final service image, qualify, approve, or print anything.

## Entry point

The production app is separate from every existing runtime entry point:

```text
app.authority_production_api:app
```

The normal service remains `app.main:app`; the candidate app remains `app.authority_candidate_api:app`. No Docker command is changed by this checkpoint.

The production route is:

```text
POST /v2/authority-production
```

It is inaccessible unless `ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API` is explicitly enabled and a separate production bearer token is configured.

## Fail-closed production prerequisites

A request is not production-authoritative merely because the production app is started. All of these independent gates must be satisfied:

1. The final service execution image reference is digest-pinned.
2. The committed `fdm-toolchain.lock.json` has status `published` and contains the reviewed published toolchain digest.
3. Exact retained toolchain manifest, package inventory and Orca runtime bytes validate against CP5.
4. A real RatRig machine/profile qualification is represented by an exact canonical `fdm-machine-qualification/1.0.0` receipt plus the exact physical-evidence artifact hashed by that receipt.
5. That receipt binds the selected RatRig, material/quality/strength request and exact machine/process/filament profile SHA-256 values, and retains an approved human qualification-review declaration.
6. The exact RatRig profile is selected; temporary/generic printer profiles are rejected.
7. The shared CP2→CP7 pipeline closes every technical authority issue and CP6 produces a production-authoritative deterministic bundle containing both qualification files as retained evidence.

The committed manufacturing-toolchain lock now satisfies prerequisite 2 with the reviewed immutable digest:

```text
ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

That does **not** make the production API ready. The final service execution image has not been separately published/reviewed and supplied by digest, and no genuine RatRig physical qualification receipt/evidence has been created for production use. The production API also remains disabled by default and is not deployed as the live entry point.

A free-form qualification ID is not sufficient. Software verifies the receipt/evidence bytes and their configuration bindings, but it does not perform the physical qualification itself or invent its measurements, acceptance criteria, reviewer, or result.

Current production qualification configuration:

- `FDM_MACHINE_QUALIFICATION_RECEIPT` — path to the exact canonical receipt JSON;
- `FDM_MACHINE_QUALIFICATION_EVIDENCE` — path to the exact physical-evidence artifact referenced by the receipt.

One configured receipt qualifies only the exact material/quality/strength and machine/process/filament profile hashes recorded in that receipt. A request for a different profile combination must fail closed until that combination has its own genuine qualification evidence. A future multi-profile qualification catalog may select among multiple reviewed receipts, but this checkpoint intentionally does not infer or synthesize such coverage.

## Human review remains separate

Successful technical production authority still returns:

- `productionOrderEligible: false`
- `productionEnablementPerformed: false`
- human review required with status `pending`

The machine-qualification review and the per-order manufacturing review are separate gates. A prior physical-machine qualification cannot approve a customer order. The FDM pricing checkpoint owns authoritative price, not fulfilment permission. The Workpiece website must independently re-verify the stored authority package and record the required per-order human review before an order can become eligible for payment/production.

## Current validation strategy

CI does **not** fake genuine RatRig physical qualification or a successful real production HTTP run.

Instead it proves:

- the real committed CP5 lock validates as `published` with the exact reviewed registry digest;
- the read-only published-toolchain workflow can pull that exact private GHCR image by digest and re-hash its reviewed retained bytes without any package-write permission;
- a **synthetic unpublished lock** still fails before the shared authority pipeline, proving the production path remains fail-closed if publication evidence is absent or regresses;
- a bare qualification identifier or missing receipt/evidence bytes cannot grant production authority;
- receipt/request/profile/evidence drift fails closed;
- CP6 independently re-parses/revalidates the exact qualification receipt against the manifest request/profile bindings, verifies its normalized summary, and retains both the receipt and physical-evidence artifact in production-authoritative bundles;
- the production policy requires published CP5 provenance, a digest-pinned final service execution image, and full technical production authority;
- pricing cannot grant production/order permission;
- production configuration health can truthfully report that the toolchain lock is published while the other production prerequisites remain separate;
- the new app is disabled by default and does not replace the normal or candidate entry points.

A real end-to-end production-authority HTTP acceptance run becomes valid only after the final service image is separately published/reviewed, genuine RatRig physical qualification evidence exists for the exact requested profile combination, and the remaining deliberate provider/configuration gates are completed.

## Current operational boundary

This source state does not:

- publish the final Authority service image;
- deploy `app.authority_production_api:app`;
- change Railway or the live root `Dockerfile` path;
- enable website Authority v2 production mode;
- migrate away from `/v1/project`;
- create RatRig qualification evidence;
- qualify Generic Ender;
- remove per-order human workshop review.
