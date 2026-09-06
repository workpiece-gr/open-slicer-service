# FDM Authority v2 — CP5 publication and digest-transition runbook

This runbook prepares the **manual, explicitly approved** transition from the current CP5 candidate state to a reviewed published toolchain digest. It does not authorize a registry write, deployment, website production-mode change, printer qualification, or production fulfilment.

The current safe state is intentionally:

```text
fdm-toolchain.lock.json
status: unpublished
digest: null
```

Until Chris explicitly approves publication, stop before every registry write described below.

## Safety invariants

The publication transition must preserve all of these invariants:

- the existing live `Dockerfile` and `/v1/project` path remain unchanged;
- candidate CI cannot publish or grant itself production authority;
- no mutable tag is accepted as manufacturing authority;
- the exact published toolchain image is identified by `image@sha256:<digest>`;
- the final service execution image is separately identified by its own digest-pinned reference;
- publication does not deploy the authority service;
- publication does not configure website `production` mode;
- publication does not create or imply RatRig physical qualification evidence;
- Generic Ender remains unqualified;
- human workshop review remains mandatory.

## Phase A — pre-publication review (safe now)

1. **Freeze the exact source revision.** Record the reviewed `open-slicer-service` commit SHA. Do not publish from a moving branch or a dirty working tree.
2. **Confirm the committed lock is still unpublished.** `status` must be `unpublished` and `digest` must be `null` before approval.
3. **Confirm the candidate pins.** Review the exact base-image digest, Orca version, release asset id, AppImage filename, AppImage SHA-256, platform, `Dockerfile.toolchain`, and `Dockerfile.authority` inputs in `fdm-toolchain.lock.json`.
4. **Require a green CP5 candidate run on the exact reviewed commit.** Retain the candidate evidence artifact from `.github/workflows/cp5-immutable-toolchain.yml`.
5. **Review the candidate evidence.** At minimum confirm the toolchain manifest, package-inventory hash, exact Orca `AppRun` hash, local toolchain image id, local authority image id, candidate provenance receipt, CP2→CP3 chain report, and retained G-code hashes.
6. **Confirm the candidate remains non-authoritative.** Candidate provenance must still report `authorityState: evidence_candidate` and `authorityCriticalComplete: false`.

A local Docker image id is useful candidate evidence, but it is **not** the registry manifest digest that may later be committed to the lock.

## Approval boundary — mandatory stop

**STOP. Do not log in to GHCR, push an image, create/update a package, or change the lock to `published` without explicit approval from Chris for the exact reviewed candidate.**

Approval should identify at least:

- exact source commit SHA;
- target image repository from the lock;
- review tag from the lock;
- platform (`linux/amd64`);
- the candidate evidence/run being approved.

If any of those inputs change after approval, return to Phase A and obtain approval for the new candidate.

## Phase B — approved toolchain publication

Only after the approval boundary has been satisfied:

1. Build the exact reviewed `Dockerfile.toolchain` from the frozen commit for `linux/amd64`.
2. Re-run the same manifest/runtime validation used by CP5 candidate CI before the push.
3. Tag that exact reviewed local image with the lock's GHCR repository and review tag.
4. Authenticate to GHCR with credentials that have only the package permissions required for the manual publication operation.
5. Push the reviewed toolchain image once.
6. Record the digest reported for the pushed registry manifest.
7. Independently resolve the tag from GHCR and confirm it resolves to the same `sha256:<64 hex>` digest.
8. Pull the image back **by digest**, not by tag, and re-extract `/opt/workpiece-toolchain/manifest.json`, `/opt/workpiece-toolchain/packages.txt`, and the exact Orca `AppRun` bytes.
9. Re-run `validate_toolchain_manifest(...)` against the committed lock inputs and the bytes extracted from the digest-pinned published image.

If the independently resolved digest differs, if the digest-pinned pull cannot be verified, or if any retained manifest/runtime byte differs from the reviewed candidate evidence, **do not update the lock**. Treat the publication as unusable authority evidence and investigate the mismatch.

## Phase C — lock transition PR

After the exact published digest has been independently verified, open a focused PR that changes the lock to:

```text
status: published
digest: sha256:<exact reviewed registry digest>
```

Do not change the image repository, review tag, upstream Orca pins, base-image digest, build recipe, service recipe, or platform in the same lock-transition PR unless the entire candidate is deliberately re-reviewed and re-published.

### Important current-CI transition

The current candidate workflow and unit suite intentionally hard-assert that the committed lock is `unpublished`. That is a useful pre-publication safety guard today, but an approved lock-transition PR must deliberately replace those state-specific assertions.

The replacement must remain fail-closed:

- a published lock must require a syntactically valid `sha256:` digest;
- CI must never infer or write the digest itself;
- candidate/local image ids must never substitute for the registry digest;
- candidate provenance must remain non-authoritative even when testing code against a published lock;
- published-image verification should be read-only and operate on the exact digest-pinned image;
- no workflow used for verification should receive `packages: write` or contain a registry push command.

Do not weaken or remove the pre-publication guard until the approved publication has actually occurred and the exact digest is available for review.

## Phase D — exact final service execution image

The published manufacturing toolchain digest alone is not sufficient for production authority.

Build the parallel service image from `Dockerfile.authority` using the **digest-pinned** toolchain reference as `TOOLCHAIN_IMAGE`. The resulting final service execution image must then be separately published/reviewed and supplied to Authority v2 as its own `image@sha256:<digest>` reference.

That service-image publication is another registry write and also requires explicit approval. Building or publishing it does not deploy it.

## Phase E — authority proof before deployment

Before any production deployment/configuration, require a controlled proof that the exact retained bytes can construct CP5 provenance with:

- the committed published toolchain digest;
- the exact digest-pinned final service execution image;
- the exact service commit;
- the exact toolchain manifest bytes;
- the exact Orca runtime bytes;
- `authorityState: production_authoritative`;
- `authorityCriticalComplete: true`.

This is only the CP5 technical provenance gate. Production Authority v2 still also requires the real RatRig qualification receipt and physical evidence, CP1–CP7 evidence, website independent verification/storage, human review, and later controlled physical acceptance.

## Explicitly separate actions

The following remain separate, deliberate operations and must not be bundled into the CP5 publication transition:

- production-authority service deployment;
- production credentials or provider configuration;
- website `production` mode enablement;
- migration away from legacy `/v1/project`;
- RatRig physical qualification or qualification receipt creation;
- Ender qualification;
- approval/checkout enablement changes;
- removal of mandatory human workshop review.

Publication is provenance preparation, not deployment and not manufacturing qualification.
