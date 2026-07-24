# V4 Open Issues

Last updated: 2026-03-20
Revised: 2026-07-24 — §1 through §3 left as written; see the §4 addendum.

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

> Status update 2026-07-24: the false-success half of this issue is fixed. The text below is kept unchanged as the point-in-time record; see the addendum in §4 for what changed and what is still open.

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

## 4. Addendum

### 4.1 2026-07-24 — the "fake completion" path in §1.3 is fixed

Sections 1 through 3 above are left as written, as the point-in-time record. This addendum records one change on top of them.

`MockExecutorStore._simulate_node_execution` in `packages/executor/store.py` used to fall through to a generic handler for any tool it had no real implementation for. That handler wrote placeholder `json/txt/svg` artifacts and returned `succeeded`. That fall-through is gone. A node that resolves to a tool identity with no registered handler now raises `MissingExecutorHandlerError`, and a node with `kind="tool"` that resolves to no tool identity at all raises as well.

The authoritative handler list is the `handlers` dict at the top of `MockExecutorStore._simulate_node_execution`. It is being actively extended, so treat the snapshot below as dated rather than fixed, and re-derive it before relying on it:

```bash
python -c "
import inspect, re
from packages.tools.compiler_metadata import DOMAIN_RULEBOOK
from packages.executor.store import MockExecutorStore
reachable = sorted({t for rb in DOMAIN_RULEBOOK.values() for t in rb['tool_order']})
handled = set(re.findall(r'\"(\w+)\":\s*self\._exec_', inspect.getsource(MockExecutorStore._simulate_node_execution)))
print([t for t in reachable if t not in handled])
"
```

Snapshot as of 2026-07-24. Handlers that exist: `identify_sequences`, `register_to_reference`, `segment_prostate`, `segment_cardiac_cine`, `classify_cardiac_cine_disease`, `generate_qa_snapshot`, `package_vlm_evidence`, `generate_report`.

Compiler-reachable tools with no handler, which therefore raise: `detect_lesion_candidates`, `extract_roi_features`, `brats_mri_segmentation`, `classify_brain_glioma_grade`. In other words the prostate lesion/ROI chain and the whole brain chain are still unrunnable; the cardiac chain has since gained handlers.

Non-tool nodes are unaffected. Presentational nodes such as `read_case` and `review_checkpoint` legitimately have nothing to call and keep their placeholder bundle.

**The consequence is flipped.** Previously, a graph containing these nodes ran to "8/8 done", all green, with the damage only visible downstream as `ROI features unavailable` or `Lesion tool status: not_assessable` buried inside the report. Now the same graph fails loudly: the node goes to `failed`, the reason lands on `node.notes` and in `case_state.last_error`, the run is recorded in `stage_outputs` with `ok=false` and `consumable=false`, `execute_until_done` stops and reports `graph.status="failed"`, and no artifacts are emitted. A red run that stops at the first unimplemented tool is the correct signal; the previous green run was not.

What this does **not** fix — the rest of §1.3 stands unchanged:

- `detect_lesion_candidates` still does not emit a real `candidates_path` / `lesion_mask_path`
- `extract_roi_features` still does not emit a real `feature_table_path`
- the brain and cardiac pipelines the compiler can plan still cannot be executed end to end
- the bar for calling §1.3 closed is still the four-point list in that section

In other words, the problem moved from "silently wrong" to "honestly blocked". The missing tool handlers are still missing.
