# V4 Executor Stage Data Contract

Last updated: 2026-03-20

## 1. Purpose

This document defines the minimal real contract that the executor in the `MRI_Agent_v4` prostate demo has to honour.

The point is not "what a node happened to run", but:

- which outputs exist only for UI or audit purposes
- which outputs are on the critical path that downstream steps actually consume
- which files, if absent, must prevent a node from declaring itself `succeeded`

Current policy:

- `identify_sequences`, `register_to_reference`, `segment_prostate`, `package_vlm_evidence`, and `generate_report` all go through the real v3 tool bridge
- the executor performs existence validation on critical output paths
- `generate_report` additionally performs downstream consistency validation
- if any critical output is missing, or the report contradicts itself semantically, the node is marked `failed` and the graph must not be allowed to drift into a misleading `completed` state

## 2. Shared Semantics

- `ok`: the tool call itself did not raise
- `consumable`: the tool's critical downstream outputs genuinely exist and pass minimal consistency checks
- `succeeded`: only when `ok=true` and `consumable=true`
- `failed`: the tool raised, or a critical output is missing, or a downstream consistency check failed

Every stage record in the runtime `case_state.json` now records all of:

- `ok`
- `consumable`
- `data`
- `validation`

## 3. Stage Contract

### 3.1 `identify_sequences`

Inputs:

- `dicom_case_dir`

Display-only fields:

- `mapping`
- `confidence`
- `series`
- `note`

Real downstream dependencies:

- `mapping`
- `series_inventory_path`
- `dicom_meta_path`
- `dicom_headers_index_path`

Paths that must genuinely exist:

- `series_inventory_path`
- `dicom_meta_path`
- `dicom_headers_index_path`

Downstream consumers:

- `register_to_reference`
- `segment_prostate`
- `package_vlm_evidence`
- `generate_report`

### 3.2 `register_to_reference`

Inputs:

- `fixed`
- `moving`

Display-only fields:

- `qc_pngs`
- `qc_metrics`
- `note`
- `warnings`

Real downstream dependencies:

- `resampled_path`
- `transform_path`

Paths that must genuinely exist:

- `resampled_path`
- `transform_path`

Downstream consumers:

- in the current prostate demo, the later report/evidence steps depend mainly on stage success and the artifact index
- if lesion / feature / QC nodes are added later, they should consume `resampled_path` directly

### 3.3 `segment_prostate`

Inputs:

- `t2w_ref`

Display-only fields:

- `note`
- `warnings`
- `degraded_mode`

Real downstream dependencies:

- `prostate_mask_path`
- `zone_mask_path`
- `t2w_input_path`

Paths that must genuinely exist:

- `prostate_mask_path`
- `zone_mask_path`
- `t2w_input_path`

Downstream consumers:

- `package_vlm_evidence`
- `generate_report`
- lesion / ROI feature nodes added in the future

Notes:

- `degraded_mode=true` still allows the node to be `succeeded`
- but only on the condition that the fallback genuinely wrote out readable mask/NIfTI files
- in other words, degraded does not mean fake

### 3.4 `package_vlm_evidence`

Inputs:

- `case_state_path`

Display-only fields:

- `summary`

Real downstream dependencies:

- `vlm_evidence_path`

Paths that must genuinely exist:

- `vlm_evidence_path`

Downstream consumers:

- `generate_report`
- the frontend evidence panel

### 3.5 `generate_report`

Inputs:

- `case_state_path`
- `domain`

Display-only fields:

- `report_txt_path`

Real downstream dependencies:

- `report_json_path`
- `clinical_report_path`

Paths that must genuinely exist:

- `report_json_path`
- `clinical_report_path`

Additional consistency check:

- if the `segment_prostate` node has already `succeeded`, then
  `lesion_assessment_meta.segmentation_usable` in `report_json_path` must not be `false`

Failure semantics:

- if the report files are missing, `generate_report` fails
- if the report content denies a segmentation that already succeeded, `generate_report` fails

## 4. Key On-Disk Paths in the Current Prostate Demo

Under the default graph `graph-prostate-demo`:

- runtime state: `runtime/graph-prostate-demo/case_state.json`
- identify artifacts: `artifacts/graph-prostate-demo/01_identify-sequences/`
- register artifacts: `artifacts/graph-prostate-demo/02_register-adc/`
- segment artifacts: `artifacts/graph-prostate-demo/03_segment-prostate/`
- vlm bundle: `artifacts/graph-prostate-demo/04_package-vlm-evidence/`
- report artifacts: `artifacts/graph-prostate-demo/05_generate-report/`

## 5. Pass/Fail Rules Agent 4 Can Check Directly

- when `register_to_reference` is `succeeded`, `resampled_path` and `transform_path` must exist
- when `segment_prostate` is `succeeded`, `prostate_mask_path`, `zone_mask_path`, and `t2w_input_path` must exist
- when `generate_report` is `succeeded`, `report_json_path` and `clinical_report_path` must exist
- if `segment_prostate` is `succeeded`, `report.json` must not contain `segmentation_usable: false`
- when the graph ends up `completed`, every critical stage record must have `consumable=true`
