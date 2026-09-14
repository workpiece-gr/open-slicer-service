# Physical machine dossier: RATRIG-VCORE3-300-01

Status: **qualification preparation only**
Printer key: `ratrig_vcore3_300`
Machine asset ID: `RATRIG-VCORE3-300-01`
Recorded: 2026-09-14
Source: read-only Moonraker/RatOS inspection plus workshop-owner observations

This record identifies the physical printer intended for the first
`workpiece-ratrig-vcore3-300-qualification-v1` run. It is not qualification
evidence and does not make any material/profile lane production-ready.

## Machine and motion system

- RatRig V-Core 3, 300 mm variant.
- CoreXY kinematics.
- Configured working envelope: 300 x 300 x 300 mm.
- Configured motion limits: 800 mm/s XY velocity, 10,000 mm/s2 XY
  acceleration, 50 mm/s Z velocity, 600 mm/s2 Z acceleration, and 5 mm/s
  square-corner velocity.
- X/Y drive: TMC2209 drivers with 40 mm rotation distance.
- Z drive: three TMC2209-controlled Z motors with 4 mm rotation distance.
- Main controller is identified by RatOS as a BTT Octopus Pro 446.
- Host computer: Raspberry Pi 4 Model B Rev 1.5, four-core 32-bit environment,
  running Raspbian GNU/Linux 11.

Configured limits describe the machine configuration; they are not a
recommendation to print qualification fixtures at those maxima.

## Toolhead and extrusion

- Installed nozzle diameter: 0.4 mm.
- Configured filament diameter: 1.75 mm.
- Orbiter 2 extruder, configured rotation distance 4.63 mm.
- UHF hotend configuration with CHT nozzle indicated by the active RatOS
  variables.
- Extruder maximum configured temperature: 300 C.
- Orbiter 2 filament sensor configuration enables insertion, runout and clog
  detection.
- Workshop owner reports extruder rotation/e-steps calibrated and the normal
  PLA settings working reliably.

## Bed, probing and calibration

- Bed surface: textured PEI flexible build plate.
- Heated-bed maximum configured temperature: 140 C.
- Beacon probing and adaptive bed-mesh/heat-soak configuration are enabled.
- Workshop owner reports homing, heaters, first layers and automatic levelling
  operating correctly.
- Input shaping: MZV, X 79.8 Hz and Y 49.2 Hz.
- Pressure advance in the active `printer.cfg`: 0.04. RatOS base configuration
  also contains a 0.03 default; the active printer override is the value to
  use when identifying this snapshot.
- A retained RatOS input-shaper artifact exists as
  `belt-tension-resonances-2026-02-27-130405.png`, 60,970 bytes, SHA-256
  `f66f33fe2880ff499b178789a0f5c616b69c6b67801bf228b833b22e74e34ae8`.

## Firmware and control software observed

- RatOS/Klipper branch: `ratos/v2.1.x`.
- Klipper: `v0.12.0-402`.
- Moonraker: `v0.9.3-0-g71f9e67`.
- RatOS theme: `v2.1.0-0`.
- RatOS configurator observed locally at `v2.1.0-0`; a newer `v2.1.2-0`
  revision was visible but had not been applied during this snapshot.
- Mainsail: `v2.18.2`.
- KlipperScreen: `v0.4.4-20`.
- At inspection time Klippy reported `ready`, with no configuration warnings.

Do not update firmware/configuration during a qualification run set. Any
material configuration change requires a new snapshot identity and may require
repeating the lane.

## Redacted configuration identity

The source configuration files are intentionally not copied into this public
repository. The hashes below permit later comparison without publishing pin,
network or other machine-local configuration details.

| Active file | Bytes | SHA-256 |
| --- | ---: | --- |
| `printer.cfg` | 24,325 | `376189440779b6c13c0480cdc3a471634d603142e9c1cf4cef8f973a5c424d08` |
| `RatOS.cfg` | 14,400 | `c864fecf49a024349a7ad723cc63c9f1b4e4113e6be2b1d0d69caeab3271a137` |
| `Orbiter2_SmartSensor.cfg` | 15,862 | `e7d893f9138db024589b6ad02aaa2121fcda07f3230a6a58487b99e3f55af5d2` |
| `ratos-variables.cfg` | 228 | `d344f0a001dcb9276e216efad14612613c588a5b03fee55eef86a22449c6ce4d` |

Configuration-manifest SHA-256:
`3cc309fba28312bc3f9ac1884608b3237da38ec7157bbf889462648af40bc414`

The manifest hash is SHA-256 over the four rows above sorted by filename and
serialized as `filename<TAB>bytes<TAB>sha256`, separated by LF. This is a
Workpiece evidence identifier, not a RatOS-native checksum.

Historical `printer-*` and `RatOS-*` backups were visible in the Machine
configuration directory. They were not included because the qualification must
bind the active configuration, not obsolete snapshots. Camera, UI, timelapse,
Moonraker and theme files were likewise excluded because they do not define the
manufacturing profile.

## Initial material lane

The first physical test material is deliberately a non-production test spool:

- Manufacturer/product: GST3D PLA+.
- Colour: marble (grey with black specks).
- Lot/spool identifier: not available.
- Conditioning: not dried before this run.
- Role: expendable test material for initial qualification exercises.

This spool can only provide evidence for its exact recorded material state. A
pass with it must **not** qualify future eSUN material.

The intended later production PLA is eSUN PLA+ in black or white. Each colour
and exact production profile must be separately identified and deliberately
qualified before being represented as production-ready.

## Frozen and pending acceptance information

Workshop-owner criteria already declared for the first lane:

- External dimensional tolerance: +/-0.2 mm.
- Internal-hole tolerance: +/-0.3 mm.
- Five-copy repeatability: maximum measured range 0.1 mm.

The following remain pending and therefore the complete acceptance criteria are
**not yet frozen**:

- predicted-versus-actual time variance;
- predicted-versus-actual material variance;
- complete visual-defect pass/fail rules;
- support-removal pass/fail rules;
- exact Authority-v2 machine/process/filament profile hashes and runtime;
- immutable candidate package identity for Q1;
- UTC time and reviewer identity freezing the criteria.

No qualification print should begin until these remaining fields are completed
and `acceptanceCriteria.frozenBeforeRun` is explicitly set to `true` in the
working evidence record.

## Change-control note

This dossier describes one observed physical/configuration state. Re-snapshot
and assess whether qualification must be repeated after changes to the nozzle,
extruder calibration, hotend, build surface, motion system, input shaping,
pressure advance, firmware/configuration, or Authority machine/process/filament
profiles.
