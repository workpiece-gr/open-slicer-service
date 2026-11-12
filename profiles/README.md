# Resolved RatRig profiles

These profiles resolve the uploaded `RatRig V-Core 3 300 0.4 nozzle - Copy` OrcaSlicer bundle against the matching upstream OrcaSlicer 2.4.2 RatRig and System bases, and are sliced by the pinned OrcaSlicer 2.5.0.

- `machine.json` — RatRig V-Core 3, 300 × 300 × 300 mm, 0.4 mm nozzle (upstream parent: `RatRig V-Core 3 300 0.4 nozzle`)
- `process/{pla,petg,pctg,abs,tpu}.json` — material-specific base process settings (upstream parent: `0.20mm Standard @RatRig`)
- `filament/{pla,petg,pctg,abs,tpu}.json` — material-specific filament settings (upstream parents: `Generic PLA @System`, `Generic PETG @System`, `Generic ABS @System` for PCTG and ABS, `RatRig Generic TPU`)

The service copies the selected process profile per request and changes only:

- `layer_height` from quality
- `wall_loops` from strength
- `sparse_infill_density` to `20%`
- `initial_layer_line_width` from the profile's percentage to the same width in millimetres (`110%` of the 0.4 mm nozzle becomes `0.44`). OrcaSlicer's CLI arranger (`get_real_skirt_dist`, 2.4.2 and 2.5.0) reads this value with `opt_float`, which returns the bare percentage number: `110%` counts as 110 mm per skirt loop, the arrange bed shrinks by `skirt_distance + skirt_loops × 110 = 222 mm` per side, and any part longer than about 144 mm fails with exit -50 ("no object is fully inside the print volume") although it fits the 300 mm bed. The physical line width is unchanged; `line_width` and the other percentage widths stay as calibrated.

All inherited values are flattened so the container does not depend on OrcaSlicer desktop user data. The profiles carry **no `inherits` key**: OrcaSlicer 2.5.0's CLI resolves `inherits` against a vendor tree installed in its data directory (`--load-settings` fails with "can not resolve preset" when that tree is absent, which is always the case in the isolated per-request data directory), and the 2.5.0 AppImage ships vendor presets only as binary `<Vendor>.opc` caches. The former parent names are recorded above. `compatible_printers` names this machine preset by its own name (`RatRig V-Core 3 300 0.4 nozzle - Copy`): when a project 3MF is reopened, the 2.5.0 CLI compares the embedded process's `compatible_printers` with the embedded machine's inherited name, or with its own name when there is no `inherits`, and refuses to slice (`CLI_PROCESS_NOT_COMPATIBLE`, -17) otherwise. Keys that 2.4.2 filled from the parent chain and that 2.5.0 dereferences without a default are set explicitly (`filament_diameter` 1.75 mm). `enable_arc_fitting` uses the 2.5.0 enum value `disabled` (formerly `0`); `silent_mode` was removed because 2.5.0 ignores it. The effective merged settings of every material were exported with `--export-settings` under 2.4.2 and 2.5.0 and differ only in those renamed/removed keys and in 2.5.0's new per-extruder vector form of the same speed, acceleration and jerk values.

Printer-host fields were removed before publication. Validate the generated G-code with a controlled print before using these settings for customer parts.
