# Profiles required

Place three JSON files exported or copied from the pinned OrcaSlicer version here:

- `machine.json` — the exact printer and nozzle profile
- `process.json` — the base process profile
- `filament.json` — the exact filament profile

The service copies and patches `process.json` per request. It changes only:

- `layer_height` from quality
- `wall_loops` from strength
- `sparse_infill_density` to `20%`

Do not use inherited fragments by themselves. Use complete/resolved profiles that slice a test cube successfully with OrcaSlicer 2.4.2. Profiles in this folder become part of the public corresponding source, so remove credentials, private notes and network printer secrets before committing them.
