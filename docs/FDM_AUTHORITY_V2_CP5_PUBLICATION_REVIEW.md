# FDM Authority v2 — CP5 publication review handoff

This document records the pre-publication review handoff that was used before the CP5 manufacturing toolchain was explicitly published. It remains relevant as the historical safety contract for future re-publication, but the repository is no longer in the unpublished state described by the original checkpoint.

## Completed transition

The reviewed candidate was published after explicit approval and independently verified by digest-pinned pull-back. The committed toolchain lock now points to:

```text
ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

`fdm-toolchain.publication.json` retains the factual publication evidence derived from the successful approved run, including:

- frozen reviewed source commit `31ca0c983c7bf8b8a92ec44b7fa57b54ddbc9318`;
- exact repository/tag/platform;
- exact registry digest;
- reviewed `Dockerfile.toolchain` SHA-256;
- exact toolchain-manifest, package-inventory, and Orca-runtime SHA-256 values;
- private package visibility;
- successful pre-push byte verification and digest-pinned pull-back verification;
- explicit evidence that no service image, deployment, physical qualification, or production enablement was performed.

## Why the pre-publication handoff existed

The CP5 candidate workflow proved that the pinned Ubuntu/Orca toolchain and parallel authority service could build and execute the FDM authority smoke chain. Its local Docker image IDs were useful candidate evidence, but they were **not registry manifest digests** and could not be copied into `fdm-toolchain.lock.json` as production evidence.

Before the registry write was approved, reviewers required one deterministic packet binding the exact candidate to:

- exact service commit;
- exact then-unpublished `fdm-toolchain.lock.json` bytes;
- exact `Dockerfile.toolchain` bytes;
- exact `Dockerfile.authority` bytes;
- exact extracted toolchain manifest;
- exact installed package inventory;
- exact extracted Orca runtime bytes;
- local toolchain candidate image ID;
- local final-service candidate image ID;
- intended GHCR repository/tag/platform from the lock.

`app/fdm_toolchain_publication_review.py` creates that packet as `fdm-toolchain-publication-review/1.0.0`.

## Safety properties retained in code

The pre-publication packet is deliberately non-authoritative. It always records:

- `authorityState: publication_review_candidate`;
- `authorityCriticalComplete: false`;
- `publication.performed: false`;
- `publication.registryDigest: null`;
- `publication.explicitApprovalRequired: true`;
- `production.productionAuthorityEligible: false`;
- `production.productionEnablementPerformed: false`;
- `retainedCandidateEvidence.localImageIdsAreRegistryDigests: false`.

A published lock cannot be represented as a pre-publication packet. The generator still fails closed if the lock is already `published`, if exact package/runtime evidence drifts from the toolchain manifest, if the source commit is not a full SHA, or if the local candidate image IDs are not content-addressed Docker image IDs.

The unit suite now exercises this contract with a **synthetic unpublished lock** and separately proves that the real committed published lock cannot be misrepresented as pre-publication evidence.

## Workflow retirement

The former `.github/workflows/cp5-publication-review.yml` was intentionally read-only and was used to prepare the approved candidate. After successful publication and digest verification it is retired from the published repository state, because automatically rebuilding a “pre-publication review packet” from a committed published lock would be semantically wrong and the generator correctly rejects that state.

Published-state verification is now handled by `.github/workflows/cp5-published-toolchain.yml`, which:

1. validates the committed published lock and retained publication record;
2. authenticates to GHCR with `packages: read` only;
3. pulls the exact image by immutable digest;
4. re-extracts the manifest, package inventory, and Orca runtime bytes;
5. validates them against the lock;
6. re-hashes them against the retained publication record;
7. uploads verification evidence only.

It has no package-write permission and no push command.

## Future re-publication rule

If the toolchain repository, review tag, platform, base-image digest, Orca pins, `Dockerfile.toolchain`, or any reviewed toolchain bytes change in the future, the current published digest must not be reused. A new candidate must return to the same pre-publication review process, obtain explicit approval, be published separately, be independently resolved/pulled by digest, and only then receive a new focused lock-transition review.

Local Docker image IDs must never substitute for a registry digest.

## Still separate after toolchain publication

The following remain independent deliberate gates:

- final Authority service image publication and digest verification;
- production-authority service deployment;
- production credentials/provider configuration;
- genuine RatRig physical qualification and immutable evidence;
- website production-mode enablement;
- migration away from `/v1/project`;
- per-order human workshop review;
- Ender qualification.

Toolchain publication is provenance preparation, not deployment or manufacturing qualification.
