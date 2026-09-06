# FDM Authority v2 — candidate integration API

Status: additive integration checkpoint. The CP1–CP7 authority chain is merged in `main`; this API exposes that chain through a **separate, disabled-by-default FastAPI entrypoint** for controlled integration testing.

It does not modify the normal `app.main:app` application, `/v1/project`, `/v1/slice`, the normal Docker command, printer profiles, website behavior, checkout, production deployment, or human-review workflow.

## Entry point

The candidate application is:

```text
app.authority_candidate_api:app
```

Its authority route is:

```text
POST /v2/authority-candidate
```

The route is intentionally unavailable unless all of the following are configured:

- `ENABLE_FDM_AUTHORITY_V2_API=1`;
- independent server-to-server bearer token `WORKPIECE_FDM_AUTHORITY_V2_TOKEN`;
- exact 40-hex service source commit through the existing `SOURCE_COMMIT_SHA` / `RAILWAY_GIT_COMMIT_SHA` provenance input;
- `WORKPIECE_FDM_AUTHORITY_RUNTIME_REF` as a content-addressed `...@sha256:<64 hex>` execution-image reference;
- `WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF` as a content-addressed `...@sha256:<64 hex>` candidate toolchain reference;
- the CP5 toolchain lock, toolchain manifest, package inventory, exact Orca runtime and RatRig profiles.

The normal `Dockerfile.authority` already contains the required source/toolchain files, but its default command remains `app.main:app`. CI starts the candidate app only by explicitly overriding that command.

## Current scope

The endpoint accepts the same bounded single-colour STL/material/quality/strength/quantity inputs needed to create an Orca project, but it currently accepts only:

```text
printer=ratrig_vcore3_300
```

The temporary generic Ender route fails closed. Authority v2 must not infer or hardcode the missing exact effective-profile/physical-qualification evidence for that printer.

## Evidence chain

For one request the candidate app performs:

```text
immutable STL
→ exact RatRig machine/process/filament profiles
→ Orca production 3MF
→ CP2 exact fresh-process reopen and per-plate G-code
→ CP3 independent/profile-driven G-code validation
→ CP4 instance/plate evidence
→ CP5 candidate toolchain provenance
→ reconciled exact per-plate statistics
→ CP6 deterministic production bundle
→ CP7 server-authoritative item price
```

The HTTP orchestration does not reimplement those checkpoints. `app/fdm_authority_candidate.py` composes the existing modules and requires their fail-closed contracts to pass.

## Candidate-only invariant

This endpoint deliberately builds CP5 with `require_published=False` and sets physical RatRig qualification to not production-ready. Before it returns a successful candidate, `evaluate_fdm_authority()` must report exactly:

- `missing_immutable_toolchain`;
- `machine_not_production_ready`.

Any additional authority issue fails the request.

A successful response therefore has:

- `authorityState: evidence_candidate`;
- `priceAuthoritative: true` in the CP7 receipt;
- `technicalProductionAuthority: false`;
- `productionOrderEligible: false`;
- `productionEnablementPerformed: false`;
- human review still required/pending.

This API cannot turn toolchain publication, physical machine qualification, or human approval into an implicit side effect.

## Transport

The current controlled-integration response is JSON. It contains:

- source/project summary and hashes;
- the complete production manifest;
- the CP7 pricing receipt;
- CP6 deterministic bundle metadata;
- the exact CP6 bundle as base64.

A configurable `MAX_FDM_AUTHORITY_BUNDLE_BYTES` transport bound fails closed before returning an oversized bundle. A future website integration may use a more efficient server-to-server binary/storage transport, but that should be a separate reviewed change rather than changing the candidate evidence semantics here.

## Validation

`.github/workflows/authority-v2-candidate-api.yml` builds the pinned CP5 toolchain and authority images locally, supplies their exact local content IDs as candidate digest references, starts this separate application, and verifies:

1. health/config readiness without revealing token/reference values;
2. unauthenticated requests fail with 401;
3. temporary/generic Ender requests fail with 422;
4. a benign RatRig STL completes the real CP2→CP7 chain;
5. authority issues remain exactly the two deliberate candidate blockers;
6. price is server-authoritative but production enablement remains false;
7. the returned bundle SHA matches the exact returned bytes;
8. every deterministic ZIP member independently matches the CP6 bundle index.

No registry publication, deployment, physical qualification, website integration, payment change, or production enablement is performed by this workflow.
