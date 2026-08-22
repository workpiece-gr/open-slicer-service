# Resolved RatRig profiles

These profiles resolve the uploaded `RatRig V-Core 3 300 0.4 nozzle - Copy` OrcaSlicer bundle against the matching upstream OrcaSlicer 2.4.2 RatRig and System bases.

- `machine.json` — RatRig V-Core 3, 300 × 300 × 300 mm, 0.4 mm nozzle
- `process/{pla,petg,pctg,abs,tpu}.json` — material-specific base process settings
- `filament/{pla,petg,pctg,abs,tpu}.json` — material-specific filament settings

The service copies the selected process profile per request and changes only:

- `layer_height` from quality
- `wall_loops` from strength
- `sparse_infill_density` to `20%`

All inherited values have been flattened so the container does not depend on OrcaSlicer desktop user data. The `inherits` strings remain as identity metadata because OrcaSlicer's CLI uses the machine marker for compatibility checks; no external preset lookup is required. Printer-host fields were removed before publication. Validate the generated G-code with a controlled print before using these settings for customer parts.
