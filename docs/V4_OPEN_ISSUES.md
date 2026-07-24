# V4 Open Issues

Last updated: 2026-03-20

This document records only the problems in `MRI_Agent_v4` that are not yet fully solved.

## 1. Core Problems Still Not Fully Solved

### 1.1 The planner is not yet a complete general-purpose Brain

Already supported:

- the main `IntentSpec -> compiler -> validator` chain
- structured `graph / patch / reply` output
- minimal graph synthesis for the three domains prostate / brain / cardiac
- `AGENT.md / SOUL.md`-style semantic planner prompt assets

Not yet fully solved:

- there are still too few patch types; it is mostly still `insert_checkpoint`
- multi-turn follow-up has not been stabilized into a "graph patch merge against the current graph"
- `SubagentProposal` has not actually landed
- optional LLM intent extraction is still off by default; the current path relies mainly on deterministic extraction

### 1.2 Retry is still operator-driven, not bounded retry

Already supported:

- `rerun-from-node`
- attempt history
- continuing execution after a patch

Not yet fully solved:

- `retry_policy` has not entered the shared schema
- the executor has no typed bounded retry / retry budget

### 1.3 The prostate lesion / ROI chain still has a "fake completion" problem

The graph layer can already orchestrate this correctly:

- `segment_prostate -> detect_lesion_candidates -> extract_roi_features -> package_vlm_evidence -> generate_report`

But the key problem that remains unsolved is:

- in the executor, `detect_lesion_candidates` and `extract_roi_features` still go through the generic handler rather than a real v3 tool handler
- node status may display as `succeeded/completed` while in reality only placeholder `json/txt/svg` was produced, with no `candidates_path / lesion_mask_path / feature_table_path` for downstream steps to consume
- `package_vlm_evidence` and `generate_report` therefore only ever see `partial`-level evidence
- the final report ends up exhibiting symptoms like these:
  - `ROI features unavailable`
  - `Lesion tool status: not_assessable`
  - `lesion candidate geometry unavailable`

This means:

- the current prostate demo is no longer "completely broken", but the lesion evidence chain still cannot be claimed as fully productized
- "8/8 done" in the graph UI does not mean the lesion-level evidence contract genuinely closes the loop

The bar for calling this closed should be:

- `detect_lesion_candidates` emits a real `candidates_path / lesion_mask_path`
- `extract_roi_features` emits a real `feature_table_path`
- all three of those outputs pass runtime contract validation and are written into case state
- when there is no real lesion/ROI evidence, `generate_report` should fail or enter an explicit yellow state, rather than letting the node run to completion normally and then degrading inside the report

### 1.4 The tool runtime is not yet fully self-contained

Already supported:

- `inproc`
- `ssh`
- `ssh + apptainer`
- runtime provenance

Not yet fully solved:

- `apptainer-medgemma` still reuses the host's `qwen_vllm` env
- it is not yet a fully baked domain image
- the `nnunet`-related tools still have no dedicated container profile

### 1.5 Patch impact analysis is still a minimal rule set

Patch / recovery is already usable, but it is not full static analysis:

- it currently handles the most common target + downstream invalidation correctly
- there is no stronger dependency-aware legality analysis yet

### 1.6 The viewer is not at medical imaging workstation level

The viewer can already show:

- text / json / svg / ordinary image artifacts
- artifact rail / raw link / inspector metadata

But the key problems that remain unsolved are:

- it cannot render `nii.gz` / volumetric image stacks directly
- it cannot link `T2w / ADC / DWI` together as a slice viewer in a shared space
- it cannot overlay `prostate mask / zone mask / lesion mask / lesion candidates` as layers on top of the viewer
- the artifact rail currently tends to treat report/json/svg as the primary preview target and lacks any "imaging-first" selection logic
- the lesion tools do not reliably produce overlay PNGs / contour bundles either, so even when the graph has the nodes, the viewer has no visual material to work with

The suggested direction for closing out the viewer:

- add a NIfTI-aware slice viewer (axial/sagittal/coronal, with axial supported first at minimum)
- support multi-layer overlays: base image / prostate mask / zone mask / lesion mask / candidates
- add windowing, slice scroll, opacity toggle, and series switching
- add `spatial_ref / modality / overlay_for / derived_from` to artifact metadata
- group segmentation / lesion artifacts with priority in the rail, instead of laying them out flat alongside report text

### 1.7 Frontend recovery / provenance display is not finished

- the recovery controls are not yet a full workstation-grade UI
- runtime provenance has not been fully surfaced in the artifact inspector

## 2. Problems That Are No Longer Primary Blockers

The following no longer count as primary blockers:

- the planner not emitting structured results
- the intermediate data contract in the prostate demo being completely broken
- state being in-memory only, with no durable store
- there being no usable GPU runtime path at all on `<gpu-node>`
- the old conflicting wording between `report.json` and `clinical_report.md`

## 3. Current Stage Assessment

Judged as a backend MVP:

- it is already ready to enter real manual testing

Judged as a complete v4 workstation:

- it is still `strong MVP / pre-release`
- it should not yet claim that all of its capabilities are productized
