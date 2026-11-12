# Open Slicer Service

An isolated HTTP wrapper around OrcaSlicer for manufacturing quotes, toolpath previews and editable production projects. The service accepts a single-colour STL, applies material plus independent quality and strength choices, runs a pinned OrcaSlicer build, and returns production-slicer estimates plus a sampled extrusion toolpath (`/v1/slice`) or an editable project 3MF for the workshop (`/v1/project`).

This repository is intentionally licensed under **GNU AGPL-3.0-or-later**. Keep the exact deployed source public and set `SOURCE_CODE_URL` to that public repository.

## One engine, two consumers

`/v1/slice` is the per-part **quote** path: fast, one STL, exact print time, filament mass, support mass and layer count from the real printer profile. `/v1/project` is the per-order **project** path: it chooses the printer, auto-orients, duplicates and arranges the quantity across plates, enables automatic supports and exports the editable 3MF the workshop opens in OrcaSlicer before printing. Both use the same printer list (`profiles/printers.json`), the same routing rule, the same profiles and the same automatic-support setting, so a quoted part and its delivered project land on the same printer with the same supports.

Printer choice is internal to the workshop. The Workpiece website never shows a printer to customers; it validates the `printer.key` a response reports against `GET /v1/printers`.

## One authentication point, one admission queue

`app/api_guard.py` is an ASGI layer in front of the application. It authenticates every `/v1/*` request and the detailed `/health` with the service bearer token (`WORKPIECE_PROJECT_API_TOKEN`) **before a single body byte is received**, then admits at most one manufacturing request at a time with a short pending queue, bounds the body (declared and actual length, deadline, multipart preflight) and the response transfer. There is no second token check and no second queue inside the application: `/v1/slice`, `/v1/project` and `/v1/printers` trust the guard. Behind the guard, `app/native_supervisor.py` runs the one OrcaSlicer process per request under Linux containment (pidfd signalling, verified PID identities, descendant reaping, fail-closed poisoning); its reservation is the workload interlock across export/verify, not a queue customers can wait in.

Public without a token: `/` (AGPL information), `/source` (the source offer) and `/healthz` (`{"ok": true}` or `503 SERVICE_NOT_READY`). `/health` (detailed: printers, queue, readiness) requires the token; this is the rule the Workpiece readiness check follows (it sends the bearer and expects `slice_authenticated`).

## Deployment paths

- **Source:** this public GitHub repository, with `SOURCE_CODE_URL` pointing to the deployed source for AGPL compliance and `SOURCE_COMMIT_SHA` set to the exact 40-hex commit of the running image.
- **Target host:** the Workpiece-owned Linux server; the container binds to loopback only and is launched by the website repository's manufacturing-service runbook (`scripts/run-manufacturing-container.sh`, systemd unit, cgroup slice).
- **Slicer:** OrcaSlicer 2.5.0 (`OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0.AppImage`), with the base-image digest and downloaded AppImage SHA-256 pinned in `Dockerfile`. The profiles in `profiles/` are fully flattened and carry no `inherits`: the 2.5.0 CLI resolves `inherits` against an installed vendor tree that the isolated per-request data directory never has (see `profiles/README.md`).

The service image is separate from the immutable qualified Authority-v2 image. A source rebuild must retain its own image digest; a matching Git commit or label alone does not prove identical runtime bytes.

## Configured printers

`profiles/printers.json` is the printer list. Each printer has `enabled`, `calibrated`, an envelope, a fit rule, its materials and its profile source. The service **refuses to start** when a printer is `enabled` without `calibrated`: an unmeasured profile must never produce a live quote or project.

| Key | State | Envelope | Profiles |
| --- | --- | --- | --- |
| `ratrig_vcore3_300` | enabled, calibrated | 300 × 300 × 300 mm, controlled 45-degree rod-fit check | `profiles/machine.json`, `profiles/process/*.json`, `profiles/filament/*.json` (RatRig V-Core 3 300 / 0.4 mm bundle) |
| `ender3_generic_235` | present, **disabled**, not calibrated | 235 × 235 × 235 mm | Was OrcaSlicer 2.4.2's bundled generic Creality Ender-3 0.4 mm presets with only the envelope overridden. OrcaSlicer 2.5.0 ships vendor presets only as binary `<Vendor>.opc` caches, so those JSON files no longer exist in the image and `/health` reports `project_3mf.ender_generic_profiles_ready: false`; a real future workshop printer that waits for a committed, calibrated profile set |

Routing (`routing.rule = smallest_capable`): with `printer=auto` the request goes to the smallest enabled printer whose material list and fit rule accept the part; ties break by envelope volume, then key. A disabled printer is never a candidate, even when requested explicitly (`PRINTER_DISABLED`). When the Ender is enabled later, small parts route to it and large ones to the RatRig; `GET /v1/printers` and `/health` show the list.

## Installed FDM profiles

```text
profiles/printers.json
profiles/machine.json
profiles/process/{pla,petg,pctg,abs,tpu}.json
profiles/filament/{pla,petg,pctg,abs,tpu}.json
```

Validate the resulting G-code in the OrcaSlicer desktop preview and with controlled test prints before treating it as production output. Each request slices one STL with one selected material.

## Local test

```bash
docker build -t open-slicer-service .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e SOURCE_CODE_URL=https://github.com/workpiece-gr/open-slicer-service \
  -e SOURCE_COMMIT_SHA=$(git rev-parse HEAD) \
  -e WORKPIECE_PROJECT_API_TOKEN=local-dev-token \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  open-slicer-service
```

Check `http://localhost:8080/healthz` (no token) for readiness, or `/health` with `-H "Authorization: Bearer local-dev-token"` for the detailed report: it shows `profiles_ready: true` only after the machine profile and all ten material-specific process/filament profiles are present, plus `printers`, `routing`, `queue` and `slice_authenticated`.

Slice a test STL (every `POST` needs the bearer token):

```bash
curl -H "Authorization: Bearer local-dev-token" \
  -F file=@test-cube.stl \
  -F material=pla \
  -F quality=balanced \
  -F strength=functional \
  -F preview=none \
  http://localhost:8080/v1/slice
```

Quality controls layer height; strength controls wall count. Infill remains fixed at 20% so the two controls stay independent. Supports are automatic tree supports, the same setting the project path uses, and the result reports `summary.support_grams` and `summary.supports_used` from the exact G-code roles.

Supported material keys are `pla`, `petg`, `pctg`, `abs`, and `tpu`.

| Setting | Result |
| --- | --- |
| Draft | 0.28 mm layer height |
| Balanced | 0.20 mm layer height |
| Fine | 0.12 mm layer height |
| Prototype | 2 walls |
| Functional | 3 walls |
| Load bearing | 5 walls |

Unit tests run without OrcaSlicer: `pip install -r requirements.txt pytest httpx && PYTHONPATH=. pytest -q` (the API tests use `tests/fake_orca.py` as the engine).

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORKPIECE_PROJECT_API_TOKEN` | empty (every `/v1/*` and `/health` request gets `503 SERVICE_AUTH_UNCONFIGURED`) | server-to-server bearer token checked by the ApiGuard for `/v1/slice`, `/v1/project`, `/v1/printers` and `/health`; the website sends it as `WORKPIECE_FDM_SLICER_TOKEN` |
| `ENABLE_EXPERIMENTAL_PROJECT_API` | `0` | enables `/v1/project` |
| `FDM_MAX_PENDING_REQUESTS` (0–4), `FDM_ADMISSION_TIMEOUT_SECONDS` (1–30) | `2`, `30` | ApiGuard admission: one active request plus this many pending; a pending request that waits longer gets `503 ADMISSION_QUEUE_TIMEOUT`, one beyond the queue `429 ADMISSION_QUEUE_FULL` |
| `FDM_BODY_TIMEOUT_SECONDS`, `FDM_RESPONSE_TIMEOUT_SECONDS` (1–30) | `15`, `15` | whole-body receive deadline (`408 REQUEST_BODY_TIMEOUT`) and absolute response transfer deadline |
| `FDM_REQUESTS_PER_MINUTE` (1–120), `FDM_REQUEST_BURST` (1–60), `FDM_BYTES_PER_MINUTE` (1–512 MiB), `FDM_BYTE_BURST` (1–256 MiB) | `60`, `30`, `256 MiB`, `128 MiB` | service-global request and upload-byte budgets (`429 SERVICE_BUDGET_EXHAUSTED`); 40 failed authentications then `429 AUTH_ATTEMPT_BUDGET_EXHAUSTED` |
| `QUEUE_TIMEOUT_SECONDS` (alias `PROJECT_QUEUE_TIMEOUT_SECONDS`) | `30` | wait for the native reservation behind the guard (`503 NATIVE_QUEUE_BUSY`) |
| `SLICE_TIMEOUT_SECONDS` (1–180) | `180` | per OrcaSlicer invocation (`504 SLICE_TIMEOUT`) |
| `MAX_UPLOAD_BYTES` (84 B–50 MiB) | 50 MiB | STL upload limit; the guard allows 1 MiB of multipart overhead on top |
| `MAX_STL_TRIANGLES` (1–1 000 000) | 1 000 000 | STL complexity cap on both routes; ASCII STL lines are limited to 4096 bytes and must be UTF-8 (`400 SOURCE_INVALID`) |
| `MAX_PREVIEW_MOVES` | `30000` | sampled toolpath size for `preview=moves` |
| `MAX_PROJECT_QUANTITY`, `MAX_PROJECT_BYTES` | `50`, 80 MiB | project limits |
| `PRINTERS_FILE` | `$PROFILE_ROOT/printers.json` | printer list override |
| `SOURCE_CODE_URL`, `SOURCE_COMMIT_SHA` | empty | AGPL source offer and provenance reported in every response |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS for direct browser use (the website calls server-to-server) |

## Experimental editable project 3MF (CP2b)

The `/v1/project` endpoint is the staged manufacturing-project builder. It is disabled by default and sits behind the same ApiGuard token and admission queue as `/v1/slice`. One OrcaSlicer process runs at a time per service process; the native reservation is held across export, inspection and verification. The guard's limits are deliberately **single-process**: deploy the reviewed single Uvicorn worker, never several replicas without separately reviewed shared admission accounting.

It accepts one original STL plus quantity/material/quality/strength and:

1. inspects the original STL envelope,
2. routes to the smallest enabled printer that fits when `printer=auto` (same rule as `/v1/slice`),
3. asks OrcaSlicer to auto-orient, duplicate and auto-arrange the requested quantity,
4. enables automatic supports in the effective process profile,
5. exports an editable project `.3mf` **without slicing in the same Orca invocation**,
6. when `verify=true` (the default used by CI), opens that project in a fresh Orca process and slices all plates, reporting `plate_count`, `total_print_time_seconds` and `total_filament_grams`,
7. when `verify=false`, stops after project export/inspection so the request performs only one Orca invocation,
8. returns either the JSON/base64 payload or, with `response_mode=binary`, the exact 3MF bytes plus compact validated metadata in response headers.

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

JSON/base64 remains available for regression testing. Binary mode does not construct the base64 payload; the production website streams the response into Workpiece-owned storage with SHA-256 enforcement and validates the retained object metadata. The editable 3MF is opened and reviewed in OrcaSlicer desktop by the workshop before anything is printed.

## API

- `GET /healthz` — minimal public readiness (`{"ok": true}` or `503 SERVICE_NOT_READY`), no token
- `GET /health` — detailed health: engine, licence/source and profile readiness, `slice_authenticated`, configured printers, routing rule, admission queue and native slot state (token)
- `GET /source` — redirects to the deployed public source repository (no token)
- `GET /v1/printers` — the configured printer list without file paths (token)
- `POST /v1/slice` — multipart STL plus `material`, `quality`, `strength`, optional `preoriented`, `printer` (`auto` default) and `preview` (`moves` default, `none` to skip the sampled toolpath); returns engine and profile provenance, the selected printer, slicer summary with support mass, extrusion by feature role, sampled extrusion moves and warnings (token)
- `POST /v1/project` — editable Orca project 3MF builder with quantity, auto machine routing, auto orientation/arrangement/supports and fresh-Orca reopen verification (token, `ENABLE_EXPERIMENTAL_PROJECT_API=1`)

Every refusal returns `{"detail": {"code": "...", "detail": "..."}}` (guard refusals also carry `message`, the same text).

- Guard, before the body is read: `SERVICE_AUTH_REQUIRED` (401, `WWW-Authenticate: Bearer`, `Connection: close`), `SERVICE_AUTH_UNCONFIGURED` (503), `AUTH_ATTEMPT_BUDGET_EXHAUSTED` / `SERVICE_BUDGET_EXHAUSTED` / `ADMISSION_QUEUE_FULL` (429, `Retry-After: 30`), `ADMISSION_QUEUE_TIMEOUT` / `SERVICE_UNAVAILABLE` / `UPLOAD_STORAGE_UNAVAILABLE` (503), `UNSUPPORTED_BODY_TYPE` (415), `INVALID_BODY_FRAMING` / `INVALID_MULTIPART` (400), `REQUEST_BODY_TOO_LARGE` / `MULTIPART_PART_LIMIT` / `MULTIPART_PART_TOO_LARGE` / `MULTIPART_HEADERS_TOO_LARGE` (413), `REQUEST_BODY_TIMEOUT` / `MULTIPART_PARSE_TIMEOUT` (408), `REQUEST_CANCELLED` (499).
- Application: `PROJECT_DISABLED` (404), `INVALID_PARAMETER` (422), `UNSUPPORTED_MEDIA` (415), `UPLOAD_TOO_LARGE` (413), `UPLOAD_EMPTY` / `SOURCE_INVALID` (400; also the triangle and ASCII-line limits), `PRINTER_UNKNOWN` / `PRINTER_DISABLED` / `MATERIAL_UNSUPPORTED` / `NO_PRINTER_FITS` (422), `ENGINE_MISSING` / `PROFILES_MISSING` (503), `SLICE_TIMEOUT` (504, `native_code: NATIVE_TIMEOUT`), `SLICE_FAILED` (422, with `engine_result` and `diagnostic` where available), `PROJECT_TOO_LARGE` / `PROJECT_INVALID` (422).
- Native supervisor (503, with `native_code`): `NATIVE_QUEUE_BUSY`, `NATIVE_UNAVAILABLE`, `NATIVE_START_FAILED`, `NATIVE_CONTAINMENT_FAILED`, `NATIVE_CLEANUP_FAILED`. A containment failure poisons the service: `/health` and `/healthz` turn 503, admission is refused and the private job directory is retained for diagnosis until an operator reconciles the container.

## Native process lifecycle

Slice, project export and fresh-project verification execute in isolated native sessions under one reservation. Each native stage keeps the `SLICE_TIMEOUT_SECONDS` ceiling (at most 180 s); the project reservation is held across export, validation and verification. Linux subreaper support, PID start-time identities, pidfds and matching container cgroup membership protect descendant termination. Timeout or client disconnect sends TERM to verified owned processes, escalates after one second and reaps children; a successful parent exit also triggers descendant cleanup before artifact acceptance. Native subprocesses do not receive the service token. Only the final 1 MiB of each captured stream is read into Python memory.

The API never sends a job to a printer. Uploaded models live only in a per-request temporary directory and are deleted after the response (retained only after a containment failure, for diagnosis).

## AGPL deployment checklist

- Keep this wrapper, modifications, Dockerfile, profiles safe to redistribute, and the exact deployed source public under AGPL-3.0-or-later.
- Keep the OrcaSlicer version and download pinned.
- Keep the visible `/source` route and add the same source link beside the website slicer UI.
- Publish build and deployment instructions needed to run the corresponding source.
- Do not commit API keys, service tokens, customer models or private commercial data.
- Preserve upstream copyright and licence notices.

This is practical implementation guidance, not legal advice. Have counsel review the final boundary between this AGPL service and any proprietary site code before launch.
