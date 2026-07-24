# V4 Planner Output Contract

Audit date: 2026-03-20

This document defines the contract for the primary output that `packages/planner` currently returns to its callers.

The goal is explicit:

- the planner's primary output is no longer plain chat text
- the planner now returns a `graph` or a `patch` in preference to anything else
- `reply` is only a secondary, operator-facing explanation

## 1. Top-level return structure

`BrainService.reply(...)` returns a JSON-serializable dict with the following fields:

```json
{
  "mode": "graph | patch | reply",
  "intent_spec": {
    "intent_id": "...",
    "intent_type": "graph_request | patch_request | reply_request",
    "domain": "...",
    "normalized_request": "...",
    "explicit_requested_capabilities": ["report", "segment"],
    "inferred_requested_capabilities": ["full_pipeline"],
    "constraints": [{"kind": "ordering", "value": "before_segmentation", "required": true, "source_text": "..."}],
    "preferences": [{"kind": "report_length", "value": "short", "source_text": "..."}],
    "target_graph_id": "...",
    "target_node_id": "...",
    "patch_anchor": "...",
    "notes": ["..."]
  },
  "reply": {
    "role": "assistant",
    "content": "..."
  },
  "graph": { "...ActionGraph json..." },
  "patch": { "...ExecutionPatch json..." },
  "warnings": ["..."],
  "planner_metadata": {
    "intent": "...",
    "source": "heuristic | llm | hybrid",
    "llm_status": "llm | heuristic | disabled | error | llm_filtered | not_used",
    "model": "...",
    "base_url": "...",
    "latency_ms": 123,
    "validation_passed": true,
    "validation_errors": [],
    "fallback_used": true,
    "extras": {}
  },
  "patch_reason": "..."
}
```

Notes:

- When `mode="graph"`, `graph` is the primary result and `patch` is empty.
- When `mode="patch"`, `patch` is the primary result and `graph` is empty.
- When `mode="reply"`, there is no structured proposal, only the secondary text reply.
- `reply` is always a secondary output and should no longer be treated as the planner's primary result.

## 2. The two main paths implemented so far

### 2.1 Semantic IR -> draft graph

The planner's internal sequence is:

1. Extract an `IntentSpec` from the user's input
2. Optionally have the LLM add an intent draft, constrained by the semantic planner prompt
3. Derive the domain / capabilities / constraints / preferences from the `IntentSpec`
4. Hand off to the compiler to produce an `ActionGraph`
5. Use the semantic validator to check whether the graph dropped anything the user explicitly asked for

This means the graph no longer carries semantic parsing responsibility directly; it is purely compiler output.

The minimal semantic fields of `IntentSpec` are currently:

- `intent_id`
- `intent_type`
- `domain`
- `normalized_request`
- `explicit_requested_capabilities`
- `inferred_requested_capabilities`
- `constraints`
- `preferences`
- `target_graph_id`
- `target_node_id`
- `patch_anchor`
- `notes`

Extraction rules:

- `explicit_requested_capabilities` only collects capabilities the user named directly, such as `register / segment / classify / report`
- `explicit_requested_capabilities` now also supports `lesion / roi_features`
- `inferred_requested_capabilities` only collects capabilities the compiler adds to fill out the workflow, such as `full_pipeline`
- `constraints` records hard constraints such as `before_segmentation` and `human_review_required`
- `preferences` records soft preferences such as `short report`

### 2.2 Natural language -> draft graph

The minimal path implemented so far has moved on from prostate-only to domain-aware and capability-aware:

- the planner combines `user_message`
- `case_state.domain`
- the current domain catalog / capability catalog

and uses them together to decide:

- which domain the graph belongs to
- which capabilities should be materialized
- which tools to select accordingly

Currently supported domains:

- `prostate`
- `brain`
- `cardiac`

Currently supported materialized capabilities:

- `full_pipeline`
- `register`
- `segment`
- `classify`
- `report`

Examples:

- prostate workup: `identify_sequences -> register_to_reference -> segment_prostate -> package_vlm_evidence -> generate_report`
- brain classify/report: `identify_sequences -> brats_mri_segmentation -> extract_roi_features -> classify_brain_glioma_grade -> generate_report`
- cardiac report: `identify_sequences -> segment_cardiac_cine -> generate_report`

The graph still retains a completed `intake_case` planner node as the workflow entry point.

### 2.3 Natural language -> typed patch

The minimal path implemented so far:

- the user enters `pause before segmentation`
- the planner produces a valid `ExecutionPatch`
- the patch targets the currently active graph

The current patch proposal uses:

- `op="insert_checkpoint"`
- `target="<segmentation node id>"`
- `value.after_node="<registration node id>"`

The effect is to:

- insert a human review checkpoint after registration
- then resume execution at the segmentation node

## 3. Minimal self-check rules

Before returning a structured proposal, the planner runs a minimal validity check.

### Graph validation

- `node_id` must be unique
- `depends_on` must not reference a node that does not exist
- `tool_name` must belong to the currently known tool catalog
- `edge.from_node` / `edge.to_node` must not reference nodes that do not exist

### Semantic graph validation

- the graph must not drop a capability the user explicitly requested
- an explicitly requested `roi_features` must not be reduced to segmentation/report alone
- `reply` text is not a substitute for the graph coverage check

### Patch validation

- `insert_checkpoint.target` must be an existing graph node
- `value.after_node` must be an existing graph node

Validation results are written to:

- `planner_metadata.validation_passed`
- `planner_metadata.validation_errors`
- `warnings`

## 4. Degradation semantics

The LLM no longer determines whether the planner can produce a primary result.

The current semantics are:

- graph / patch proposals are generated by the deterministic heuristic planner
- the LLM is only responsible for adding a short, operator-facing `reply`

So even if the LLM is down:

- the planner can still return a primary result with `mode="graph"` or `mode="patch"`
- `planner_metadata.llm_status` is marked as `disabled` / `error` / `llm_filtered`
- `warnings` carries the reason for the degradation

This guarantees that:

- the planner does not lose its ability to produce proposals just because the chat layer is down
- a failed `reply` does not block structured graph / patch output

## 5. Current intent routing

Implemented intents:

- `graph_domain_workup`
- `patch_review_before_segmentation`
- `reply`

The current minimal intent rules:

- a request carrying domain plus workflow / segment / classify / register / report semantics routes to `mode="graph"`
- a request containing `pause/review/checkpoint before segmentation` routes to `mode="patch"`
- any other request routes to `mode="reply"`

## 6. planner_metadata.extras extensions

When `mode="graph"`, `planner_metadata.extras` currently contains at least:

- `domain`
- `proposal_kind`
- `requested_capabilities`
- `available_domain_capabilities`
- `selected_tools`
- `runtime_hints`

`runtime_hints` gives the runtime profile currently resolved for each selected tool, for example:

- `profile_id`
- `launcher`
- `gpu`
- `ssh_host`

This lets a planner proposal surface its execution prerequisites as early as the graph draft stage, in particular that GPU tools can be run via `ssh <gpu-node>`.

## 7. Minimal wiring required from Agent 3

This round of work did not modify `apps/api/**`.

For Agent 3 to wire planner proposals into the API and state layer for real, the minimum required is:

1. When `/api/chat` receives `planner.mode="graph"`, persist `planner.graph` as a staged proposal instead of leaving it only in the response.
2. When `/api/chat` receives `planner.mode="patch"`, use `planner.patch` in preference to falling back to the heuristic `preview_patch(reason=...)`.
3. Add an explicit "accept / replace active graph" entry point for graph proposals.

## 8. Reproduction

### Prostate Graph Draft Demo

```bash
cd /path/to/MRI_Agent_v4
python - <<'PY'
from packages.planner.service import create_default_brain_service
from packages.schemas.mock_data import create_mock_session

brain = create_default_brain_service()
session = create_mock_session()
result = brain.reply(
    user_message="Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.",
    graph=session.graph,
    case_state=session.case_state,
    chat_history=session.chat_history,
)
print(result["mode"])
print(len(result["graph"]["nodes"]))
print(result["planner_metadata"]["validation_passed"])
PY
```

### Brain Graph Draft Demo

```bash
cd /path/to/MRI_Agent_v4
python - <<'PY'
from packages.planner.service import create_default_brain_service
from packages.schemas.models import ActionGraph, CaseState

brain = create_default_brain_service()
graph = ActionGraph(graph_id="g-brain", case_id="brain_case", domain="brain", root_goal="x", nodes=[], edges=[], artifacts=[], events=[], proposals=[], patch_history=[])
case_state = CaseState(case_id="brain_case", domain="brain", input_root="/tmp/brain_case")
result = brain.reply(
    user_message="Plan a brain MRI workflow to segment the tumor, classify glioma grade, and report findings.",
    graph=graph,
    case_state=case_state,
    chat_history=[],
)
print(result["mode"])
print(result["graph"]["domain"])
print(result["planner_metadata"]["extras"]["requested_capabilities"])
print([node["tool_name"] for node in result["graph"]["nodes"] if node.get("tool_name")])
PY
```

### Patch Demo

```bash
cd /path/to/MRI_Agent_v4
python - <<'PY'
from packages.planner.service import create_default_brain_service
from packages.schemas.mock_data import create_mock_session

brain = create_default_brain_service()
session = create_mock_session()
result = brain.reply(
    user_message="pause before segmentation",
    graph=session.graph,
    case_state=session.case_state,
    chat_history=session.chat_history,
)
print(result["mode"])
print(result["patch"]["operations"][0]["op"])
print(result["planner_metadata"]["validation_passed"])
PY
```
