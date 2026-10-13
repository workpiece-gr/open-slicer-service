# Open Slicer Service

An isolated HTTP wrapper around OrcaSlicer for manufacturing quotes and toolpath previews. The service accepts a single-colour STL, applies material plus independent quality and strength choices, runs a pinned OrcaSlicer build, and returns production-slicer estimates plus a sampled extrusion toolpath.

This repository is intentionally licensed under **GNU AGPL-3.0-or-later**. Keep the exact deployed source public and set `SOURCE_CODE_URL` to that public repository.

## Deployment paths

- **Source:** this public GitHub repository, with `SOURCE_CODE_URL` pointing to the deployed source for AGPL compliance.
- **Target host:** the Workpiece-owned Linux server; bind the legacy `/v1/project` container to loopback using the website repository's manufacturing-service runbook.
- **Legacy provider:** Railway remains an exit/rollback dependency, not the target architecture.
- **Slicer:** OrcaSlicer 2.4.2, with the base-image digest and downloaded AppImage SHA-256 pinned in `Dockerfile`.

The legacy image is separate from the immutable qualified Authority-v2 image. A source rebuild must retain its own image digest; a matching Git commit or label alone does not prove identical runtime bytes.

## Installed FDM profiles

The resolved profile matrix is built from the supplied RatRig V-Core 3 300 / 0.4 mm OrcaSlicer bundle and matching OrcaSlicer 2.4.2 upstream bases:

```text
profiles/machine.json
profiles/process/{pla,petg,pctg,abs,tpu}.json
profiles/filament/{pla,petg,pctg,abs,tpu}.json
```

Validate the resulting G-code in the OrcaSlicer desktop preview and with controlled test prints before treating it as production output. The initial service remains limited to one STL and one selected material per request.

## Local test

```bash
docker build -t open-slicer-service .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e SOURCE_CODE_URL=https://github.com/workpiece-gr/open-slicer-service \
  -e ALLOWED_ORIGINS=https://workpiece.gr,https://www.workpiece.gr \
  open-slicer-service
```

Check `http://localhost:8080/health`. It reports `profiles_ready: true` only after the machine profile and all ten material-specific process/filament profiles are present.

Slice a test STL:

```bash
curl -F file=@test-cube.stl \
  -F material=pla \
  -F quality=balanced \
  -F strength=functional \
  http://localhost:8080/v1/slice
```

Quality controls layer height; strength controls wall count. Infill remains fixed at 20% so the two controls stay independent.

Supported material keys are `pla`, `petg`, `pctg`, `abs`, and `tpu`.

| Setting | Result |
| --- | --- |
| Draft | 0.28 mm layer height |
| Balanced | 0.20 mm layer height |
| Fine | 0.12 mm layer height |
| Prototype | 2 walls |
| Functional | 3 walls |
| Load bearing | 5 walls |

## Experimental editable project 3MF (CP2b)

The `/v1/project` endpoint is the staged manufacturing-project builder. It remains separate from the public `/v1/slice` quote-preview path and is disabled by default. When enabled it requires a server-to-server bearer token (`WORKPIECE_PROJECT_API_TOKEN`) and serializes generation to one active Orca project build per service process. Until a distributed queue exists, deploy exactly one service replica if Workpiece must enforce one active project build globally.

It accepts one original STL plus quantity/material/quality/strength and:

1. inspects the original STL envelope,
2. chooses the smallest currently eligible Workpiece FDM machine when `printer=auto`,
3. asks OrcaSlicer to auto-orient, duplicate and auto-arrange the requested quantity,
4. enables automatic supports in the effective process profile,
5. exports an editable project `.3mf` **without slicing in the same Orca invocation**,
6. when `verify=true` (the default used by CI), opens that project in a fresh Orca process and slices all plates,
7. when `verify=false` (the Workpiece live review path), stops after project export/inspection so the request performs only one Orca invocation,
8. returns either the existing JSON/base64 test payload or, with `response_mode=binary`, the exact 3MF bytes plus compact validated metadata in response headers.

The temporary machine matrix is:

- `ender3_generic_235` — 235 × 235 × 235 mm Workpiece routing envelope, using OrcaSlicer 2.4.2's bundled generic Creality Ender-3 0.4 mm machine/process/filament presets with only the Workpiece envelope overridden. This is **not a calibrated production profile** and must be replaced when the real Ender profiles are supplied.
- `ratrig_vcore3_300` — existing Workpiece RatRig V-Core 3 300 / 0.4 mm profiles.

Ender auto-routing currently supports PLA, PETG, ABS and TPU from Orca's bundled generic Creality filaments. PCTG stays on the RatRig path until a suitable Ender profile is supplied.

Example:

```bash
curl -H "Authorization: Bearer $WORKPIECE_PROJECT_API_TOKEN" \
  -F file=@part.stl \
  -F material=pla \
  -F quality=balanced \
  -F strength=functional \
  -F quantity=6 \
  -F printer=auto \
  -F verify=false \
  -F response_mode=binary \
  http://localhost:8080/v1/project \
  -o workpiece-production.3mf
```

JSON/base64 remains available for regression testing. Binary mode does not construct the base64 payload; the production website streams the response into Workpiece-owned R2 storage with SHA-256 enforcement and validates the retained object metadata.

Real OrcaSlicer 2.4.2 Docker acceptance now covers Ender quantity, RatRig routing, PCTG fallback, fresh-process reopen/slice, and a 12-instance / 3-plate project. A desktop Orca acceptance check also opened and sliced the generated RatRig project successfully. Authentication, single-job concurrency control, binary handoff, profile hashes and website-owned durable artifact storage are staged in CP3. The temporary generic Ender profile remains the major machine-profile caveat, and desktop Orca inspection remains mandatory before printing.

## Publish to GitHub

1. Sign in to GitHub and open <https://github.com/new>.
2. Name the repository `open-slicer-service`, choose **Public**, and create it.
3. Open **Add file → Upload files** and drag in the unzipped project contents. Commit to `main`.
4. On the repository page, confirm the `LICENSE` is detected as AGPL-3.0.

## Legacy Railway deployment (retirement/rollback reference)

1. Sign in at <https://railway.com/> with GitHub.
2. Choose **New Project → Deploy from GitHub repo** and select `open-slicer-service`.
3. Add these variables in the service settings:

```text
SOURCE_CODE_URL=https://github.com/workpiece-gr/open-slicer-service
ALLOWED_ORIGINS=https://workpiece.gr,https://www.workpiece.gr
MAX_UPLOAD_BYTES=52428800
SLICE_TIMEOUT_SECONDS=180
MAX_PREVIEW_MOVES=30000
ENABLE_EXPERIMENTAL_PROJECT_API=1
WORKPIECE_PROJECT_API_TOKEN=<strong-random-server-to-server-secret>
PROJECT_QUEUE_TIMEOUT_SECONDS=30
```

4. Generate a public Railway domain and open `/health` on it.
5. Do not connect the live website until `profiles_ready` is true and a known test cube produces valid OrcaSlicer output.

## API

- `GET /health` — engine, licence/source and profile readiness
- `GET /source` — redirects to the deployed public source repository
- `POST /v1/slice` — multipart STL plus `material`, `quality` and `strength`; returns slicer summary and sampled extrusion moves
- `POST /v1/project` — experimental editable Orca project 3MF builder with quantity, auto machine routing, auto orientation/arrangement/supports and fresh-Orca reopen verification

The API never sends a job to a printer. Uploaded models live only in a per-request temporary directory and are deleted after the response. Add an edge rate limit before opening the endpoint to anonymous production traffic.

## AGPL deployment checklist

- Keep this wrapper, modifications, Dockerfile, profiles safe to redistribute, and the exact deployed source public under AGPL-3.0-or-later.
- Keep the OrcaSlicer version and download pinned.
- Keep the visible `/source` route and add the same source link beside the website slicer UI.
- Publish build and deployment instructions needed to run the corresponding source.
- Do not commit API keys, Railway secrets, customer models or private commercial data.
- Preserve upstream copyright and licence notices.

This is practical implementation guidance, not legal advice. Have counsel review the final boundary between this AGPL service and any proprietary site code before launch.

## Native process lifecycle

Slice, project export and fresh-project verification execute in isolated native
sessions under one shared compile reservation. Each native stage retains the
existing 180-second execution ceiling; the project reservation remains held
across export, validation and verification. Queue admission remains bounded by
`PROJECT_QUEUE_TIMEOUT_SECONDS` (30 seconds by default).

Linux subreaper support, PID start-time identities, pidfds and matching container
cgroup membership protect descendant termination. Timeout or request cancellation
sends TERM to verified owned processes, then escalates after one second and reaps
children. Successful parent exit also triggers descendant cleanup before artifact
acceptance. Native subprocesses do not receive the service authentication token.
Unverified containment poisons readiness and further admission and retains the
private job directory for diagnosis; it never publishes partial output or kills
unrelated processes. Container-local temporary storage bounds captured logs;
only their final 1 MiB is read into Python memory.

## Legacy API admission and upload protection

All `/v1/*` routes and detailed `/health` require the existing
`WORKPIECE_PROJECT_API_TOKEN` bearer credential. An outer ASGI gate checks that
credential before receiving the body or invoking multipart parsing. The website
must use the same protected service credential for both slicing and project
creation. Public `/healthz` returns only readiness (`ok`); the AGPL `/source` offer
and root information remain public. Detailed health remains available to
credentialed operators without waiting for manufacturing admission.

The service uses fixed, service-global limits, independent of client IP or
forwarding headers. Defaults are one active complete request (upload, parsing,
native work and response), two queued requests and a 30-second maximum queue
wait. This supplements the unchanged one-native-workload supervisor. Invalid
credentials receive 401; after a global 40-attempt burst, authentication failures
receive 429 (120 attempts/minute refill). Missing service credentials fail 503.

Authorized manufacturing admission defaults to 60 requests/minute with a burst
of 30, and 256 MiB/minute of actual incoming body bytes with a 128 MiB burst.
Multipart body size is at most the upload ceiling plus 1 MiB of overhead (51 MiB
at the default 50 MiB STL ceiling). Declared and actual lengths are both checked.
A complete body must arrive within 15 seconds, without resetting its deadline
per chunk. Duplicate/ambiguous body framing is rejected. Bodies are first spooled
into private automatically closed temporary files before multipart parsing, so
failed/oversized/disconnected uploads cannot leave parser-created upload files.
This temporarily requires up to twice the body size while the parser runs; the
existing 1 GiB container `/tmp` cap remains the hard storage boundary.

Bounded environment controls are `FDM_MAX_PENDING_REQUESTS` (0–4),
`FDM_ADMISSION_TIMEOUT_SECONDS` (1–30), `FDM_BODY_TIMEOUT_SECONDS` (1–30),
`FDM_REQUESTS_PER_MINUTE` (1–120), `FDM_REQUEST_BURST` (1–60),
`FDM_BYTES_PER_MINUTE` (1–512 MiB) and `FDM_BYTE_BURST` (1–256 MiB).
Invalid, nonfinite and excessive settings fail startup. `MAX_UPLOAD_BYTES` may be
reduced but cannot exceed 50 MiB. `MAX_STL_TRIANGLES` defaults to 1,000,000 and may
only be reduced; both native routes inspect STL geometry within that cap. ASCII
STL lines are limited to 4096 bytes and must decode as UTF-8. Geometry bounds and
profile/provenance algorithms remain unchanged for valid files within the caps.

Application failures use structured error codes: 429 for exhausted global
budgets/full queue, 503 for queue expiry or unavailable configuration/storage,
413 for body size, 408 for the body deadline and 499 for upload disconnect.
Capacity and private body files are released on every confirmed response,
rejection or cancellation. Native containment failures remain fail closed.

These limits are deliberately **single-process**: deploy the reviewed single
Uvicorn worker. Multiple replicas/workers would require separately reviewed
shared admission accounting. Authentication does not confer manufacturing
qualification; mandatory workshop review and nonauthoritative pricing remain.

Before FastAPI allocates multipart upload files, the pinned `python-multipart`
parser performs a no-file preflight over the bounded spool: at most one file
named `file`, 16 form fields, 8 KiB per part's headers, 8 KiB per field value,
a 200-byte boundary and a 255-byte filename. File bytes retain the configured
STL upload ceiling. Truncated/malformed forms and alternate URL-encoded form
bodies are rejected. The preflight itself has a finite five-second inspection
budget. Rejections request connection closure so unread rejected bodies cannot
occupy persistent connections. Existing transport connection/keep-alive bounds
must be configured by the reviewed launcher; this application limiter is not a
replacement for transport admission control.

Protected manufacturing responses have an absolute 15-second transfer deadline
starting with response headers; it is never reset for later chunks.
`FDM_RESPONSE_TIMEOUT_SECONDS` may be set only within 1–30 seconds. A stalled
partial response raises a connection-closing error rather than sending a second
status or pretending an incomplete artifact succeeded. Admission is released
only after application cancellation/finally cleanup has finished. A request that
waited in the queue also rechecks native containment readiness before consuming
its upload body.
