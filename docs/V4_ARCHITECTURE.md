# MRI_Agent v4 Architecture

This document describes the target architecture for `MRI_Agent_v4`: a natural-language radiology workstation that preserves the BCER execution philosophy but replaces template-anchored planning with capability-anchored action graphs.

The design goal is not to reproduce the `v3` benchmark stack. The goal is to make radiology workflows feel conversational, editable, visible, and auditable.

## 1. System Shape

`v4` is organized around four cooperating surfaces:

- `web`: the operator workstation
- `api`: session, case, graph, artifact, and event APIs
- `planner`: the Brain that turns natural language into structured action graphs
- `executor`: the Cerebellum that validates and executes graphs deterministically
- `worker`: tool runners for heavy MRI workloads

The key architectural rule is simple: the planner proposes, the executor validates, and the worker computes.

## 2. Core Principle

The Brain should not emit raw tool calls as its primary interface. It should emit a constrained, typed action graph that can be inspected and edited before execution.

The Cerebellum should not invent plan structure. It should enforce contracts, resolve dependencies, manage retries, and write provenance.

The operator should always be able to see:

- what the system thinks the workflow is
- which artifacts are available
- which node failed and why
- what can be patched or rerun

## 3. Service Boundaries

### 3.1 Web

The web app is the radiology workstation UI. It should contain:

- a chat panel for natural-language interaction
- a graph canvas for the live action graph
- a viewer for images, overlays, and evidence
- an inspector for node arguments, logs, patches, and artifacts

The web layer should not execute MRI tools directly. It only renders state, submits user actions, and subscribes to events.

### 3.2 API

The API layer owns:

- case registration
- session lifecycle
- graph persistence
- artifact indexing
- event streaming
- human approval and patch endpoints

The API should be stateless with respect to computation. It should store and retrieve state, but not perform image processing.

### 3.3 Planner

The planner is the Brain. It receives:

- the user request
- current case metadata
- the current graph state
- relevant artifacts and evidence
- domain constraints and available capabilities

It returns:

- a proposed action graph or graph patch
- confidence and rationale metadata
- optional repair suggestions when a graph is partially failing

The planner may be implemented with one or more subagents, but it must present a single coherent proposal to the system.

### 3.4 Executor

The executor is the Cerebellum. It owns:

- graph validation
- contract enforcement
- dependency resolution
- deterministic retries
- reflection dispatch
- run state transitions
- provenance emission

The executor should treat planner output as untrusted input until validated.

### 3.5 Worker

Workers execute the expensive domain operations:

- DICOM ingestion
- registration
- segmentation
- reconstruction
- feature extraction
- report generation
- QA retrieval

Workers should be isolated from UI and planner concerns. Their job is to consume typed inputs and return typed outputs plus artifacts.

### 3.6 Runtime Profiles

`v4` should separate orchestration from execution environment choice.

Each tool should resolve to a small named `runtime_profile`, for example:

- `control-plane`
- `legacy-qwen-vllm`
- `nnunet-gpu`
- `apptainer-medgemma`

The executor should own this resolution step. The planner can suggest a tool, but it should not directly select a shell environment or container image.

This matters because MRI tools do not share one clean dependency stack. Some are safe in the main API environment, some need a specialized GPU conda env, and some are best isolated behind `Apptainer` on HPC.

## 4. Request Flow

A typical request should move through the system as follows:

1. The user speaks in natural language.
2. The planner converts the request into an action graph proposal.
3. The UI renders the proposal and highlights dependencies and artifacts.
4. The user approves, patches, or edits the graph.
5. The executor validates the graph and schedules runnable nodes.
6. Workers execute nodes and write artifacts.
7. The executor emits node and run events.
8. The UI updates the graph, viewer, and inspector live.
9. The system produces a final report and provenance bundle.

This flow keeps the plan visible and editable without sacrificing deterministic execution.

Implementation status as of 2026-07-24: step 4 is only half-built. Patches do go through the approve step — the planner stages them into `graph.proposals` and they land only on `POST /api/proposals/apply-latest`. Graph proposals do not: `POST /api/chat` calls `STORE.replace_graph(...)` inline, so a `mode="graph"` turn replaces the active graph with no approval and no undo. This section describes the target design, not current behavior. See `V4_PLANNER_OUTPUT_CONTRACT.md` §7.

## 5. Action Graph Model

`v4` should center the system on a first-class `ActionGraph`.

Minimum concepts:

- `ActionGraph`
- `ActionNode`
- `ActionEdge`
- `ArtifactRef`
- `ExecutionPatch`
- `GraphEvent`

Each node should carry:

- a stable identifier
- an action type
- a tool or operation name
- structured arguments
- dependency edges
- status
- input artifact references
- output artifact references
- validation checks
- human editability flags

The graph should support three kinds of nodes:

- planner-authored nodes
- operator-authored nodes
- runtime-generated repair or retry nodes

This is the main replacement for template-anchored planning.

## 6. Persistence And Eventing

The system should persist two related streams of truth:

- durable graph state
- append-only event history

Suggested storage model:

- `sessions`
- `cases`
- `graphs`
- `graph_nodes`
- `graph_edges`
- `artifacts`
- `events`
- `approvals`
- `patches`

The event stream should capture:

- planner proposals
- user edits
- graph validation results
- node start and completion
- tool stderr/stdout summaries
- artifact writes
- reflection attempts
- finalization events

The event log should be append-only. Graph state can be materialized from the event stream, but a cached snapshot is useful for UI performance.

## 7. Artifact Model

Artifacts are the primary bridge between execution and inspection.

An artifact should record:

- artifact id
- case id
- run id
- node id
- type
- path or URI
- human-readable label
- mime type
- provenance metadata
- checksum or version marker when practical

Artifact categories should include:

- raw inputs
- derived images
- masks and overlays
- feature tables
- QC images
- reports
- logs
- planner/debug traces

Artifacts should be addressable from the graph and renderable in the viewer and inspector.

## 8. Viewer Integration

The viewer should not be a passive image panel. It should be graph-aware.

Expected viewer behaviors:

- select a node and show its input/output artifacts
- click an artifact and highlight the producing node
- overlay masks, registrations, and QC outputs
- inspect slices and evidence tied to a node
- annotate or pin artifacts for downstream use

The viewer should support MRI-first workflows. A practical first implementation can focus on NIfTI and derived overlays before expanding to full DICOM workstation behavior.

## 9. Planner / Executor Boundary

This boundary is the central BCER invariant in `v4`.

Planner responsibilities:

- understand user intent
- choose capabilities
- propose graph structure
- suggest missing steps
- suggest repair patches

Executor responsibilities:

- validate graph and arguments
- enforce tool legality and scope
- resolve dependencies
- apply safe defaults only when allowed by policy
- execute nodes
- stop unsafe or incomplete runs

The planner should be free to reason. The executor should be strict.

## 10. Safety Model

`v4` should keep the `v3` safety posture, but present it as product behavior rather than benchmark machinery.

Required controls:

- scoped path validation
- domain-aware tool allowlists
- schema validation for all planner and operator inputs
- artifact provenance tracking
- bounded retry and reflection policies
- explicit human approval on high-risk steps
- isolated worker execution

Operationally, the executor should reject:

- out-of-scope paths
- illegal tool requests
- malformed graph references
- unapproved destructive operations
- ambiguous or incomplete inputs when policy requires certainty

The safety model should be explicit in the UI. Users should know when the system is blocking, repairing, or waiting for approval.

## 11. Multi-Subagent Supervision

`v4` can support multiple subagents, but only under a single supervisory Brain.

Recommended pattern:

- `Supervisor`
  - owns global state and final decisions
- `Planner subagent`
  - proposes graph structure
- `Evidence subagent`
  - inspects artifacts and supports QA
- `Recovery subagent`
  - suggests repairs for failing nodes
- `Reporting subagent`
  - composes the final radiology summary

The supervisor must maintain one shared source of truth:

- one action graph
- one artifact registry
- one event stream
- one case state

Subagents may contribute independently, but they should not maintain divergent private plans.

## 12. Implementation Phases

### Phase 0: Product Skeleton

- create `MRI_Agent_v4/`
- define docs and package layout
- define repository conventions
- keep the scope product-oriented, not benchmark-oriented

### Phase 1: Shared Schemas

- define `ActionGraph`, `ActionNode`, `ArtifactRef`, `GraphEvent`
- define graph patch and approval models
- define session and case metadata models

### Phase 2: API And Event Stream

- implement session and graph endpoints
- implement append-only event writing
- implement materialized graph snapshots

### Phase 3: Minimal Planner / Executor

- implement natural-language planning to action graph
- implement graph validation
- implement a small set of deterministic MRI tools
- implement a basic repair loop

### Phase 4: Workstation UI

- render chat, graph, viewer, and inspector
- stream events live
- support human patch and rerun flows

### Phase 5: Domain Expansion

- expand tool coverage
- add richer evidence extraction
- improve report generation
- add optional subagent specialization

## 13. Non-Goals For First Release

Do not start `v4` with:

- benchmark runners
- fault-injection suites
- template migration tooling
- full DICOM workstation parity
- multi-user collaboration
- unrestricted tool exposure

Those can come later, but they should not define the first architecture.

## 14. Bottom Line

`v4` should feel like a radiology workstation that can think in natural language, show its working graph, and let the operator steer execution safely.

BCER survives in `v4` as a contract:

- Brain proposes
- Cerebellum verifies and executes
- the user can see and intervene
- every artifact and decision is traceable

The main architectural change is that the plan is no longer template-shaped. It is capability-shaped.
