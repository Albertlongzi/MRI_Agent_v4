# V4 Action Graph Schema

Revised: 2026-07-24 — §4 `ActionNode` field list reconciled with `packages/schemas/models.py`.

This document defines the core intermediate representation for `MRI_Agent_v4`.
The goal is to preserve BCER's deterministic execution discipline while moving
from template-anchored planning to capability-anchored, natural-language-driven
action graphs.

The key design rule is simple: **there is one shared source of truth** for the
current case, the plan, the runtime state, and the artifacts. Subagents may
propose changes, but they do not own independent copies of the truth.

## 1. Design Goals

- Make planning editable, inspectable, and executable as a graph.
- Allow natural language to become structured actions incrementally.
- Preserve deterministic execution, provenance, and safety checks.
- Support human patching without breaking state consistency.
- Support multiple specialist subagents without fragmenting the plan state.

## 2. Core Objects

`v4` should use a small set of typed objects that are shared by planner,
executor, UI, and subagents.

- `ActionGraph`
- `ActionNode`
- `ActionEdge`
- `ArtifactRef`
- `GraphEvent`
- `ExecutionPatch`
- `CaseState`
- `SubagentProposal`

These objects should be serializable, versioned, and safe to persist.

## 3. ActionGraph

`ActionGraph` is the top-level object for a case session.

Minimum fields:

- `graph_id`: stable identifier for the session graph
- `case_id`: case/session identifier
- `domain`: `brain`, `prostate`, `cardiac`, or `unknown`
- `status`: `draft`, `ready`, `running`, `paused`, `completed`, `failed`
- `version`: monotonic graph version
- `root_goal`: user intent in normalized form
- `nodes`: list of `ActionNode`
- `edges`: list of `ActionEdge`
- `artifacts`: list of `ArtifactRef`
- `events`: append-only event stream
- `proposals`: optional subagent proposals not yet merged
- `patch_history`: human and system patches applied to the graph

The graph must be authoritative for:

- execution order
- current status
- selected artifacts
- failure history
- user-approved modifications

## 4. ActionNode

`ActionNode` represents one executable or reviewable step.

Minimum fields:

- `node_id`: unique within the graph
- `kind`: `tool`, `human`, `planner`, `review`, `merge`, `branch`, `finalize`
- `title`: short human-readable label
- `action_type`: normalized semantic action, for example `identify_sequences`
- `tool_name`: optional concrete tool, if `kind == tool`
- `status`: `planned`, `ready`, `running`, `blocked`, `succeeded`, `failed`,
  `skipped`, `patched`
- `depends_on`: list of upstream `node_id`
- `inputs`: structured arguments or references
- `outputs`: declared output slots
- `checks`: validation rules or expectations
- `artifact_refs`: links to produced or consumed artifacts
- `owner`: `supervisor`, `specialist:<name>`, `human`, or `executor`
- `editable`: boolean
- `notes`: optional planner or human notes

Retry / rerun provenance, added by the recovery work and present in every serialized node:

- `attempt_count`: integer, defaults to `0`
- `current_attempt_id`: optional attempt id
- `rerun_from`: optional node id this attempt was re-run from
- `supersedes`: optional attempt id this attempt replaces
- `attempt_history`: list of attempt records, defaults to `[]`

See `V4_EXECUTOR_RECOVERY.md` for the semantics of these five fields.

Not implemented: `retry_policy` was specified here in the original design but was never added to `ActionNode` in `packages/schemas/models.py`. Because `V4BaseModel` sets `extra="forbid"`, passing a `retry_policy` key raises a pydantic `ValidationError` rather than being ignored. Bounded retry is still operator-driven; see `V4_OPEN_ISSUES.md` §1.2.

Node rules:

- Each node must have a stable identity.
- A node may be proposed before it is executable.
- A node may be human-editable only until locked by policy.
- A node can point to artifacts, but artifacts remain separate objects.
- A node may represent a pure human checkpoint rather than a tool call.

## 5. ActionEdge

`ActionEdge` captures dependency and data flow.

Minimum fields:

- `edge_id`: unique identifier
- `from_node`: upstream `node_id`
- `to_node`: downstream `node_id`
- `type`: `control`, `data`, `approval`, `feedback`
- `label`: optional short semantic description
- `artifact_key`: optional output/input key carried by the edge

Edge rules:

- `control` edges define execution order.
- `data` edges define artifact or value flow.
- `approval` edges define human gating.
- `feedback` edges define recovery or refinement loops.

## 6. ArtifactRef

`ArtifactRef` is the canonical link between the graph and the filesystem or
other artifact stores.

Minimum fields:

- `artifact_id`: unique stable ID
- `node_id`: producing node
- `name`: logical artifact name
- `kind`: `nifti`, `dicom_manifest`, `json`, `csv`, `png`, `svg`, `text`,
  `report`, `log`, `mask`, `overlay`, `table`
- `uri`: storage location or logical path
- `mime_type`: optional MIME type
- `role`: `input`, `output`, `evidence`, `preview`, `intermediate`
- `schema_version`: optional artifact schema version
- `checksum`: optional integrity hash
- `visible`: boolean

Artifacts should always be referenced through the graph, never assumed from
implicit file naming alone.

## 7. GraphEvent

`GraphEvent` is the append-only audit record for all graph changes.

Minimum fields:

- `event_id`
- `graph_id`
- `ts`
- `actor_type`: `supervisor`, `specialist`, `human`, `executor`, `system`
- `actor_id`
- `event_type`: `proposed`, `merged`, `patched`, `approved`, `locked`,
  `node_started`, `node_finished`, `node_failed`, `artifact_added`,
  `artifact_selected`, `graph_rebased`
- `target_id`: node, edge, artifact, or graph ID
- `payload`: structured event payload
- `parent_event_id`: optional causal link

Events are the primary mechanism for traceability and replay.

## 8. ExecutionPatch

`ExecutionPatch` is how humans and subagents request graph edits.

Minimum fields:

- `patch_id`
- `graph_id`
- `author_type`: `human`, `supervisor`, `specialist`, `system`
- `author_id`
- `timestamp`
- `reason`
- `operations`: list of patch operations
- `applies_to_version`: graph version the patch was written against
- `result`: `applied`, `rejected`, `superseded`, `merged`

Supported patch operations:

- `add_node`
- `remove_node`
- `update_node`
- `add_edge`
- `remove_edge`
- `rebind_artifact`
- `lock_node`
- `unlock_node`
- `insert_checkpoint`
- `reroute_dependency`

Patch rules:

- Patches must be explicit and diff-like.
- Patches are validated before application.
- Patches can be human-authored or agent-authored.
- A patch never silently mutates the graph.

## 9. CaseState

`CaseState` is the shared runtime truth for the current case.

Minimum fields:

- `case_id`
- `domain`
- `input_root`
- `sequence_index`
- `available_modalities`
- `active_graph_id`
- `active_node_id`
- `selected_artifacts`
- `last_error`
- `last_event_id`
- `ui_focus`

`CaseState` should be treated as the runtime projection of the graph, not a
separate source of truth.

## 10. Shared Truth Model

Supervisor and specialists must operate on a single shared state model:

- the `ActionGraph` is the canonical plan
- the `CaseState` is the canonical runtime projection
- the `GraphEvent` stream is the canonical history
- the artifact store is the canonical data plane

Subagents may keep local scratch context, but they do not maintain authoritative
private copies of graph state. The only accepted way to change truth is:

1. propose a patch or event
2. validate it against the current graph version
3. apply it through the supervisor/executor boundary
4. emit an event
5. refresh the shared state projection

This prevents the common failure mode where multiple agents diverge and each
believes a different version of the plan.

## 11. Multi-Subagent Supervision Model

The recommended multi-agent hierarchy is:

- `Supervisor`
  - owns the graph
  - arbitrates patches
  - merges specialist proposals
  - resolves conflicts
  - finalizes what is executable
- `Specialist agents`
  - each handle a narrow function
  - produce typed proposals, not free-form side effects
- `Executor`
  - runs validated nodes
  - publishes events and artifacts

Suggested specialist roles:

- `IntakeAgent`
  - case completeness, modality inventory, input sanity
- `PlanningAgent`
  - action graph expansion from natural language
- `RecoveryAgent`
  - failure diagnosis and patch suggestions
- `EvidenceAgent`
  - artifact inspection and result validation
- `ReportingAgent`
  - report drafting and result packaging

Specialists should not directly mutate the graph. They should emit
`SubagentProposal` objects.

## 12. SubagentProposal

`SubagentProposal` is the bounded interface between specialist agents and the
supervisor.

Minimum fields:

- `proposal_id`
- `graph_id`
- `source_agent`
- `proposal_type`: `node_addition`, `node_update`, `edge_update`, `patch`,
  `review`, `evidence_note`
- `summary`
- `payload`
- `confidence`
- `rationale`
- `related_node_ids`
- `status`: `proposed`, `accepted`, `rejected`, `merged`

Supervisor behavior:

- collect proposals
- score for consistency and utility
- merge only validated changes
- emit a graph event for any accepted change

## 13. Natural Language To Graph

Natural language should be transformed incrementally, not in one rigid shot.

Example request:

- "Inspect this prostate case, register ADC to T2, segment the gland, and give
  me a short report."

Possible graph:

- `ReadCase`
- `IdentifySequences`
- `RegisterADCToT2`
- `SegmentProstate`
- `InspectArtifacts`
- `GenerateReport`

Example mapping:

- user intent -> `root_goal`
- "register ADC to T2" -> `ActionNode(action_type="register_to_reference")`
- "segment the gland" -> `ActionNode(action_type="segment_prostate")`
- "short report" -> `ActionNode(action_type="generate_report")`

The planner may add human checkpoints or evidence checks if the request is
underspecified.

## 14. Example Graph JSON

```json
{
  "graph_id": "g_001",
  "case_id": "case_014",
  "domain": "prostate",
  "status": "draft",
  "version": 1,
  "root_goal": "register ADC to T2, segment prostate, generate report",
  "nodes": [
    {
      "node_id": "n1",
      "kind": "tool",
      "title": "Identify sequences",
      "action_type": "identify_sequences",
      "tool_name": "identify_sequences",
      "status": "ready",
      "depends_on": [],
      "inputs": {"dicom_case_dir": "@case.input"},
      "outputs": {"sequence_index": "artifact"},
      "checks": ["must_discover_t2w", "must_discover_adc"],
      "artifact_refs": [],
      "owner": "supervisor",
      "editable": true
    }
  ],
  "edges": [],
  "artifacts": [],
  "events": [],
  "proposals": [],
  "patch_history": []
}
```

## 15. Example Human Patch

User action:

- "Use the T2w series instead of the ADC series for the downstream report."

Patch:

- `update_node` on the downstream node inputs
- `rebind_artifact` from the ADC artifact to the T2w artifact
- `insert_checkpoint` before final report generation

The patch must be version-checked against the current graph before it applies.

## 16. Suggested Execution Semantics

- Planning creates or updates a draft graph.
- Supervisor validates the graph against tools and policy.
- Human can inspect and patch before execution.
- Executor runs nodes in dependency order.
- Failures generate recovery events and patch proposals.
- Final report generation is just another graph node, not a special side channel.

## 17. Compatibility With BCER

This schema preserves BCER's important properties:

- the executor remains deterministic
- tool contracts remain typed
- recovery remains structured
- provenance remains explicit
- human oversight remains possible

What changes is the planning shape:

- from template selection to graph synthesis
- from fixed benchmark arms to flexible case workflows
- from isolated subagent opinions to a shared graph-backed truth model

## 18. Implementation Notes

- Use strict schema validation.
- Version every graph mutation.
- Keep the event stream append-only.
- Never let a specialist directly overwrite canonical graph state.
- Make graph rebasing explicit when a patch lands on a stale version.
- Persist artifacts separately from the graph, but always reference them from
  the graph.

