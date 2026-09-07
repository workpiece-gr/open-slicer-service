# FDM Authority v2 — CP5 publication and digest-transition runbook

This runbook records the approval-gated transition from the CP5 candidate state to a reviewed published toolchain digest and defines the remaining service-image/production steps. Publication, deployment, website production mode, printer qualification, and production fulfilment remain separate actions.

## Current verified state

Phases A and B have been completed for the reviewed toolchain candidate, and this lock-transition checkpoint implements Phase C.

The reviewed publication is:

```text
frozen source: 31ca0c983c7bf8b8a92ec44b7fa57b54ddbc9318
image: ghcr.io/workpiece-gr/fdm-slicer-toolchain
tag: orca-2.4.2-noble-20260810-amd64
platform: linux/amd64
digest: sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
visibility: private
```

The approved publication run independently resolved the registry digest, pulled the image back by digest, and reproduced the reviewed toolchain-manifest, package-inventory, and Orca-runtime bytes. `fdm-toolchain.publication.json` retains those facts.

The committed lock is therefore now intended to be:

```text
status: published
digest: sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

## Safety invariants

The publication transition must preserve all of these invariants:

- the existing live `Dockerfile` and `/v1/project` path remain unchanged;
- candidate CI cannot publish or grant itself production authority;
- no mutable tag is accepted as manufacturing authority;
- the exact published toolchain image is identified by `image@sha256:<digest>`;
- the final service execution image is separately identified by its own digest-pinned reference;
- toolchain publication does not deploy the authority service;
- toolchain publication does not configure website `production` mode;
- toolchain publication does not create or imply RatRig physical qualification evidence;
- Generic Ender remains unqualified;
- human workshop review remains mandatory.

## Phase A — pre-publication review — completed

The approved review froze source commit `31ca0c983c7bf8b8a92ec44b7fa57b54ddbc9318`, confirmed the then-unpublished lock and exact upstream/build pins, required green CP5 candidate evidence, and reviewed the retained manifest/package/runtime/local-image/CP2→CP3 evidence.

Candidate provenance remained:

- `authorityState: evidence_candidate`;
- `authorityCriticalComplete: false`.

Local Docker image IDs were treated only as candidate evidence and never as registry digests.

## Approval boundary — satisfied for the exact reviewed toolchain

Chris explicitly approved publication after the safety check for the exact frozen candidate. That approval applied only to the toolchain image above. It did not approve the final service image, deployment, physical qualification, website production mode, or any other operational change.

Any future change to the reviewed inputs requires a new review and explicit approval.

## Phase B — approved toolchain publication — completed

The approved publication operation:

1. built the exact reviewed `Dockerfile.toolchain` for `linux/amd64`;
2. revalidated the reviewed manifest/package/runtime bytes before the registry write;
3. pushed only the reviewed toolchain tag;
4. independently resolved the resulting registry digest;
5. pulled the image back **by digest**;
6. re-extracted the exact toolchain manifest, package inventory, and Orca runtime;
7. required byte/hash equality with the reviewed candidate evidence;
8. confirmed the created GHCR package was private;
9. recorded that no service image, deployment, physical qualification, or production enablement occurred;
10. removed the one-time package-write workflow after the successful operation.

The verified immutable toolchain reference is:

```text
ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

## Phase C — lock transition — this checkpoint

The focused transition changes only the lock state/digest among the manufacturing pins:

```text
status: published
digest: sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

It does **not** change:

- image repository;
- review tag;
- upstream Orca pins;
- base-image digest;
- `Dockerfile.toolchain` manufacturing recipe;
- `Dockerfile.authority` service recipe;
- platform;
- profiles.

### CI transition

The former pre-publication assertions are deliberately replaced rather than weakened:

- the committed lock must validate as `published` with a real `sha256:` digest;
- the exact digest must match the retained actual publication record;
- candidate/local image IDs never substitute for the registry digest;
- candidate provenance remains `evidence_candidate` / `authorityCriticalComplete: false` even when the committed lock is published;
- the old automatic pre-publication review workflow is retired because its generator correctly rejects a published lock;
- a new published-image verifier uses only `contents: read` and `packages: read`, pulls the exact digest-pinned image, and re-hashes its retained bytes against `fdm-toolchain.publication.json`;
- no published-state verification workflow receives `packages: write` or contains a registry push command.

Synthetic unpublished-lock tests remain to prove that production authority still fails closed if a future malformed/unpublished lock is supplied.

## Phase D — exact final service execution image — not yet performed

The published manufacturing toolchain digest alone is not sufficient for production authority.

The parallel service image must be built from `Dockerfile.authority` using the **digest-pinned** toolchain reference as `TOOLCHAIN_IMAGE`. The resulting final service execution image must then be separately published/reviewed and supplied to Authority v2 as its own `image@sha256:<digest>` reference.

That service-image publication is another registry write and requires a separate explicit approval. Building or publishing it does not deploy it.

## Phase E — authority proof before deployment — not yet performed

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

The following remain separate, deliberate operations and must not be bundled into this lock transition:

- final service-image publication;
- production-authority service deployment;
- production credentials or provider configuration;
- website `production` mode enablement;
- migration away from legacy `/v1/project`;
- RatRig physical qualification or qualification receipt creation;
- Ender qualification;
- approval/checkout enablement changes;
- removal of mandatory human workshop review.

The published toolchain is now immutable provenance infrastructure. It is not deployment and not manufacturing qualification.
