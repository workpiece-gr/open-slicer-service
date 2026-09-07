# FDM Authority v2 self-host path

`Dockerfile.selfhost-authority` is the portable source-build path for Workpiece-owned Linux hosts and temporary providers that cannot pull the private GHCR packages.

It intentionally rebuilds the exact pinned base image and OrcaSlicer asset used by `Dockerfile.toolchain`, then installs the Authority application and retained lock files in one image.

## Intended uses

- temporary Railway source build while private-registry pulls are unavailable;
- future Workpiece-owned Linux fabrication server;
- local parity and stress testing before cutting traffic away from Railway.

## Safety boundary

This source-built image is **not** the already-published final Authority service image recorded in `fdm-service.lock.json`. It must not claim the published registry digest or silently satisfy the published-service-runtime gate.

The production app starts as `app.authority_production_api:app`, but the API remains disabled unless `ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API` is deliberately enabled. A genuine RatRig qualification receipt/evidence pair remains required before technical production authority can pass, and per-order human workshop review remains mandatory.

Do not set `WORKPIECE_FDM_AUTHORITY_RUNTIME_REF` to the published GHCR service digest when running a source-built image. That would misrepresent the running bytes.

## Build

From the repository root on a Linux/amd64 Docker host:

```sh
docker build -f Dockerfile.selfhost-authority -t workpiece-fdm-authority:selfhost .
```

The build verifies the pinned OrcaSlicer AppImage SHA-256 before extraction and generates the retained toolchain manifest and package inventory.

## Safe first run

Run the service with production disabled:

```sh
docker run --rm -p 8080:8080 \
  -e ENABLE_FDM_AUTHORITY_V2_PRODUCTION_API=0 \
  workpiece-fdm-authority:selfhost
```

Then inspect:

```text
GET /health
```

A source-built pre-qualification runtime is expected to report `ok: false`; that is intentional. The useful checks are that Orca, RatRig profiles, toolchain manifest/package inventory and committed locks are present while published-service identity, machine qualification and deliberate production enablement remain unsatisfied.

## Local candidate runtime for qualification jobs

The same source-built image can run the separate candidate app when generating physical-qualification jobs. This avoids creating a second cloud-specific implementation.

Use the exact local Docker image ID as the content-addressed candidate runtime/toolchain identity. This identity is **candidate evidence only**; it does not publish or qualify the image for production.

```sh
IMAGE_ID="$(docker image inspect workpiece-fdm-authority:selfhost --format '{{.Id}}')"
IMAGE_SHA="${IMAGE_ID#sha256:}"
COMMIT="$(git rev-parse HEAD)"
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

case "$IMAGE_SHA" in
  [0-9a-f][0-9a-f]*) ;;
  *) echo "Unexpected Docker image id" >&2; exit 1 ;;
esac

docker run --rm -p 8081:8080 \
  -e ENABLE_FDM_AUTHORITY_V2_API=1 \
  -e WORKPIECE_FDM_AUTHORITY_V2_TOKEN="$TOKEN" \
  -e SOURCE_COMMIT_SHA="$COMMIT" \
  -e WORKPIECE_FDM_AUTHORITY_RUNTIME_REF="workpiece-fdm-authority-selfhost@sha256:$IMAGE_SHA" \
  -e WORKPIECE_FDM_TOOLCHAIN_IMAGE_REF="workpiece-fdm-authority-selfhost@sha256:$IMAGE_SHA" \
  workpiece-fdm-authority:selfhost \
  sh -c 'uvicorn app.authority_candidate_api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1'
```

Keep the generated token in the local shell only; do not commit it. The candidate endpoint is:

```text
POST http://127.0.0.1:8081/v2/authority-candidate
Authorization: Bearer <local token>
```

The candidate path remains deliberately non-production-authoritative. It must retain the machine-not-qualified and immutable-production-toolchain blockers, and human review remains pending. Use its exact retained bundle/G-code as the digital input for the physical protocol in `FDM_RATRIG_QUALIFICATION_PLAN.md`.

## Future migration sequence

1. Prepare the Linux host with Docker and persistent monitoring/logging.
2. Run the current `/v1/project` service and this Authority service in parallel.
3. Compare health, exact 3MF output and repeated/concurrent slicing against the hosted path.
4. Keep the current hosted slicer available during the comparison period.
5. Generate qualification candidates locally from the exact source-built image identity.
6. Perform real RatRig qualification and retain the exact evidence package.
7. Deliberately review/publish/lock the exact self-host production runtime that will be used, or pull the already-published immutable service image if registry access is available.
8. Only then configure the production Authority runtime identity, qualification evidence and production token.
9. Switch website Authority mode separately after end-to-end evidence/storage/admin/checkout acceptance.

Moving hosting never removes the human workshop-review requirement and never qualifies Generic Ender automatically.
