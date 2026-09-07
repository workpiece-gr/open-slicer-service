# RatRig V-Core 3 300 physical qualification protocol v1

Status: **qualification preparation only**. Nothing in this document, its fixtures, or its templates qualifies a printer. Production authority remains blocked until real prints are completed, retained evidence is reviewed, and an exact canonical `fdm-machine-qualification/1.0.0` receipt is deliberately approved.

Protocol id: `workpiece-ratrig-vcore3-300-qualification-v1`

Target software printer key: `ratrig_vcore3_300`

## Why this exists

The production receipt contract intentionally verifies identity and immutability, not physical quality. It binds one human-approved qualification to the exact printer key, material/quality/strength request, machine/process/filament profile hashes, evidence bytes and review decision. Physical acceptance criteria and measurements therefore need a separate workshop protocol before `productionReady=true` can ever be truthful.

One qualification receipt covers **one exact** material + quality + strength + profile-hash combination. A different combination needs its own real evidence and receipt.

## Initial qualification scope

Start with one narrow lane rather than trying to qualify the whole menu at once:

- printer: `ratrig_vcore3_300`;
- material: `pla`;
- quality: `balanced`;
- strength: `functional`.

This is the recommended first lane because `balanced` / `functional` is the service default and the most exercised Authority-v2 software path. This is only the planned first test scope; it is not an accepted production profile.

After the first lane passes real workshop review, repeat the same protocol for additional combinations actually intended for sale. Recommended expansion order is `petg` / `balanced` / `functional`, then `pctg` / `balanced` / `functional`. ABS, TPU, other quality levels and other strength levels should remain unqualified until their own workshop conditions and acceptance criteria are deliberately defined and tested.

## Immutable qualification fixtures

Generate the v1 fixtures from the committed standard-library-only generator:

```bash
python qualification/ratrig_vcore3_300/generate_fixtures.py --output-dir ./qualification-fixtures
```

Expected exact source hashes:

| Fixture | Purpose | SHA-256 |
| --- | --- | --- |
| `qualification-dimensions-v1.stl` | 20 mm cube, 100 mm bar, 40 mm tower | `52f02a382c62e4e659fa7df8df055389f76da89e1265a63fec4ebc6752083bbb` |
| `qualification-holes-v1.stl` | 5/10/20 mm nominal internal rings and matching external pins | `ce1237ea41ac74feee93b8e4b5c811f2b329de0f816ec238d89e4847b47a74ec` |
| `qualification-bridge-support-v1.stl` | single-body automatic-support handling of a 40 mm clear span | `cb4a9639617841e08647c634d62417513d60d39eb12a7a2ff850dcaab261d56a` |

If any generated hash differs, stop. Do not use the changed file as qualification evidence until the fixture version is deliberately updated and reviewed.

## Before the first physical run

Copy `qualification-evidence.template.json` into a working evidence directory. It is explicitly marked `template_not_evidence` and must stay non-authoritative until real observations exist.

Before printing, record or freeze:

- the physical machine identity used in the workshop;
- nozzle diameter and actual installed nozzle;
- bed surface;
- printer firmware/config snapshot identity or hash where available;
- filament manufacturer/product/colour and spool or lot identifier;
- any filament conditioning/drying notes;
- exact material, quality and strength request;
- exact machine/process/filament profile SHA-256 values returned by the Authority candidate chain;
- exact source commit/runtime identity used to generate the candidate;
- the numerical and visual acceptance criteria to be used for the run.

`acceptanceCriteria.frozenBeforeRun` must be changed to `true` before the first qualification print. Do not choose tolerances retrospectively after seeing the results.

## Generate the digital test jobs

Qualification prints must originate from the Authority-v2 candidate chain, not from a manually re-sliced desktop project. The candidate application is:

```text
app.authority_candidate_api:app
POST /v2/authority-candidate
```

It is enabled separately with `ENABLE_FDM_AUTHORITY_V2_API=1` and a server-only `WORKPIECE_FDM_AUTHORITY_V2_TOKEN`.

For each fixture, generate and retain the exact candidate package for the selected qualification lane. Keep the immutable source STL, exact generated 3MF, exact per-plate G-code, bundle/evidence metadata, profile hashes, source commit/runtime identity, predicted material and predicted time.

The G-code physically printed must be the exact retained G-code from that candidate package. Do not open the project and re-slice it with changed settings. If any manual production edit is required, that run is not evidence for this exact qualification and must be regenerated/repeated under the revised configuration.

## Physical run sequence

### Q1 — dimensions

Print `qualification-dimensions-v1.stl`, quantity 1.

Measure and record at minimum:

- cube X/Y/Z;
- bar length and thickness;
- tower height;
- first-layer/bed-contact observations;
- visible layer shift, ringing, under/over-extrusion, warping or adhesion defects.

Compare every measured dimension against the tolerance frozen before the run.

### Q2 — holes and external pins

Print `qualification-holes-v1.stl`, quantity 1.

Measure and record:

- nominal 5/10/20 mm internal diameters;
- nominal 5/10/20 mm external pin diameters;
- roundness/obvious distortion;
- any elephant-foot or first-layer effect that changes intended fit.

Use the pre-frozen hole/external-feature criteria. Do not call a profile qualified merely because parts can be forced together.

### Q3 — automatic supports / span handling

Print `qualification-bridge-support-v1.stl`, quantity 1, through the normal Authority automatic-support policy. The fixture is one connected manifold U-shaped body, so the 40 mm clear span cannot be split and auto-arranged as separate STL shells.

Record:

- whether the print completes without collision or layer shift;
- whether supports were actually generated where expected;
- support removal effort and any damage to intended geometry;
- underside quality and gross sagging/failed span behavior;
- whether the result meets the support-removal and visual rules frozen before the run.

### Q4 — repeatability

Generate the dimensions fixture as quantity 5 under the exact same request/profile combination and print the retained plate(s).

Measure the same key dimensions on all five copies. Record min/max/range rather than only an average. The run passes only if the range stays within the repeatability criterion frozen before printing and no copy has a qualifying defect.

### Q5 — representative functional part

Choose one non-customer-controlled mechanical part representative of the work Workpiece expects to sell: holes/fastener features, several wall directions, meaningful Z height, and at least one geometry feature not present in the coupons.

Freeze and hash that STL before generation. Generate it through the same Authority candidate chain and retain the exact package. Record dimensional/fit/visual results against pre-declared criteria. This part supplements the controlled fixtures; it does not replace them.

## Hard-fail conditions

A qualification run cannot pass if any of the following occurs:

- the printed G-code is not byte-identical to the retained Authority candidate G-code;
- the material/quality/strength or profile hashes differ from the lane being qualified;
- the acceptance criteria were changed after printing began;
- the printer suffers a layer shift, collision, aborted print, unrecovered adhesion failure or other machine fault affecting the result;
- required measurements/evidence are missing;
- a failed result is manually edited/re-sliced and then represented as the original run;
- the human qualification review is not explicitly approved.

A failed run is useful evidence. Keep it and repeat after the actual corrective change; do not overwrite it.

## Evidence package

For each qualification lane, retain one evidence directory before packaging. It should contain at least:

- completed worksheet/evidence JSON;
- generated fixture STLs and their SHA-256 values;
- exact retained 3MF and per-plate G-code for every run;
- Authority candidate bundle/manifest/price or statistics receipts returned for the runs;
- photos sufficient for the human reviewer to identify the printed fixtures and visible defects;
- measurement table for every required dimension and repeatability copy;
- predicted vs actual print time;
- predicted material and, where measured reliably, actual material usage/mass;
- notes for any failure, intervention or anomaly;
- the representative-part STL/hash and its results.

Package those exact files as `qualification-evidence.zip` only after the run set is complete. The later production receipt hashes the exact ZIP bytes, so changing anything inside requires a new evidence hash and receipt.

## Human review and receipt transition

The committed `qualification-receipt.template.json` is deliberately fail-closed:

- `productionReady` is `false`;
- qualification/reviewer ids are blank;
- evidence byte count/hash are unset;
- review status is `pending`.

Only after the complete evidence package has been reviewed should a reviewer deliberately create the canonical production receipt. The final receipt must:

- set `productionReady=true` only for a passing lane;
- bind the exact `ratrig_vcore3_300` printer key;
- bind the exact material/quality/strength request;
- bind the exact machine/process/filament profile hashes from the tested candidate;
- bind the exact `qualification-evidence.zip` filename, byte count and SHA-256;
- use a stable qualification id and this protocol id;
- record an explicit `approved` human review with reviewer id and UTC completion time;
- be serialized as the canonical deterministic JSON required by `app/fdm_machine_qualification.py`.

The qualification review is not the same as per-order workshop review. Even after a machine/profile lane is physically qualified, every customer order still requires its separate human manufacturing review.

## Promotion sequence after a real pass

For one successfully reviewed lane:

1. retain the exact evidence ZIP and canonical receipt outside the source repository;
2. verify the receipt through `validate_machine_qualification_receipt` against the exact request/profile hashes;
3. mount/configure those exact evidence files into the production Authority runtime;
4. prove production `/health` sees the qualification but remains otherwise fail-closed until deliberately enabled;
5. run a controlled production-authority HTTP acceptance request;
6. verify website ingestion/storage/re-verification on the exact returned bundle;
7. keep human order review mandatory;
8. only then consider enabling that exact lane for controlled customer production.

Do not generalize a passing receipt to other materials, quality levels, strength levels, profile hashes, Ender printers, or a changed RatRig configuration.
