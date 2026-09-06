# FDM Authority v2 — CP5 publication review handoff

This checkpoint prepares the immutable CP5 toolchain for a later, explicitly approved registry publication without publishing, deploying, or granting production authority.

The canonical CP5 lock remains `status: unpublished` with `digest: null`. That state must not be changed by candidate CI or by this review workflow.

## Why this handoff exists

The existing CP5 candidate workflow proves that the pinned Ubuntu/Orca toolchain and parallel authority service can build and execute the FDM authority smoke chain. Its local Docker image IDs are useful candidate evidence, but they are **not registry manifest digests** and cannot be copied into `fdm-toolchain.lock.json` as production evidence.

Before any registry write is approved, reviewers need one deterministic packet binding the exact candidate to:

- exact service commit;
- exact committed `fdm-toolchain.lock.json` bytes;
- exact `Dockerfile.toolchain` bytes;
- exact `Dockerfile.authority` bytes;
- exact extracted toolchain manifest;
- exact installed package inventory;
- exact extracted Orca runtime bytes;
- local toolchain candidate image ID;
- local final-service candidate image ID;
- intended GHCR repository/tag/platform from the lock.

`app/fdm_toolchain_publication_review.py` creates that packet as `fdm-toolchain-publication-review/1.0.0`.

## Safety properties

The packet is deliberately non-authoritative. It always records:

- `authorityState: publication_review_candidate`;
- `authorityCriticalComplete: false`;
- `publication.performed: false`;
- `publication.registryDigest: null`;
- `publication.explicitApprovalRequired: true`;
- `production.productionAuthorityEligible: false`;
- `production.productionEnablementPerformed: false`;
- `retainedCandidateEvidence.localImageIdsAreRegistryDigests: false`.

A published lock cannot be represented as a pre-publication packet. The generator fails closed if the lock is already `published`, if exact package/runtime evidence drifts from the toolchain manifest, if the source commit is not a full SHA, or if the local candidate image IDs are not content-addressed Docker image IDs.

This packet does **not** qualify a physical printer, replace the RatRig qualification receipt/evidence contract, remove human review, or change `/v1/project`.

## Read-only review workflow

`.github/workflows/cp5-publication-review.yml` is a review-only workflow. It:

1. runs CP5 provenance/publication-review unit tests;
2. builds `Dockerfile.toolchain` locally;
3. extracts the exact manifest, package inventory and Orca runtime bytes;
4. records the local toolchain image ID;
5. builds `Dockerfile.authority` on that local toolchain candidate;
6. records the local service image ID;
7. generates `publication-review-packet.json`;
8. proves the committed lock is still unpublished and the workflow contains no registry-write capability;
9. uploads only the review evidence artifact.

The workflow has only `contents: read` permission. It does not request package-write permission, authenticate to GHCR, push an image, change the lock, or deploy anything.

## Required review before publication

When this checkpoint is merged, a future publication should still remain a separate operator-controlled action. Before asking for publication approval:

1. run the publication-review workflow from the exact reviewed source state intended for publication;
2. retain `publication-review-packet.json`, `toolchain-manifest.json`, `packages.txt`, and both local image-ID files;
3. verify the packet source commit and recipe/lock hashes against the reviewed source;
4. verify the packet still says publication was not performed and production authority is not eligible;
5. review the pinned base image, Orca release asset identity/SHA, runtime SHA and package-inventory SHA;
6. obtain explicit approval for the registry publication itself.

No registry digest exists until that approved publication occurs. Do not infer or fabricate one from a local Docker image ID.

## Required steps after an approved publication

After an explicitly approved publication, but before production authority can be enabled:

1. capture the exact registry-returned `sha256:` digest for the published toolchain image;
2. independently verify the digest-pinned published image is the reviewed toolchain candidate;
3. update `fdm-toolchain.lock.json` in a separate reviewed checkpoint to `status: published` with that exact digest;
4. build the exact final service execution image from the digest-pinned toolchain image;
5. capture and review the final service execution image by digest;
6. keep the production-authority service disabled until genuine RatRig physical qualification evidence and the remaining deliberate provider/configuration gates are complete.

Merge, publication, provider configuration, and deployment remain separate actions.
