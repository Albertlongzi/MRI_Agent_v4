# MRI_Agent v4 Implementation Sequence

This document turns the initial `v4` design pack into a concrete build order.

## 1. First Milestone

Target:

- open a browser UI
- load a case
- render a mock action graph
- stream fake events
- inspect node details

This milestone should not run real MRI tools yet.

## 2. Recommended Build Order

### Step 1: Repo/bootstrap skeleton

- initialize `apps/web`
- initialize `apps/api`
- initialize `apps/worker`
- create `packages/schemas`
- create `packages/planner`
- create `packages/executor`
- create `packages/tools`

Deliverable:

- one command to boot the whole stack locally

### Step 2: Shared schemas first

- define `ActionGraph`
- define `ActionNode`
- define `ActionEdge`
- define `ArtifactRef`
- define `GraphEvent`
- define `ExecutionPatch`
- define `CaseState`

Deliverable:

- shared typed schema package used by both frontend and backend

### Step 3: Basic API

- create session endpoints
- create case registration endpoint
- create graph read/write endpoints
- create event stream endpoint
- create patch endpoint

Deliverable:

- a backend that can store and serve one graph-backed case session

### Step 4: Web workstation shell

- chat pane
- graph pane
- inspector pane
- placeholder viewer pane

Deliverable:

- clicking a node updates the inspector
- event stream updates the graph live

### Step 5: Minimal Brain

- natural language request in
- constrained graph proposal out
- no templates
- one small domain flow only

Suggested first domain:

- prostate or brain

Deliverable:

- user request creates a valid draft graph

### Step 6: Minimal Cerebellum

- validate graph
- schedule runnable nodes
- mark states
- write events
- record artifacts

Deliverable:

- graph can move from `draft` to `running` to `completed`

### Step 7: First real tools

Suggested first tool set:

- `identify_sequences`
- `register_to_reference`
- `segment_prostate` or `brats_mri_segmentation`
- `generate_report`

Deliverable:

- one end-to-end case with real artifacts

### Step 8: Human patch flow

- edit node args
- approve graph
- rerun from node
- accept or reject repair proposal

Deliverable:

- the operator can change the course of execution without leaving the UI

### Step 9: Viewer/artifact linkage

- link selected node to produced artifacts
- show overlays and QC outputs
- allow pinning an artifact for downstream use

Deliverable:

- graph, viewer, and artifact browser stay synchronized

### Step 10: Optional supervisor + specialist agents

- supervisor owns the graph
- specialists propose patches only
- no direct specialist mutation of canonical state

Deliverable:

- multi-agent assistance without split-brain state

## 3. Recommended MVP Tool Stack

Backend:

- Python
- FastAPI
- Pydantic
- SQLite

Frontend:

- React
- TypeScript
- React Flow
- a lightweight MRI viewer such as Niivue

Infra:

- local filesystem artifact store first
- SSE or websocket event streaming

## 4. What To Avoid Early

- full tool parity with `v3`
- complex auth
- multi-user sessions
- full DICOM workstation scope
- template migration
- benchmark harness carry-over

## 5. Suggested Next Coding Task

The most sensible first coding task is:

- scaffold `apps/web`, `apps/api`, and `packages/schemas`
- define the initial `ActionGraph` schema
- render one mock graph in the browser

That gives the project a visible center of gravity immediately.
