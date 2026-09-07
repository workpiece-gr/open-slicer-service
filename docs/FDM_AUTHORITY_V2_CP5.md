# FDM Authority v2 — CP5 immutable toolchain provenance

CP5 provides an immutable manufacturing-toolchain authority boundary alongside the current FDM service image. It does not replace or deploy the existing production Dockerfile.

## Current state

The reviewed manufacturing toolchain has now been explicitly published and independently verified. The committed lock is:

```text
status: published
digest: sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

The immutable toolchain reference is therefore:

```text
ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:3cee4cdf6b09237a77a1bb76226830dc9f363211657511d9d4b1a8edbf744739
```

`fdm-toolchain.publication.json` retains the reviewed publication facts from the approved publication run: frozen source commit, repository/tag/platform, exact registry digest, reviewed recipe/manifest/package/runtime SHA-256 values, private package visibility, successful digest-pinned pull-back, and explicit statements that no service image, deployment, physical qualification, or production enablement was performed.

Publication of this toolchain does **not** by itself grant FDM production authority.

## Authority boundary

FDM production authority needs two separate content-addressed image identities:

1. **manufacturing toolchain image** — Ubuntu/system libraries plus the exact OrcaSlicer runtime;
2. **final service execution image** — the exact toolchain plus the exact Workpiece service code and Python environment that executes the job.

A mutable tag is not sufficient for either authority boundary. Production receipts must use `image@sha256:<digest>` references.

The manufacturing-toolchain boundary is now published and digest-pinned. The final service execution image is still a separate future publication/review boundary.

## Parallel build path

The existing `Dockerfile` remains unchanged and continues to represent the current working production path.

CP5 uses:

- `Dockerfile.toolchain` — rarely rebuilt Orca/system dependency image;
- `Dockerfile.authority` — parallel service image that consumes a supplied `TOOLCHAIN_IMAGE`;
- `fdm-toolchain.lock.json` — reviewed immutable toolchain lock;
- `fdm-toolchain.publication.json` — retained publication verification facts;
- `app/fdm_toolchain_provenance.py` — fail-closed provenance validation;
- `.github/workflows/cp5-immutable-toolchain.yml` — candidate-only local integration evidence;
- `.github/workflows/cp5-published-toolchain.yml` — read-only verification of the exact published digest.

No CP5 file changes Railway/deployment configuration or makes the live service consume the authority image.

## Upstream pins

The committed lock fixes the published CP5 toolchain to:

- platform: `linux/amd64`;
- Ubuntu Noble dated image `noble-20260810` by full amd64 manifest digest;
- OrcaSlicer `2.4.2`;
- exact Linux Ubuntu 24.04 AppImage asset name;
- exact GitHub release asset id;
- exact upstream AppImage SHA-256.

`Dockerfile.toolchain` verifies the downloaded AppImage with `sha256sum -c` before extraction.

The image records `/opt/workpiece-toolchain/manifest.json` containing:

- schema/version;
- platform;
- exact base-image reference;
- Orca version and upstream asset SHA-256;
- exact runtime `AppRun` path and SHA-256;
- SHA-256 of the complete installed dpkg package/version inventory.

The published verification workflow pulls the exact locked image **by digest**, re-extracts those retained bytes, validates them against the lock, and requires the manifest/package/runtime hashes to match `fdm-toolchain.publication.json`.

## Why the final service digest is separate

Even with an immutable toolchain image, the final service layer adds:

- exact Workpiece application code;
- exact profiles;
- Python packages and their resolved transitive environment;
- runtime configuration baked into the image.

Therefore pinning the toolchain alone is not sufficient. Technical production authority also requires the exact final service image reference by digest.

## Provenance receipt

`fdm-toolchain-provenance/1.0.0` includes:

- exact service commit;
- exact Orca runtime SHA-256;
- digest-pinned final execution environment reference/digest;
- digest-pinned manufacturing-toolchain image reference/digest;
- toolchain-manifest SHA-256;
- digest-pinned base image reference;
- upstream Orca asset identity/SHA-256;
- installed-package inventory SHA-256;
- committed lock SHA-256 and status.

The validator checks the manifest against the committed lock and independently hashes the actual runtime Orca bytes.

## Candidate vs production

A **published lock does not make local candidate CI authoritative**.

Candidate workflows continue to build local content-addressed images and call:

```text
build_toolchain_provenance(..., require_published=False)
```

Those receipts must still report:

- `authorityState: evidence_candidate`;
- `authorityCriticalComplete: false`;
- `lockStatus: published`.

The lock status truthfully records that the reviewed manufacturing toolchain exists, while the candidate receipt remains non-authoritative because it is bound to local candidate image IDs rather than the reviewed published toolchain plus a reviewed final service image.

Production provenance requires:

- the committed published toolchain digest;
- the exact published toolchain bytes;
- an independently published/reviewed final service execution image by digest;
- the remaining CP1–CP7 evidence and machine qualification gates.

## Publication handoff history

Before publication, `app/fdm_toolchain_publication_review.py` and the former `cp5-publication-review.yml` workflow created deterministic non-publishing review evidence. The generator intentionally rejects a published lock so historical pre-publication evidence cannot be recreated or misrepresented after the transition.

The automatic pre-publication workflow is retired in the published state. Its contract remains unit-tested with synthetic unpublished locks, and the actual approved publication evidence is retained in `fdm-toolchain.publication.json`.

See [`FDM_AUTHORITY_V2_CP5_PUBLICATION_REVIEW.md`](FDM_AUTHORITY_V2_CP5_PUBLICATION_REVIEW.md) for the historical handoff and [`FDM_AUTHORITY_V2_CP5_PUBLICATION.md`](FDM_AUTHORITY_V2_CP5_PUBLICATION.md) for the approval-gated transition record and remaining phases.

## Candidate integration gate

The CP5 candidate workflow still:

1. builds `Dockerfile.toolchain` locally;
2. extracts the exact toolchain manifest, package inventory, and Orca `AppRun` bytes;
3. validates them against the published `fdm-toolchain.lock.json`;
4. records the local content-addressed toolchain image ID;
5. builds `Dockerfile.authority` on that local candidate toolchain;
6. records the local content-addressed final service image ID;
7. creates candidate-only CP5 provenance and proves it cannot self-authorize;
8. starts the authority service candidate;
9. generates a real RatRig production 3MF;
10. reopens that exact 3MF through CP2;
11. independently validates the resulting exact G-code through CP3;
12. re-hashes the retained G-code on the host;
13. uploads candidate evidence only.

## Published verification gate

The published verification workflow:

1. validates the committed lock with `require_published=True`;
2. validates the retained publication receipt against the lock and reviewed `Dockerfile.toolchain` hash;
3. authenticates to GHCR with `packages: read` only;
4. pulls `ghcr.io/workpiece-gr/fdm-slicer-toolchain@sha256:3cee4cdf...` by digest;
5. extracts the exact manifest, package inventory, and Orca runtime bytes;
6. re-validates those bytes against the lock;
7. requires their SHA-256 values to match the retained publication record;
8. uploads verification evidence only.

The workflow contains no package-write permission and no registry push command.

## Relationship to CP1–CP4

CP5 does not weaken earlier gates. The technical chain still requires all of:

- immutable source/config/project evidence;
- exact retained per-plate G-code;
- CP3 independent validation;
- CP4 exact instance/plate membership evidence;
- CP5 immutable toolchain provenance.

The CP5 receipt itself cannot replace any CP2–CP4 evidence.

## Remaining non-goals / gates

This published-lock checkpoint does not:

- publish the final service execution image;
- change the live `Dockerfile`;
- deploy the authority service;
- change `/v1/project` or website behavior;
- change any printer/material profile;
- qualify the RatRig physically;
- qualify the generic Ender route;
- enable website production mode or checkout;
- remove the human workshop review gate.
