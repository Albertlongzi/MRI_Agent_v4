# V4 IntentSpec / Semantic Planning IR

Last updated: 2026-03-20

This document defines the semantic intent layer of the `MRI_Agent_v4` planner.

The goal is not to generate the final `ActionGraph` directly, but to first normalize user input into a more stable, compilable `IntentSpec`.

The current implementation supports two sources:

- heuristic intent extraction
- optional LLM semantic intent extraction

Even when the LLM is enabled, it can only produce an `IntentSpec`-style semantic object; it cannot produce the final graph directly.

## 1. IntentSpec

`IntentSpec` is the planner's minimal semantic object. Its fields are:

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

### Field semantics

- `intent_id`: a stable id for this semantic request, which makes logging and auditing easier.
- `intent_type`: one of `graph_request`, `patch_request`, or `reply_request`.
- `domain`: the domain the request belongs to, for example `prostate`, `brain`, or `cardiac`.
- `normalized_request`: the user's original text after cleanup, with redundant whitespace removed but the meaning unchanged.
- `explicit_requested_capabilities`: capabilities the user named directly.
- `inferred_requested_capabilities`: additional capabilities the compiler derives in order to complete the workflow.
- `constraints`: hard constraints that must be satisfied.
- `preferences`: soft preferences.
- `target_graph_id`: the id of the graph currently being compiled into, or the id of the draft graph.
- `target_node_id`: filled in when the request clearly points at a specific existing node.
- `patch_anchor`: a semantic-level anchor for a patch, for example `before_segmentation`.
- `notes`: supplementary notes, for use by the compiler and for auditing.

## 2. Extraction rules

### 2.1 Explicit requested capabilities

Only capabilities the user states outright are collected here:

- `register`
- `segment`
- `classify`
- `report`
- `qa`
- `lesion`
- `roi_features`
- `full_pipeline`

### 2.2 Inferred requested capabilities

Only capabilities the compiler needs in order to fill out the workflow are collected here:

- when the user says `workflow`, `workup`, `inspect`, or `analyze`, `full_pipeline` can be inferred
- when the user names no capability at all but the context is clearly a single-case workflow, a default `report` can be added

### 2.3 Constraints

The current minimal constraint vocabulary:

- `before_segmentation`
- `human_review_required`
- `exclude_capability`

### 2.4 Preferences

The current minimal preference vocabulary:

- `report_length=short`
- `report_length=detailed`
- `risk_tolerance=conservative`

## 3. Relationship to the compiler

The planner's internal sequence is now:

1. Extract an `IntentSpec` from the user's input
2. Optionally have the LLM add a semantic intent draft, constrained by `AGENT.md` / `SOUL.md`
3. Merge the explicit capabilities, constraints, and preferences
4. Compile the result into an `ActionGraph` or an `ExecutionPatch`

This means that:

- the graph no longer carries responsibility for parsing raw intent
- patches no longer depend on keyword heuristics alone
- supporting more domains later only requires extending `IntentSpec` first, then the compiler

## 4. Current implementation boundaries

`IntentSpec` today is only a minimal skeleton, not a complete planner ontology.

Not yet implemented:

- constraint conflict resolution
- intent merging across multi-turn conversations
- graded confidence
- merging proposals from multiple subagents
- stronger intent-level repair / follow-up graph patch merging
