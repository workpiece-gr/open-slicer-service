# Open Slicer Service

An isolated HTTP wrapper around OrcaSlicer for manufacturing quotes and toolpath previews. The service accepts a single-colour STL, applies material plus independent quality and strength choices, runs a pinned OrcaSlicer build, and returns production-slicer estimates plus a sampled extrusion toolpath.

This repository is intentionally licensed under **GNU AGPL-3.0-or-later**. Keep the exact deployed source public and set `SOURCE_CODE_URL` to that public repository.

## Chosen hosting

- **Source:** a public GitHub repository named `open-slicer-service`
- **Container host:** Railway, using the included `Dockerfile`
- **Slicer:** OrcaSlicer 2.4.2, pinned by version and AppImage URL

Railway finds a root `Dockerfile` automatically. Use the Hobby plan for the pilot; OrcaSlicer is too large for a dependable 0.5 GB worker.

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
docker run --rm -p 8080:8080 \
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

The `/v1/project` endpoint is an **experimental manufacturing-project builder**. It is intentionally separate from the existing `/v1/slice` quote-preview path and is disabled by default. Set `ENABLE_EXPERIMENTAL_PROJECT_API=1` only in a controlled validation environment.

It accepts one original STL plus quantity/material/quality/strength and:

1. inspects the original STL envelope,
2. chooses the smallest currently eligible Workpiece FDM machine when `printer=auto`,
3. asks OrcaSlicer to auto-orient, duplicate and auto-arrange the requested quantity,
4. enables automatic supports in the effective process profile,
5. exports an editable project `.3mf` **without slicing in the same Orca invocation**,
6. opens that project in a fresh Orca process with no external profiles loaded,
7. slices all plates to prove that the project carries enough embedded configuration to reopen and slice,
8. returns the editable 3MF plus verification metadata.

The temporary machine matrix is:

- `ender3_generic_235` — 235 × 235 × 235 mm Workpiece routing envelope, using OrcaSlicer 2.4.2's bundled generic Creality Ender-3 0.4 mm machine/process/filament presets with only the Workpiece envelope overridden. This is **not a calibrated production profile** and must be replaced when the real Ender profiles are supplied.
- `ratrig_vcore3_300` — existing Workpiece RatRig V-Core 3 300 / 0.4 mm profiles.

Ender auto-routing currently supports PLA, PETG, ABS and TPU from Orca's bundled generic Creality filaments. PCTG stays on the RatRig path until a suitable Ender profile is supplied.

Example:

```bash
curl -F file=@part.stl \
  -F material=pla \
  -F quality=balanced \
  -F strength=functional \
  -F quantity=6 \
  -F printer=auto \
  http://localhost:8080/v1/project
```

The JSON response includes the project as base64 only for this experiment. A later production checkpoint should replace that transport with Workpiece-owned object storage/binary artifact handoff.

Real OrcaSlicer 2.4.2 Docker acceptance now covers Ender quantity, RatRig routing, PCTG fallback, fresh-process reopen/slice, and a 12-instance / 3-plate project. A desktop Orca acceptance check also opened and sliced the generated RatRig project successfully. The endpoint remains experimental because production authentication, concurrency/resource controls, durable artifact storage, calibrated Ender profiles, and full manufacturing provenance are still pending.

## Publish to GitHub

1. Sign in to GitHub and open <https://github.com/new>.
2. Name the repository `open-slicer-service`, choose **Public**, and create it.
3. Open **Add file → Upload files** and drag in the unzipped project contents. Commit to `main`.
4. On the repository page, confirm the `LICENSE` is detected as AGPL-3.0.

## Deploy on Railway

1. Sign in at <https://railway.com/> with GitHub.
2. Choose **New Project → Deploy from GitHub repo** and select `open-slicer-service`.
3. Add these variables in the service settings:

```text
SOURCE_CODE_URL=https://github.com/workpiece-gr/open-slicer-service
ALLOWED_ORIGINS=https://workpiece.gr,https://www.workpiece.gr
MAX_UPLOAD_BYTES=52428800
SLICE_TIMEOUT_SECONDS=180
MAX_PREVIEW_MOVES=30000
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
