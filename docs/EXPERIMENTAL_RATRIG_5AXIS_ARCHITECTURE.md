# Experimental RatRig 3Z Five-Axis Architecture

Status: **R&D architecture only — not production authority**

Date: 2026-09-20

Target baseline: Workpiece RatRig V-Core 3 300, 0.4 mm nozzle, current production profile key `ratrig_vcore3_300`.

This document defines a parallel experimental path for non-planar / five-axis FFF. It must not change the existing `/v1/project` path, Authority-v2 qualified runtime, production machine profile, or existing qualification evidence.

## 1. Decision

Build the first prototype around a **triple-Z tilting-bed machine** and an external non-planar toolpath engine derived from / interoperable with AtomSlicer.

Do **not** begin with a deep OrcaSlicer fork.

The architecture is:

```text
STL
 |
 v
5X planning engine
  - non-planar layers
  - orientation field
  - continuous toolpaths
  - collision checks
 |
 v
tool-space path
  p = X,Y,Z
  n = nx,ny,nz
  deposition / extrusion / feed
 |
 v
3Z machine adapter
  inverse kinematics
  envelope checks
  bed / gantry clearance
 |
 v
physical machine path
  X,Y,Z,U,V,E,F
 |
 v
firmware
 |
 v
RatRig triple-Z bed
```

Orca can later be used as the operator-facing shell for profiles, model selection, parameters, launch/export and preview integration.

## 2. Why this matches the RatRig

The public 3Z RatRig research implementation modifies a RatRig V-Core 3.x so the three independently driven Z supports are no longer constrained to remain coplanar and horizontal. Each support uses a sliding / articulating connection, allowing the three support heights to define bed pitch and roll while CoreXY continues to supply X/Y.

The reference implementation publishes both modified CAD and firmware:

- https://github.com/VincentBELLEI/3Zkinematic-Ratcurve
- https://github.com/iota97/AtomSlicer

AtomSlicer already contains an inverse-kinematics implementation for this exact class of machine and emits coordinated `X Y Z U V E` moves.

### Important correction to the initial concept

The reference 3Z machine is **not Klipper-based**. It uses RepRapFirmware and dynamically switches the three physical Z motors between:

1. ordinary coupled Z operation; and
2. independent `Z/U/V` axes for five-axis printing.

That is a known-working firmware architecture and should be the lowest-risk first physical prototype.

Current Klipper can synchronize additional G-code axes, so a Klipper implementation is plausible, but recreating the same runtime three-motor mode switch cleanly is additional firmware/kinematics work.

## 3. Existing Workpiece production machine remains separate

The current Workpiece machine profile is:

- RatRig V-Core 3 300;
- 300 x 300 mm nominal XY area;
- 300 mm nominal Z height;
- 0.4 mm nozzle;
- Klipper G-code flavor;
- ordinary triple-Z behavior;
- immutable Authority-v2 profile / runtime qualification chain.

The five-axis machine must receive a **new identity**, for example:

`ratrig_vcore3_300_3z5x_exp`

No existing qualification receipt, evidence, process hash, or production-ready flag may be inherited from `ratrig_vcore3_300`.

Changing the bed supports, available envelope, firmware, homing model, nozzle clearance or motion semantics creates a different manufacturing machine.

## 4. Reference 3Z geometry

AtomSlicer's current reference machine contains these machine constants:

```text
bed support 0 XY  = (-4.07, -12.16) mm
bed support 1 XY  = (304.93, -12.16) mm
bed support 2 XY  = (150.43, 296.84) mm

support rail angles = +29.89°, -29.89°, 90°
support ball Z      = -45.7 mm
Z working offset    = 75 mm

maximum tilt        = 30°
machine X limit     = 300 mm
machine Y limit     = 293 mm
Z/U/V upper limit   = 280 mm

bed-corner margin   = 10 mm
nozzle-to-gantry
clearance model     = 70 mm
```

These values are evidence for the **published reference machine**, not automatically valid dimensions for the Workpiece RatRig. Our implementation must move them into a machine manifest and verify them against the physical machine before any powered five-axis motion.

## 5. Mechanical conversion

The minimum mechanical conversion is not simply “command the three existing Z motors independently.” A normal RatRig bed assembly is constrained for horizontal travel and can bind if the three supports are driven far out of plane.

The conversion therefore needs:

### 5.1 Articulating three-point bed support

Use the published 3Z concept as the baseline:

- three spherical / ball-type bed attachment points;
- each attachment allowed to translate along its required planar rail direction;
- modified Z brackets based on the published U/V/Z bracket geometry;
- sufficient linear-rail and leadscrew travel for the intended tilt;
- no rigid fourth constraint.

The reference repository contains modified U, V and Z bracket STEP/STL files plus a cushion component. Those should be imported as reference CAD and compared against the exact Workpiece machine revision before fabrication.

### 5.2 Conservative first tilt envelope

Although the research implementation allows approximately 30°, the Workpiece prototype should start with a software limit of **10°**.

Progression:

```text
2° motion validation
5° metrology validation
10° first printing envelope
15–20° later experimental expansion
30° only after mechanical, thermal and collision evidence
```

The mechanical structure may be designed to accommodate the larger eventual range, but software should fail closed above the currently validated range.

### 5.3 Hotend / gantry clearance

Tilting the bed raises one or more bed corners toward the gantry. Clearance depends on:

- nozzle protrusion below the carriage;
- hotend / duct shape;
- probe;
- bed thickness and heater;
- carriage geometry;
- actual support pivot height;
- cable routing.

The reference AtomSlicer model explicitly checks the maximum transformed bed-corner height against a nozzle-to-gantry clearance.

Workpiece must replace that scalar approximation with a measured machine envelope and later, ideally, a simple collision mesh for:

- toolhead;
- probe;
- ducts;
- gantry;
- bed;
- clamps / magnetic sheet;
- Z support hardware.

A longer nozzle or revised toolhead may be required. This must be decided from measured CAD/clearance, not assumed.

### 5.4 Heated-bed wiring

The bed heater, thermistor and ground wiring must tolerate repeated pitch/roll without:

- tensile loading;
- rubbing against extrusion or Z screws;
- bending at one fixed conductor point;
- entering the toolhead envelope.

Add deliberate service loops and strain relief. Five-axis motion validation must initially be performed with heaters disabled.

### 5.5 Hard mechanical safety

Add physical travel/tilt stops where practical so a software or homing fault cannot drive the bed into the gantry.

## 6. Firmware strategy

### Phase A — reference-compatible RepRapFirmware prototype

Use the published architecture as the first physical proof.

Normal mode:

```text
Z = Z0 + Z1 + Z2 coupled
```

Five-axis mode:

```text
Z -> motor 0
U -> motor 1
V -> motor 2
```

The reference `enable3Z.g` dynamically remaps the three motors with `M584`, applies independent axis limits and establishes a common Z/U/V offset. `disable3Z.g` returns the motors to one coupled Z axis.

We should reproduce the behavior conceptually but regenerate pin mapping, current, steps/mm, endstops, limits and safe positions from the Workpiece hardware.

Do not copy the reference electrical values blindly.

### Phase B — Klipper port

Retaining Klipper is desirable for eventual Workpiece integration, but it is not necessary to prove the mechanical/slicing concept.

Current Klipper supports synchronized extra G-code axes through extra/manual steppers. The remaining engineering problem is clean ownership of the same three Z motors between:

- conventional triple-Z homing/tilt operation; and
- independent 3Z deposition motion.

Preferred long-term solution: a small dedicated Klipper kinematics/extras implementation representing the three support carriages explicitly, rather than a fragile macro/post-processing hack.

Do not sacrifice ordinary homing, Z-tilt safety or recovery semantics merely to avoid the firmware-port work.

## 7. Slicer architecture

### 7.1 AtomSlicer is the starting engine, not the finished product

Reuse / study from AtomSlicer:

- field-aligned non-planar slicing;
- constant-thickness layer generation;
- tool-orientation objectives;
- continuous toolpath generation;
- infill strategies;
- collision-angle logic;
- tool-space waypoint representation;
- 3Z inverse kinematics;
- machine-envelope lift calculation;
- 5-axis G-code generation.

Do not retain these as hard-coded RatRig constants.

Refactor the machine-specific layer into data-driven configuration.

### 7.2 Canonical intermediate toolpath

The Workpiece 5X engine should define a machine-independent waypoint contract similar to:

```json
{
  "position_mm": [120.0, 80.0, 24.3],
  "tool_normal": [0.10, -0.14, 0.985],
  "deposition": true,
  "extrusion_mm": 0.031,
  "world_speed_mm_s": 12.0
}
```

A toolpath is a sequence of these waypoints plus:

- nozzle width;
- nominal layer thickness;
- filament diameter;
- temperature/fan events;
- provenance;
- source model hash;
- planner version;
- orientation objective;
- collision policy.

This keeps the difficult geometric planner independent of RatRig-specific motor coordinates.

### 7.3 3Z machine adapter

Input:

`(X,Y,Z,nx,ny,nz)`

Output:

`(Xmachine,Ymachine,Z0,Z1,Z2)`

Responsibilities:

- inverse kinematics;
- support rail geometry;
- global placement / lift;
- tilt limit;
- motor travel limits;
- bed-corner / gantry clearance;
- transformed printable envelope;
- feedrate mapping;
- segment subdivision;
- continuity checks;
- singularity / impossible-pose rejection.

The physical-axis names `Z/U/V` belong to the postprocessor, not the planner.

### 7.4 Segment interpolation

This is safety-critical.

A path that looks like a short smooth move in tool space can create substantially different motion on the three Z actuators.

Before G-code export, subdivide paths so that:

- positional chord error stays below a configured tolerance;
- tool-normal angular change per segment stays below a configured tolerance;
- no Z support exceeds per-segment motion limits;
- every interpolated pose is envelope-valid.

Validation must sample the entire segment, not only its endpoints.

## 8. OrcaSlicer integration

Orca's newer-than-2.4.2 / Nightly plugin system exposes slicing-pipeline hooks and the live slicing graph.

That makes an Orca plugin useful as a **front end**, but the first implementation should not attempt to force continuous 5X path generation into Orca's existing planar pipeline.

Initial plugin responsibilities:

1. obtain/export the selected model;
2. read selected printer / filament / relevant process values;
3. expose 5X-specific controls;
4. invoke the Workpiece 5X engine;
5. show diagnostics and rejected-pose information;
6. export the generated five-axis G-code;
7. later import/render a preview representation if Orca APIs permit it cleanly.

Because Workpiece production is pinned to Orca 2.4.2 and the plugin API is newer, use a **separate experimental Orca installation**.

Never update the production Authority image merely to get plugin support.

## 9. Recommended repository boundary

### New repository: `workpiece-gr/ratrig-5x-engine`

Suggested tree:

```text
CMakeLists.txt
LICENSES/
docs/
  ARCHITECTURE.md
  MECHANICAL-CONVERSION.md
  VALIDATION-PROTOCOL.md
machines/
  ratrig_vcore3_300_3z5x_exp.yaml
src/
  planner/
    atomslicer_adapter/
  toolpath/
    waypoint.*
    toolpath_io.*
  kinematics/
    three_z.*
  validation/
    envelope.*
    collision.*
    interpolation.*
  post/
    reprap_3z.*
    klipper_3z.*
  cli/
tests/
  kinematics/
  envelope/
  golden/
vendor/
  atomslicer/
```

Pin the exact upstream AtomSlicer commit and retain license/provenance.

### Optional later repository: `workpiece-gr/orca-5x-plugin`

Keep the UI/Orca integration separate from the core planner.

### Existing `open-slicer-service`

Do not make it the experimental 5X development host.

Only after physical validation should it gain either:

- a separate experimental service binding; or
- a client to a dedicated five-axis service.

No 5X code should silently alter Authority-v2 output.

## 10. Machine manifest

Replace AtomSlicer's hard-coded constants with an immutable machine manifest, e.g.:

```yaml
machine_id: ratrig_vcore3_300_3z5x_exp
kinematics: three_z_tilting_bed

bed_supports_mm:
  - [TBD, TBD, TBD]
  - [TBD, TBD, TBD]
  - [TBD, TBD, TBD]

rail_angles_deg: [TBD, TBD, TBD]

limits:
  x_mm: [TBD, TBD]
  y_mm: [TBD, TBD]
  z_support_mm: [TBD, TBD]
  validated_tilt_deg: 10

clearance:
  nozzle_to_gantry_mm: TBD
  bed_corner_margin_mm: TBD

motion:
  z_support_max_speed_mm_s: TBD
  z_support_max_accel_mm_s2: TBD
```

Every physical five-axis G-code file should embed or accompany the manifest hash.

## 11. Independent validator

Do not trust a successful slice as proof of safe motion.

Build a second validation pass which consumes the final physical machine path and independently checks:

- X/Y bounds;
- each of Z/U/V bounds;
- maximum tilt;
- transformed bed corners;
- toolhead/bed clearance;
- support velocity;
- support acceleration;
- discontinuities;
- non-finite values;
- unexpected axis jumps;
- extrusion during invalid travel;
- start/end safe states.

If validation fails, no printable G-code is emitted.

## 12. Prototype gates

### Gate 0 — digital only

- build upstream AtomSlicer at a pinned commit;
- run known sample STLs;
- retain 3-axis, 5-axis and PLY outputs;
- reproduce the published 3Z inverse kinematics in unit tests;
- move all machine constants into a manifest;
- add golden tests for flat and tilted poses.

### Gate 1 — unheated motion rig

- three Z motors coupled and homed normally;
- switch to independent mode;
- command equal Z/U/V motion;
- command very small differential motion;
- heaters and extrusion disabled;
- emergency stop immediately accessible.

### Gate 2 — plane metrology

Validate requested bed normal versus measured plane at:

- 0°;
- ±2°;
- ±5°;
- ±10° about representative directions.

Record repeatability and return-to-flat error.

### Gate 3 — full dry-run G-code

- nozzle kept well above bed;
- execute representative 5X path without extrusion;
- log all axes;
- compare commanded support heights against validator output.

### Gate 4 — first material deposition

PLA only initially.

Use a small dedicated non-planar coupon at low speed and <=10° validated tilt.

### Gate 5 — indexed 3+2

Validate discrete bed-orientation changes between deposition regions before aggressive simultaneous motion.

### Gate 6 — continuous five-axis

Enable continuously changing orientation only after Gates 0–5 are accepted.

## 13. Qualification / Workpiece authority

A five-axis RatRig is not covered by the existing `ratrig_vcore3_300` qualification.

If five-axis printing ever becomes a customer manufacturing capability, create a dedicated authority chain which binds:

- the new machine key;
- exact mechanical revision;
- firmware and machine configuration;
- 5X planner commit;
- machine-manifest hash;
- postprocessor;
- final-validator version;
- material/process profile;
- physical qualification evidence.

Human workshop review remains mandatory.

## 14. First implementation milestone

The first useful software milestone is **not an Orca UI**.

It is:

```text
STL
 -> pinned AtomSlicer-derived planner
 -> canonical tool-space path
 -> configurable 3Z inverse kinematics
 -> independent envelope validator
 -> RRF Z/U/V G-code
 -> simulator / CSV/PLY diagnostics
```

Acceptance:

1. Flat normals produce equal Z/U/V support motion.
2. Known reference-machine poses reproduce AtomSlicer output within a defined numeric tolerance.
3. Changing the manifest changes kinematics without recompilation.
4. Invalid tilt or axis travel is rejected before G-code generation.
5. The output contains no production-authority claim.
6. Existing Workpiece Orca 2.4.2 and Authority-v2 tests remain unchanged.

## 15. Immediate engineering order

1. Create `ratrig-5x-engine`.
2. Pin AtomSlicer upstream commit and provenance.
3. Extract its toolpath and `Machine::inverse` behavior behind interfaces.
4. Build the machine manifest.
5. Add reference-output / property tests for the kinematics.
6. Add the independent physical-path validator.
7. Add RepRapFirmware `Z/U/V` postprocessing.
8. Create a digital visualization comparing tool-space and machine-space paths.
9. Measure the Workpiece RatRig support coordinates, usable Z travel and toolhead/gantry clearances.
10. Adapt the published CAD to those measurements.
11. Only then energize differential Z motion.
12. Add Orca Nightly plugin integration after the standalone path is reliable.

## 16. Open inputs that must be measured from the actual machine

The connected Workpiece repositories identify the slicer profile and Klipper flavor, but they do not contain the physical printer's current `printer.cfg`, controller pin map or a verified CAD snapshot.

Before firmware/CAD implementation, capture:

- exact RatRig revision and size;
- controller board(s);
- Z motor/driver mapping;
- leadscrew pitch and effective steps/mm;
- Z linear-rail length and usable travel;
- three bed-support coordinates;
- build-plate dimensions/thickness;
- heater and cable exit geometry;
- toolhead/hotend/probe model;
- nozzle protrusion;
- minimum nozzle-to-gantry clearance;
- enclosure interference;
- current homing/probing topology.

These values must become measured inputs, not assumptions.
