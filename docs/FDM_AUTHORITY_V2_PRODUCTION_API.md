# FDM Authority v2 production API

This checkpoint adds **source support only** for a production-authoritative FDM
Authority v2 request. It does not deploy, enable, publish, qualify, approve, or
print anything.

## Entry point

The production app is separate from every existing runtime entry point:

```text
app.authority_production_api:app
```

The normal service remains `app.main:app`; the candidate app remains
`app.authority_candidate_api:app`. No Docker command is changed by this
checkpoint.

The production route is:

```text
POST /v2/authority-production
```

It is inaccessible unless `ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API` is explicitly
enabled and a separate production bearer token is configured.

## Fail-closed production prerequisites

A request is not production-authoritative merely because the production app is
started. All of these independent gates must be satisfied:

1. The final service execution image reference is digest-pinned.
2. The committed `fdm-toolchain.lock.json` has status `published` and contains
   the reviewed published toolchain digest.
3. Exact retained toolchain manifest, package inventory and Orca runtime bytes
   validate against CP5.
4. A real, non-empty RatRig machine/profile qualification evidence identifier
   is supplied through configuration.
5. The exact RatRig profile is selected; temporary/generic printer profiles are
   rejected.
6. The shared CP2→CP7 pipeline closes every technical authority issue and CP6
   produces a production-authoritative deterministic bundle.

The repository's current committed toolchain lock is intentionally
`unpublished`, so the production app is expected to report that its production
preconditions are incomplete. Do not change that status or add a digest until
the real immutable image has been published and reviewed.

Likewise, production machine qualification now requires two exact retained files: a canonical `fdm-machine-qualification/1.0.0` receipt and the physical-evidence artifact hashed by that receipt. The receipt binds the RatRig, material/quality/strength selection, exact machine/process/filament profile SHA-256 values, qualification protocol/review metadata, and evidence SHA/byte count. A free-form evidence ID is not sufficient and must never be invented to make software authority pass.

Current production qualification configuration:

- `FDM_MACHINE_QUALIFICATION_RECEIPT` (path to the exact canonical receipt JSON)
- `FDM_MACHINE_QUALIFICATION_EVIDENCE` (path to the exact physical-evidence artifact referenced by the receipt)

## Human review remains separate

Successful technical production authority still returns:

- `productionOrderEligible: false`
- `productionEnablementPerformed: false`
- human review required with status `pending`

The FDM pricing checkpoint owns authoritative price, not fulfilment permission.
The Workpiece website must independently re-verify the stored authority package
and record the required human review before an order can become eligible for
payment/production.

## Current validation strategy

Because the real CP5 publication and physical RatRig qualification are external
operational gates, CI does **not** fake a successful real production HTTP run.
Instead it proves:

- the actual current unpublished lock fails before the shared authority pipeline;
- missing machine qualification evidence fails immediately;
- the production policy requires published CP5 provenance and full technical
  production authority;
- pricing cannot grant production/order permission;
- the new app is disabled by default and does not replace the normal or
  candidate entry points.

A real end-to-end production-authority HTTP acceptance run becomes valid only
after the real CP5 publication and physical qualification work is completed.
