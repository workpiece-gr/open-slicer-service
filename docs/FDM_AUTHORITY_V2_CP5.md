# FDM Authority v2 — CP5 immutable toolchain provenance

CP5 adds an immutable manufacturing-toolchain path alongside the current FDM service image. It does not replace or deploy the existing production Dockerfile.

## Authority boundary

FDM production authority needs two separate content-addressed image identities:

1. **manufacturing toolchain image** — Ubuntu/system libraries plus the exact OrcaSlicer runtime;
2. **final service execution image** — the exact toolchain plus the exact Workpiece service code and Python environment that executes the job.

A mutable tag is not sufficient for either authority boundary. Production receipts must use `image@sha256:<digest>` references.

## Parallel build path

The existing `Dockerfile` remains unchanged and continues to represent the current working production path.

CP5 adds:

- `Dockerfile.toolchain` — rarely rebuilt Orca/system dependency image;
- `Dockerfile.authority` — parallel service image that consumes a supplied `TOOLCHAIN_IMAGE`;
- `fdm-toolchain.lock.json` — reviewed upstream/toolchain lock;
- `app/fdm_toolchain_provenance.py` — fail-closed provenance validation;
- a candidate-only CI workflow.

No CP5 file changes Railway/deployment configuration or makes the live service consume the authority image.

## Upstream pins

The committed lock fixes the CP5 candidate to:

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

The package inventory is evidence of the exact built candidate. It is not treated as a substitute for the final image digest.

## Why the final service digest is separate

Even with an immutable toolchain image, the final service layer adds:

- exact Workpiece application code;
- exact profiles;
- Python packages and their resolved transitive environment;
- runtime configuration baked into the image.

Therefore CP5 does not claim that pinning only Orca or only the toolchain base is sufficient. Technical production authority requires the exact final service image reference by digest as well.

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

The committed lock intentionally starts as:

```text
status: unpublished
digest: null
```

This is a safety property, not missing evidence hidden by CI.

Candidate CI may build local content-addressed images and record their Docker image IDs, but `build_toolchain_provenance(..., require_published=False)` must return:

- `authorityState: evidence_candidate`;
- `authorityCriticalComplete: false`.

Production provenance is only possible after a separate, explicitly approved publication/review step updates the committed lock to:

- `status: published`;
- exact `sha256:<digest>` of the reviewed toolchain image.

The final service runtime must independently be supplied as a digest-pinned execution reference.

CP5 does not implement or execute that publication step. The CI workflow has read-only repository permission, no package-write permission, no registry login, and no push command.

## Pre-publication review handoff

Before any approved registry publication, `.github/workflows/cp5-publication-review.yml` can build the exact candidate locally and emit a deterministic `fdm-toolchain-publication-review/1.0.0` packet. The packet binds the reviewed source commit, lock bytes, both CP5 Docker recipes, exact extracted manifest/package/runtime evidence, local candidate image IDs, and the intended repository/tag/platform.

That packet is deliberately non-authoritative: it records that publication was not performed, no registry digest exists, production authority is not eligible, production enablement was not performed, and explicit publication approval is still required. Local Docker image IDs are retained only as candidate evidence and are explicitly not treated as registry manifest digests.

See [`FDM_AUTHORITY_V2_CP5_PUBLICATION_REVIEW.md`](FDM_AUTHORITY_V2_CP5_PUBLICATION_REVIEW.md) for the review and later operator handoff sequence. The read-only review workflow does not authenticate to GHCR, push images, update the lock, deploy anything, or create physical-machine qualification evidence.

## Candidate integration gate

The CP5 workflow:

1. builds `Dockerfile.toolchain` locally;
2. extracts the exact toolchain manifest, package inventory, and Orca `AppRun` bytes;
3. validates them against `fdm-toolchain.lock.json`;
4. records the local content-addressed toolchain image ID;
5. builds `Dockerfile.authority` on that candidate toolchain;
6. records the local content-addressed final service image ID;
7. creates candidate-only CP5 provenance and proves it cannot self-authorize;
8. starts the authority service candidate;
9. generates a real RatRig production 3MF;
10. reopens that exact 3MF through CP2;
11. independently validates the resulting exact G-code through CP3;
12. re-hashes the retained G-code on the host;
13. uploads candidate evidence only.

## Relationship to CP1–CP4

CP5 does not weaken earlier gates. Once wired into the v2 authority evaluator, the technical chain requires all of:

- immutable source/config/project evidence;
- exact retained per-plate G-code;
- CP3 independent validation;
- CP4 exact instance/plate membership evidence;
- CP5 immutable toolchain provenance.

The CP5 receipt itself cannot replace any CP2–CP4 evidence.

## Non-goals

CP5 does not:

- publish a Docker/GHCR image;
- update a registry digest automatically;
- change the live `Dockerfile`;
- deploy the authority image;
- change `/v1/project` or website behavior;
- change any printer/material profile;
- qualify the generic Ender route;
- create the deterministic production bundle (CP6);
- make pricing server-authoritative (CP7);
- remove the human workshop review gate.
