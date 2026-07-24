# V4 Manual Test Plan

Last updated: 2026-03-20

This document is meant to be used for hands-on manual testing.

The goal is not to cover every detail in one pass, but to answer three questions in as few test rounds as possible:

1. Whether `v4` can already be used as a real workstation backend
2. Whether the core end-to-end loop genuinely holds up
3. Whether the remaining problems are more about product experience or about whether the underlying work is real

## 1. Before You Start

Recommended environment:

- The API runs on the local machine
- The planner LLM runs on `<gpu-node>`
- GPU-heavy segmentation is allowed to run on `<gpu-node>`

Confirm these commands first:

```bash
cd /path/to/MRI_Agent_v4
PYTHONPATH=/path/to/MRI_Agent_v4 .venv/bin/pytest -q
```

```bash
curl http://127.0.0.1:8008/api/health
curl http://127.0.0.1:8008/api/planner/health
```

If the API is not up:

```bash
cd /path/to/MRI_Agent_v4
PYTHONPATH=/path/to/MRI_Agent_v4 .venv/bin/python -m apps.api
```

## 2. Round One: Happy Path

Goal:

- Confirm that a single prostate case really can go from chat all the way to a report

Suggested input:

- case: `/path/to/BCER/demo/cases/sub-019_2`
- chat:
  `Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.`

What to look for:

- `/api/chat` returns `mode=graph`
- `/api/graph` shows a complete workflow
- `/api/execute/until-done` ends with `graph_status=completed`
- `report.json` exists
- `clinical_report.md` exists
- `report.json` has `lesion_assessment_meta.segmentation_usable=true`
- `clinical_report.md` no longer contains:
  - `missing ADC and/or segmentation issues`
  - `Pipeline could not reliably assess lesions`

Verdict:

- If all of these hold, the most important loop — the one that shows the pipeline is doing real work end to end — is in place

## 3. Round Two: Patch / Review / Continue

Goal:

- Confirm that planner patching and human-in-the-loop are real capabilities now, not just for show

Suggested procedure:

1. Register the same case first
2. Enter in chat:
   `pause before segmentation`
3. Inspect the graph and the proposal
4. Apply the latest proposal
5. Execute up to the checkpoint
6. Then continue executing to completion

What to look for:

- The planner returns `mode=patch`
- A review checkpoint really is inserted into the graph
- The graph version changes after the patch is applied
- Execution stops at the checkpoint instead of running straight past it
- Execution can still reach completion after being resumed

Verdict:

- If this holds, graph patching is now an operable capability

## 4. Round Three: Recovery / Rerun

Goal:

- Confirm that a failure does not leave you with no option but to reset the whole graph

Suggested procedure:

1. Force a node to fail manually, or pick an existing failed run
2. Call `POST /api/execute/rerun-from-node`
3. Execute again

What to look for:

- The target node becomes `ready`
- Downstream nodes become `planned`
- Old artifacts are not overwritten
- New artifacts carry a new attempt
- The graph can eventually resume execution

Pay particular attention to:

- The event stream
- Artifact metadata
- Attempt history

Verdict:

- If this holds, the recovery path has moved from demo to usable

## 5. Round Four: Runtime / GPU / Container

Goal:

- Confirm that GPU-heavy tools really do run on the correct runtime

Where to look:

- Node outputs related to `segment_prostate`
- The runtime case state
- Provenance / runtime profile

What to confirm:

- `runtime_profile=apptainer-medgemma`, or whichever profile you expect
- `launcher=apptainer`
- `host=<gpu-node>`
- The outputs really exist, rather than being strings

If you want to run a smoke test directly:

```bash
cd /path/to/MRI_Agent_v4
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from packages.tools.runtime import run_v3_tool

repo = Path.cwd().resolve()
run_dir = repo / "tmp_manual_runtime_check"
artifacts_dir = run_dir / "artifacts"
case_state_path = run_dir / "case_state.json"
run_dir.mkdir(parents=True, exist_ok=True)
artifacts_dir.mkdir(parents=True, exist_ok=True)
case_state_path.write_text(json.dumps({
    "case_id": "manual-runtime-check",
    "run_id": "manual-runtime-check",
    "stage_outputs": {},
    "stage_meta": {},
    "artifacts_index": [],
    "summary": {},
    "metadata": {},
}), encoding="utf-8")

t2w_ref = (repo / "artifacts" / "graph-prostate-demo" / "03_segment-prostate" / "t2w_input.nii.gz").resolve()
result = run_v3_tool(
    "segment_prostate",
    {"t2w_ref": str(t2w_ref), "output_subdir": "04_segment-prostate"},
    case_id="manual-runtime-check",
    run_id="manual-runtime-check",
    run_dir=run_dir,
    artifacts_dir=artifacts_dir,
    case_state_path=case_state_path,
    runtime_profile_override="apptainer-medgemma",
)
print(result.runtime_profile, result.launcher, result.host)
print(result.data.get("prostate_mask_path"))
PY
```

## 6. Round Five: Frontend Integration Experience

Goal:

- Judge whether the remaining problems lean backend or frontend UX

While actually using the frontend, focus on:

- Whether the graph updates promptly
- Whether clicking an artifact takes you straight to it
- Whether the report is easy to read
- Whether checkpoints and reruns are easy to understand
- Whether the provenance is legible enough

In this round, record three kinds of problem:

- `Authenticity problems`
- `Control-flow problems`
- `Presentation / interaction problems`

## 7. Suggested Format For Recording Issues

Try to record each issue in this format:

```text
Title:
Steps:
Expected:
Actual:
graph_id / node_id involved:
Artifact path involved:
Reliably reproducible:
```

## 8. The Three Things I Would Test First

If you are short on time, do these three:

1. The prostate happy path through to a report
2. The pause-before-segmentation patch
3. `rerun-from-node`

These three do the most to show whether `v4` has moved out of the "engineering repair" phase and into the "ready for real trial use" phase.
