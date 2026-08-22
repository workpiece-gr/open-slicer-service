# Open Slicer Service

An isolated HTTP wrapper around OrcaSlicer for manufacturing quotes and toolpath previews. The service accepts a single-colour STL, applies independent quality and strength choices, runs a pinned OrcaSlicer build, and returns production-slicer estimates plus a sampled extrusion toolpath.

This repository is intentionally licensed under **GNU AGPL-3.0-or-later**. Keep the exact deployed source public and set `SOURCE_CODE_URL` to that public repository.

## Chosen hosting

- **Source:** a public GitHub repository named `open-slicer-service`
- **Container host:** Railway, using the included `Dockerfile`
- **Slicer:** OrcaSlicer 2.4.2, pinned by version and AppImage URL

Railway finds a root `Dockerfile` automatically. Use the Hobby plan for the pilot; OrcaSlicer is too large for a dependable 0.5 GB worker.

## Before the first real slice

Export or copy one tested machine, process and filament JSON profile from the same OrcaSlicer version and save them as:

```text
profiles/machine.json
profiles/process.json
profiles/filament.json
```

Start with the exact printer, 0.4 mm nozzle and filament you will quote. Validate the resulting G-code in the OrcaSlicer desktop preview and with a controlled test print before treating it as production output. The initial service is intentionally limited to one STL and one filament.

## Local test

```bash
docker build -t open-slicer-service .
docker run --rm -p 8080:8080 \
  -e SOURCE_CODE_URL=https://github.com/YOUR-NAME/open-slicer-service \
  -e ALLOWED_ORIGINS=https://YOUR-SITE.example \
  open-slicer-service
```

Check `http://localhost:8080/health`. It reports `profiles_ready: true` only after all three profile files are present.

Slice a test STL:

```bash
curl -F file=@test-cube.stl \
  -F quality=balanced \
  -F strength=functional \
  http://localhost:8080/v1/slice
```

Quality controls layer height; strength controls wall count. Infill remains fixed at 20% so the two controls stay independent.

| Setting | Result |
| --- | --- |
| Draft | 0.28 mm layer height |
| Balanced | 0.20 mm layer height |
| Fine | 0.12 mm layer height |
| Prototype | 2 walls |
| Functional | 3 walls |
| Load bearing | 5 walls |

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
SOURCE_CODE_URL=https://github.com/YOUR-NAME/open-slicer-service
ALLOWED_ORIGINS=https://YOUR-SITE.example
MAX_UPLOAD_BYTES=52428800
SLICE_TIMEOUT_SECONDS=180
MAX_PREVIEW_MOVES=30000
```

4. Generate a public Railway domain and open `/health` on it.
5. Do not connect the live website until `profiles_ready` is true and a known test cube matches the OrcaSlicer desktop result.

## API

- `GET /health` — engine, licence/source and profile readiness
- `GET /source` — redirects to the deployed public source repository
- `POST /v1/slice` — multipart STL plus `quality` and `strength`; returns slicer summary and sampled extrusion moves

The API never sends a job to a printer. Uploaded models live only in a per-request temporary directory and are deleted after the response. Add an edge rate limit before opening the endpoint to anonymous production traffic.

## AGPL deployment checklist

- Keep this wrapper, modifications, Dockerfile, profiles safe to redistribute, and the exact deployed source public under AGPL-3.0-or-later.
- Keep the OrcaSlicer version and download pinned.
- Keep the visible `/source` route and add the same source link beside the website slicer UI.
- Publish build and deployment instructions needed to run the corresponding source.
- Do not commit API keys, Railway secrets, customer models or private commercial data.
- Preserve upstream copyright and licence notices.

This is practical implementation guidance, not legal advice. Have counsel review the final boundary between this AGPL service and any proprietary site code before launch.
