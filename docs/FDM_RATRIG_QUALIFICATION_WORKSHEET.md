# RatRig V-Core 3 300 qualification workshop worksheet

> **Status:** recording aid only. This worksheet does not qualify a printer, set tolerances, approve a profile or create production authority.
>
> Use it only with [`FDM_RATRIG_QUALIFICATION_PLAN.md`](FDM_RATRIG_QUALIFICATION_PLAN.md) and `qualification/ratrig_vcore3_300/qualification-evidence.template.json`. The canonical evidence JSON and later canonical `fdm-machine-qualification/1.0.0` receipt remain authoritative. Do not invent or pre-fill measurements, reviewer approval or physical pass results.

Protocol: `workpiece-ratrig-vcore3-300-qualification-v1`  
Printer key: `ratrig_vcore3_300`

## 1. Qualification lane identity

Complete before any print.

| Field | Recorded value |
| --- | --- |
| Material | |
| Quality | |
| Strength | |
| Machine asset ID | |
| Physical printer identification notes | |
| Installed nozzle diameter (mm) | |
| Nozzle identity/type | |
| Bed surface | |
| Firmware/config snapshot SHA-256 | |
| Filament manufacturer | |
| Filament product | |
| Colour | |
| Lot/spool ID | |
| Conditioning/drying notes | |

Initial recommended lane from the protocol is `pla / balanced / functional`, but that recommendation is **not** a qualification result.

## 2. Exact software/profile identity

Copy these from the exact Authority-v2 candidate run used for the physical qualification.

| Field | Recorded value |
| --- | --- |
| Machine profile SHA-256 | |
| Process profile SHA-256 | |
| Filament profile SHA-256 | |
| Source commit | |
| Authority runtime ref | |
| Toolchain digest | |
| Orca version | |

Stop if the request/profile hashes differ from the qualification lane being tested.

## 3. Acceptance criteria frozen before run

All numerical and visual criteria must be chosen **before** the first qualification print. Never back-fit tolerances to observed results.

| Criterion | Frozen value/rule |
| --- | --- |
| `acceptanceCriteria.frozenBeforeRun` | `true` only after all criteria below are fixed |
| Dimensional tolerance (mm) | |
| Hole tolerance (mm) | |
| Repeatability range (mm) | |
| Time variance (%) | |
| Material variance (%) | |
| Visual defect rules | |
| Support-removal rules | |

Criteria approved/frozen by: ____________________  
UTC time frozen: ____________________

## 4. Immutable fixture verification

Generate fixtures from the committed generator and verify every SHA-256 before use.

| Fixture | Expected SHA-256 | Actual SHA-256 | Match |
| --- | --- | --- | --- |
| `qualification-dimensions-v1.stl` | `52f02a382c62e4e659fa7df8df055389f76da89e1265a63fec4ebc6752083bbb` | | |
| `qualification-holes-v1.stl` | `ce1237ea41ac74feee93b8e4b5c811f2b329de0f816ec238d89e4847b47a74ec` | | |
| `qualification-bridge-support-v1.stl` | `cb4a9639617841e08647c634d62417513d60d39eb12a7a2ff850dcaab261d56a` | | |

If any hash differs, stop and do not use that fixture as evidence under this protocol version.

## 5. Digital candidate package record

For every physical run, print the exact retained Authority candidate G-code. Do not manually re-slice or alter settings and then represent the result as evidence for the original package.

Use one row per fixture/run/plate as needed.

| Run | Source STL SHA-256 | 3MF SHA-256 | G-code SHA-256 | Bundle/manifest ID or hash | Predicted time | Predicted material | Exact G-code printed? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Q1 | | | | | | | |
| Q2 | | | | | | | |
| Q3 | | | | | | | |
| Q4 | | | | | | | |
| Q5 | | | | | | | |

## 6. Q1 — dimensions fixture

Fixture: `qualification-dimensions-v1.stl`, quantity 1.

### Measurements

| Feature | Nominal | Measured | Error | Within frozen criterion? |
| --- | ---: | ---: | ---: | --- |
| Cube X | 20 mm | | | |
| Cube Y | 20 mm | | | |
| Cube Z | 20 mm | | | |
| Bar length | 100 mm | | | |
| Bar thickness | record from fixture geometry/protocol tooling | | | |
| Tower height | 40 mm | | | |

### Observations

| Observation | Result/notes |
| --- | --- |
| First-layer / bed contact | |
| Layer shift | |
| Ringing | |
| Under/over-extrusion | |
| Warping | |
| Adhesion defects | |
| Other anomaly/intervention | |

Q1 provisional result against pre-frozen criteria: ____________________

## 7. Q2 — holes and external pins

Fixture: `qualification-holes-v1.stl`, quantity 1.

| Feature | Nominal | Measured | Error | Within frozen criterion? |
| --- | ---: | ---: | ---: | --- |
| Internal diameter | 5 mm | | | |
| Internal diameter | 10 mm | | | |
| Internal diameter | 20 mm | | | |
| External pin diameter | 5 mm | | | |
| External pin diameter | 10 mm | | | |
| External pin diameter | 20 mm | | | |

| Observation | Result/notes |
| --- | --- |
| Roundness / distortion | |
| Elephant-foot / first-layer fit effect | |
| Other anomaly/intervention | |

Do not mark the case passing merely because components can be forced together.

Q2 provisional result against pre-frozen criteria: ____________________

## 8. Q3 — automatic supports / span handling

Fixture: `qualification-bridge-support-v1.stl`, quantity 1, normal Authority automatic-support policy.

| Check | Result/notes |
| --- | --- |
| Print completed without collision/layer shift | |
| Supports generated where expected | |
| Support removal effort | |
| Damage from support removal | |
| Underside quality | |
| Gross sagging / failed span | |
| Frozen support-removal rules met | |
| Frozen visual rules met | |
| Other anomaly/intervention | |

Q3 provisional result against pre-frozen criteria: ____________________

## 9. Q4 — repeatability

Dimensions fixture, quantity 5, exact same request/profile combination.

Record the same agreed key dimensions on all five copies. The protocol requires min/max/range; do not report only an average.

| Measurement | Copy 1 | Copy 2 | Copy 3 | Copy 4 | Copy 5 | Min | Max | Range | Within frozen criterion? |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Key dimension 1 | | | | | | | | | |
| Key dimension 2 | | | | | | | | | |
| Key dimension 3 | | | | | | | | | |

| Copy | Qualifying visual/machine defect? | Notes |
| --- | --- | --- |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |

Q4 provisional result against pre-frozen criteria: ____________________

## 10. Q5 — representative functional part

Choose one non-customer-controlled mechanical part representative of expected Workpiece production. It should include holes/fastener features, multiple wall directions, meaningful Z height, and at least one feature not present in the coupons.

| Field | Recorded value |
| --- | --- |
| Part filename | |
| Part SHA-256 frozen before generation | |
| Why this part is representative | |
| Requested material / quality / strength | |
| Exact candidate package identity | |
| Dimensional/fit criteria frozen before run | |
| Visual criteria frozen before run | |

### Representative-part results

| Check/measurement | Expected/frozen criterion | Observed | Pass against criterion? |
| --- | --- | --- | --- |
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |

Q5 provisional result against pre-frozen criteria: ____________________

## 11. Predicted vs actual production observations

| Run | Predicted print time | Actual print time | Variance % | Within criterion? | Predicted material | Actual material/mass if reliably measured | Variance % | Within criterion? |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| Q1 | | | | | | | | |
| Q2 | | | | | | | | |
| Q3 | | | | | | | | |
| Q4 | | | | | | | | |
| Q5 | | | | | | | | |

Do not invent actual material usage if it cannot be measured reliably; record that limitation instead.

## 12. Photo/evidence inventory

Photos must be sufficient for a human reviewer to identify the fixture/part and visible defects. Record filenames/hashes when packaging the final evidence.

| Evidence item | Filename | SHA-256 / identifier | Notes |
| --- | --- | --- | --- |
| Q1 photos | | | |
| Q2 photos | | | |
| Q3 photos | | | |
| Q4 photos | | | |
| Q5 photos | | | |
| Measurement export/notes | | | |
| Other evidence | | | |

## 13. Hard-fail review

Check each item before any approval decision.

- [ ] Printed G-code is byte-identical to retained Authority candidate G-code.
- [ ] Material / quality / strength match the qualification lane.
- [ ] Machine/process/filament profile hashes match the qualification lane.
- [ ] Acceptance criteria were frozen before printing began.
- [ ] No qualifying layer shift, collision, aborted print or unrecovered adhesion/machine failure was ignored.
- [ ] All required measurements/evidence are present.
- [ ] No manually edited/re-sliced failed result is represented as the original run.
- [ ] Any failed run is retained rather than overwritten.

If any hard-fail condition applies, the lane cannot pass under this run set.

## 14. Evidence-package completion

Before packaging `qualification-evidence.zip`, confirm the evidence directory contains at least:

- [ ] completed canonical worksheet/evidence JSON;
- [ ] exact fixture STLs and verified SHA-256 values;
- [ ] exact retained 3MF and per-plate G-code for every run;
- [ ] Authority candidate bundle/manifest/price/statistics receipts;
- [ ] photos sufficient for review;
- [ ] all measurement tables including Q4 min/max/range;
- [ ] predicted vs actual print time;
- [ ] predicted material and actual material/mass where reliably measured;
- [ ] failure/intervention/anomaly notes;
- [ ] representative-part STL/hash and results.

Final evidence ZIP filename: ____________________  
Final evidence ZIP byte count: ____________________  
Final evidence ZIP SHA-256: ____________________

Any later change inside the package changes the evidence bytes and requires a new hash/receipt.

## 15. Human review

This section records the review process but is **not** the canonical production receipt.

| Field | Recorded value |
| --- | --- |
| Review status (`pending` / `approved` / `rejected`) | |
| Reviewer ID | |
| UTC completion time | |
| Decision notes | |

Reviewer signature/acknowledgment: ____________________

Only after the complete evidence package is deliberately approved may a canonical production receipt be created. A passing receipt must bind the exact printer key, material/quality/strength, profile hashes, evidence ZIP filename/byte count/SHA-256, stable qualification ID, protocol ID, reviewer ID and UTC completion time.

## 16. Promotion checklist after a real pass

Do not execute these steps for a failed/pending lane.

- [ ] Retain exact evidence ZIP and canonical receipt outside the source repository.
- [ ] Validate the receipt against the exact request/profile hashes.
- [ ] Mount/configure those exact evidence files into the production Authority runtime.
- [ ] Verify production `/health` sees the qualification while remaining fail-closed until deliberately enabled.
- [ ] Run controlled production-authority HTTP acceptance.
- [ ] Verify website ingestion/storage/re-verification of the exact returned bundle.
- [ ] Keep per-order human manufacturing review mandatory.
- [ ] Deliberately decide whether to enable only this exact tested lane for controlled production.

A passing lane must never be generalized to other materials, quality/strength levels, changed profile hashes, a changed RatRig configuration, or Generic Ender printers without their own real qualification.
