# V4 Planner Output Contract

Audit date: 2026-03-20
Revised: 2026-07-24 — §2.2 pipeline examples, §2.2 capability list, §3 validation-failure behavior, §4 scope note, and §7 (API wiring) corrected against the source.

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

## 2. The three main paths implemented so far

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

- `explicit_requested_capabilities` only collects capabilities the user named directly. The full keyword vocabulary in `packages/planner/service.py` is `full_pipeline / register / segment / classify / lesion / roi_features / report / qa / custom_analysis`; see `V4_INTENT_SPEC.md` §2.1.
- every one of those is additionally filtered against the capability catalog for the active domain, so a capability the domain does not advertise is dropped even if the user names it
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

Currently supported materialized capabilities — that is, the capabilities some `capability_rules.when_any` in `DOMAIN_RULEBOOK` (`packages/tools/compiler_metadata.py`) actually keys on:

- `full_pipeline`
- `register`
- `segment`
- `classify`
- `report`
- `lesion`
- `roi_features`

Not every capability the intent layer recognizes materializes a tool. `qa` and `custom_analysis` are extracted into `IntentSpec` and are advertised by all three domains, but no rulebook rule keys on them, so a request that only asks for them compiles down to `['identify_sequences']` and nothing else. Coverage of `lesion` and `roi_features` is also uneven per domain: only `prostate` expands on them, and only `prostate` and `brain` advertise `roi_features` at all.

Examples, taken from `_build_pipeline(domain, capabilities)[0]`:

- prostate workup (`full_pipeline`): `identify_sequences -> register_to_reference -> segment_prostate -> package_vlm_evidence -> generate_report`
- prostate lesion workup (`lesion`, `report`): `identify_sequences -> register_to_reference -> segment_prostate -> detect_lesion_candidates -> extract_roi_features -> package_vlm_evidence -> generate_report`
- brain classify/report (`classify`, `report`, and also `full_pipeline`): `identify_sequences -> brats_mri_segmentation -> extract_roi_features -> classify_brain_glioma_grade -> package_vlm_evidence -> generate_report`
- cardiac report (`report`): `identify_sequences -> segment_cardiac_cine -> package_vlm_evidence -> generate_report`
- cardiac full workup (`full_pipeline`): `identify_sequences -> segment_cardiac_cine -> classify_cardiac_cine_disease -> package_vlm_evidence -> generate_report`

`package_vlm_evidence` is always inserted ahead of `generate_report` — the `*-report-expansion` rule in every domain selects the pair together, so `generate_report` never directly follows an analysis node.

These lists are the compiler's `selected_tools`. The emitted `ActionGraph` additionally prepends an `intake_case` planner node, which is created with `status="succeeded"` and acts as the workflow entry point.

Reproduce any row with:

```bash
python -c "from packages.planner.compiler import _build_pipeline; print(_build_pipeline('cardiac', ['full_pipeline'])[0])"
```

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

### What failure does

Validation failure is not advisory. If any graph error is collected, `BrainService.reply(...)` discards the compiled graph and returns `mode="reply"` with `graph=None`, `validation_passed=false`, and a canned reply of `"Planner could not validate the draft graph proposal."`. The same applies to the patch path. The errors are also copied into `warnings`.

A caller that only inspects `mode` will therefore see a graph request come back as an ordinary chat reply. Check `planner_metadata.validation_passed` to tell a genuine `reply_request` apart from a rejected graph.

One way to hit this on a plainly reasonable request: `qa` and `custom_analysis` are extracted into `explicit_requested_capabilities`, but the coverage table in `packages/planner/validator.py` has no entry for either and no tool contract declares them, so the coverage check can never be satisfied. Asking for a workup plus a sandbox analysis in the same sentence yields `validation_errors: ["graph does not cover explicit requested capability: custom_analysis"]` and downgrades the whole turn to `mode="reply"`.

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

Scope note: this applies to the **LLM** axis only. The validator is a separate, harder gate, and it does downgrade `mode` to `reply` — see §3, "What failure does". So `mode="reply"` has three distinct causes: the request really was conversational, the compiled proposal failed validation, or `/api/chat` caught an exception (that last one reports `mode="error"`, see §7.3).

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

## 7. API wiring (shipped)

The three items previously listed here as outstanding have all landed in `apps/api/main.py`. This section now records what the wiring actually does, because it does **not** match what was originally asked for.

### 7.1 What shipped

1. `planner.mode="graph"` is persisted. `/api/chat` calls `STORE.replace_graph(planner_result["graph"], author_id="brain", reason=...)`, which swaps the active graph and appends a `graph_replaced` event.
2. `planner.mode="patch"` prefers the typed patch. `/api/chat` calls `STORE.stage_patch(planner_result["patch"], author_id="brain")` and sets a local `staged_patch` flag. The old heuristic `STORE.preview_patch(reason=...)` now runs only as a fallback, and only when no typed patch was staged **and** `graph.proposals` is currently empty.
3. Applying a proposal has an explicit entry point: `POST /api/proposals/apply-latest`.

### 7.2 The graph path has no human gate — read this before building UI on it

Item 1 shipped as a direct write, not as a staged proposal. The two paths are therefore asymmetric:

| planner mode | API call | lands in | operator approval |
| --- | --- | --- | --- |
| `graph` | `STORE.replace_graph(...)` | the **active graph**, immediately | none |
| `patch` | `STORE.stage_patch(...)` | `graph.proposals` | required — `POST /api/proposals/apply-latest` |

So any chat turn the planner routes to `mode="graph"` overwrites the operator's current graph on the spot. `graph.proposals` stays empty on that turn, and there is no accept, reject, or undo endpoint for it. `POST /api/proposals/apply-latest` gates patches only; it never sees graph proposals.

The routing that decides this is keyword-driven (see §5), so an ordinary-looking message that happens to pair a workflow verb with a domain noun is enough to trigger a replacement.

### 7.3 Other behaviors of the shipped wiring

- Failures are soft. Both `replace_graph` and `stage_patch` are wrapped in `try/except`; a failure appends `graph_stage_failed: ...` or `patch_stage_failed: ...` to `planner.warnings` and the request still returns 200.
- `replace_graph` rewrites the incoming graph's `case_id` and `domain` to the active session's values. A `brain` graph compiled against a session registered as `prostate` is stored with `domain="prostate"` even though `planner.graph.domain` in the same response reads `"brain"`.
- If `BrainService.reply(...)` raises, `/api/chat` substitutes `{"mode": "error", "error": ..., "reply": None, "patch_reason": ...}`. That dict has no `planner_metadata`, so it does not satisfy the §1 contract; callers must handle it defensively.

### 7.4 Still outstanding

- an accept / reject / undo gate for graph proposals
- a way to stage a graph proposal without activating it

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
