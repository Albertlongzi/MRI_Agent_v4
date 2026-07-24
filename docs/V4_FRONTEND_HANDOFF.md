# V4 Frontend Handoff

Revised: 2026-07-24 — §4 route list, §6 `ActionNode`, and §7.4 `planner` shape corrected against `apps/api/main.py` and `packages/schemas/models.py`; §7.9–§7.11 added.

This document describes the frontend's contract with the backend, so that the frontend can be developed independently. The goal is not to keep building on the current static page, but to hand over the `MRI_Agent_v4` backend API surface, its data models, and the entry points of the existing frontend code in one place, so the UI can be rewritten from scratch.

## 1. Current frontend code

The current frontend is still a framework-free static version. All of its code lives in:

- [index.html](../apps/web/index.html)
- [styles.css](../apps/web/styles.css)
- [app.js](../apps/web/app.js)

Supporting notes:

- [apps/web/README.md](../apps/web/README.md)

The recommended approach for a from-scratch rewrite is:

- keep `apps/api` as the backend
- start a separate frontend project, for example under `frontend/` or in its own repo
- treat the API contract described here as the only data source

## 2. Current backend entry point

The backend entry point is:

- [main.py](../apps/api/main.py)

The backend already allows cross-origin requests:

- `allow_origins=["*"]`
- `allow_methods=["*"]`
- `allow_headers=["*"]`

A standalone local frontend can therefore call the API on the HPC directly, as long as the network path or tunnel is open.

## 3. Recommended ways to connect a local frontend

Two patterns are recommended:

### 3.1 Local dev server + SSH tunnel

When the backend runs on an HPC node:

- the local browser hits the frontend's own dev server, for example `http://127.0.0.1:3000`
- frontend requests are pointed through the tunnel at the HPC backend, for example `http://127.0.0.1:18008`

The frontend should be configured with an environment variable:

```bash
VITE_API_BASE_URL=http://127.0.0.1:18008
```

or:

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:18008
```

### 3.2 Local dev server proxying `/api`

With Vite or Next.js, `/api` can instead be proxied straight to the backend.

Frontend code then only needs to request:

```text
/api/session
/api/graph
/api/chat
```

## 4. Stable API surface

The endpoints below can be treated as the stable surface a frontend should depend on directly.

### Health / Planner

- `GET /api/health`
- `GET /api/planner/health`

Used for:

- the top status bar
- showing whether the Brain is connected to the local `vLLM`

### Session / Graph / Events

- `GET /api/session`
- `GET /api/graph`
- `GET /api/events`
- `GET /api/events/stream` — Server-Sent Events (`text/event-stream`)
- `POST /api/session`
- `POST /api/cases/register` — alias; it calls the same handler as `POST /api/session`

Used for:

- initializing the page's main state
- refreshing the graph, chat, artifacts, and event timeline
- live-tailing the event timeline over SSE instead of re-fetching `GET /api/events`
- registering a non-demo case as the active session

Both `GET /api/events` and `GET /api/events/stream` accept `after_event_id` and only return events for the currently active graph. `GET /api/events/stream` also accepts `poll_interval` (seconds, `0.1`–`5.0`, default `0.5`).

See `V4_STATE_API_LIFECYCLE.md` for the session registration body and the SSE frame semantics.

### Chat / Patch / Execute

- `POST /api/chat`
- `POST /api/patch`
- `POST /api/proposals/apply-latest`
- `POST /api/execute/next`
- `POST /api/execute/until-done`
- `POST /api/execute/rerun-from-node`
- `POST /api/reset`

Used for:

- chat
- inserting a human review checkpoint
- applying a proposal
- single-step execution / full-pipeline execution
- re-running from a chosen node and invalidating everything downstream of it
- reset session

### Tool / Runtime Metadata

- `GET /api/tools`
- `GET /api/domains`
- `GET /api/capabilities`
- `GET /api/tools/bridge/health`
- `GET /api/runtime/profiles`
- `GET /api/runtime/tools/{tool_name}`

Used for:

- the tool catalog
- the domain / capability panel
- runtime profile visualization

### Artifact Serving

- `GET /artifacts/...`

Used for:

- rendering `json/txt/svg` directly
- later extending to an image / NIfTI viewer

## 5. Key data models

The models are defined in:

- [models.py](../packages/schemas/models.py)

The ones the frontend most needs to care about are:

- `MockSession`
- `CaseState`
- `ActionGraph`
- `ActionNode`
- `ActionEdge`
- `ArtifactRef`
- `GraphEvent`
- `ExecutionPatch`

## 6. Suggested frontend TypeScript interfaces

A local rewrite can start by declaring the types below.

```ts
export type GraphStatus = "draft" | "ready" | "running" | "paused" | "completed" | "failed";
export type NodeStatus = "planned" | "ready" | "running" | "blocked" | "succeeded" | "failed" | "skipped" | "patched";
export type NodeKind = "tool" | "human" | "planner" | "review" | "merge" | "branch" | "finalize";

export interface CaseState {
  case_id: string;
  domain: string;
  input_root: string;
  sequence_index: Record<string, string>;
  available_modalities: string[];
  active_graph_id?: string | null;
  active_node_id?: string | null;
  selected_artifacts: string[];
  last_error?: string | null;
  last_event_id?: string | null;
  ui_focus: Record<string, string>;
}

export interface ActionNode {
  node_id: string;
  kind: NodeKind;
  title: string;
  action_type: string;
  tool_name?: string | null;
  status: NodeStatus;
  depends_on: string[];
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  checks: string[];
  artifact_refs: string[];
  owner: string;
  editable: boolean;
  notes?: string | null;
  // Retry / rerun provenance. These are always present in the JSON
  // (`attempt_count` defaults to 0, `attempt_history` to []).
  attempt_count: number;
  current_attempt_id?: string | null;
  rerun_from?: string | null;
  supersedes?: string | null;
  attempt_history: Array<Record<string, unknown>>;
}

export interface ActionEdge {
  edge_id: string;
  from_node: string;
  to_node: string;
  type: "control" | "data" | "approval" | "feedback";
  label?: string | null;
  artifact_key?: string | null;
}

export interface ArtifactRef {
  artifact_id: string;
  node_id: string;
  name: string;
  kind: string;
  uri: string;
  role: "input" | "output" | "evidence" | "preview" | "intermediate";
  mime_type?: string | null;
  visible: boolean;
  metadata: Record<string, unknown>;
}

export interface GraphEvent {
  event_id: string;
  graph_id: string;
  ts: string;
  actor_type: "supervisor" | "specialist" | "human" | "executor" | "system";
  actor_id: string;
  event_type: string;
  target_id: string;
  payload: Record<string, unknown>;
  parent_event_id?: string | null;
}

export interface PatchOperation {
  op: string;
  target?: string | null;
  value: Record<string, unknown>;
}

export interface ExecutionPatch {
  patch_id: string;
  graph_id: string;
  author_type: "human" | "supervisor" | "specialist" | "system";
  author_id: string;
  timestamp: string;
  reason: string;
  operations: PatchOperation[];
  applies_to_version: number;
  result: "applied" | "rejected" | "superseded" | "merged" | "preview";
}

export interface ActionGraph {
  graph_id: string;
  case_id: string;
  domain: string;
  status: GraphStatus;
  version: number;
  root_goal: string;
  nodes: ActionNode[];
  edges: ActionEdge[];
  artifacts: ArtifactRef[];
  events: GraphEvent[];
  proposals: ExecutionPatch[];
  patch_history: ExecutionPatch[];
}

export interface MockSession {
  session_id: string;
  case_state: CaseState;
  graph: ActionGraph;
  chat_history: Array<{ role: string; content: string }>;
}
```

## 7. Endpoint response formats

### 7.1 `GET /api/session`

Returns a `MockSession`.

### 7.2 `GET /api/graph`

Returns an `ActionGraph`.

### 7.3 `GET /api/events`

Returns a `GraphEvent[]`.

### 7.4 `POST /api/chat`

Request body:

```json
{
  "message": "who are you"
}
```

Key response fields:

```json
{
  "reply": {
    "role": "assistant",
    "content": "..."
  },
  "chat_history": [],
  "event": {},
  "graph": {},
  "session": {},
  "planner": {},
  "patch": {},
  "proposal_count": 1
}
```

`reply`, `chat_history`, `event`, `graph`, and `session` are always present. `patch` and `proposal_count` appear only when a proposal was staged or previewed on this turn.

#### The `planner` field

`response.planner` is **not** a two-key summary. The API assigns the entire dict returned by `BrainService.reply(...)` to it, so it carries the full planner contract described in `V4_PLANNER_OUTPUT_CONTRACT.md`:

```json
{
  "planner": {
    "mode": "graph | patch | reply",
    "intent_spec": {},
    "reply": { "role": "assistant", "content": "..." },
    "graph": null,
    "patch": null,
    "warnings": [],
    "planner_metadata": {
      "intent": "graph_domain_workup",
      "source": "heuristic | llm | hybrid",
      "llm_status": "llm | heuristic | disabled | error | llm_filtered | not_used",
      "model": null,
      "base_url": null,
      "latency_ms": null,
      "validation_passed": true,
      "validation_errors": [],
      "fallback_used": false,
      "extras": {}
    },
    "patch_reason": null
  }
}
```

Notes on `planner.mode`:

- the real values are `graph`, `patch`, and `reply` — these come from the `PlannerMode` literal in `packages/planner/contracts.py`
- `llm`, `llm_filtered`, `heuristic`, `disabled`, `error`, and `not_used` are **not** `mode` values; they live one level down in `planner.planner_metadata.llm_status`
- `error` is the one extra `mode` value the API itself can produce: if `BrainService.reply(...)` raises, `apps/api/main.py` substitutes a much smaller dict with only `{"mode": "error", "error": "<message>", "reply": null, "patch_reason": "..."}`, so a frontend must not assume `planner.planner_metadata` exists
- the string `mock` never appears in `planner.mode`. It is a fallback for the chat event's `reply_source` only, and surfaces at `response.event.payload.reply_source`

Notes on duplication:

- `planner.graph` is populated only when `mode="graph"`; it is the same graph the API just wrote into the active session, so it duplicates top-level `response.graph`
- `planner.patch` is populated only when `mode="patch"`; top-level `response.patch` is the staged proposal derived from it
- `response.event.payload.reply_metadata` is another copy of `planner`, minus the `reply` key

Notes for the frontend:

- prefer overwriting local state with `response.session` and `response.graph`
- treat `response.reply` as a display-only fallback
- when `response.patch` is present, the Brain or a heuristic has raised a new proposal
- read `planner.mode` to decide what actually happened, and read `planner.planner_metadata.llm_status` to render Brain health
- **`mode="graph"` has already replaced the active graph by the time the response is returned** — see §7.4.1

#### 7.4.1 Graph proposals are applied without a human gate

Graph and patch results are wired asymmetrically in `apps/api/main.py`:

- `mode="graph"` calls `STORE.replace_graph(...)` inline. The planner's graph becomes the active graph on that chat turn, a `graph_replaced` event is appended, and `graph.proposals` stays empty. There is no accept/reject step.
- `mode="patch"` calls `STORE.stage_patch(...)`. That only appends to `graph.proposals`; the change lands only after `POST /api/proposals/apply-latest`.

So a UI that renders proposals as "pending, awaiting approval" will never see a graph proposal in that state. If a graph turn needs an operator gate, the frontend has to implement it — for example by diffing against the previous `response.graph` and offering an undo — because the backend does not provide one.

Two related behaviors worth knowing:

- if `replace_graph` raises, the failure is swallowed into `planner.warnings` as `graph_stage_failed: ...`, and the response is otherwise a normal 200
- `replace_graph` overwrites the incoming graph's `case_id` and `domain` with the current session's values. A brain-domain graph compiled while the session is registered as `prostate` will arrive with `planner.graph.domain = "brain"` but `response.graph.domain = "prostate"`.

### 7.5 `POST /api/patch`

Request body:

```json
{
  "reason": "Add a human review checkpoint before segmentation."
}
```

Response:

```json
{
  "patch": {},
  "proposal_count": 1
}
```

### 7.6 `POST /api/proposals/apply-latest`

Response:

- on success, carries `graph` and `session`
- when there is no proposal:

```json
{
  "applied": false,
  "reason": "no proposals available",
  "graph": {}
}
```

### 7.7 `POST /api/execute/next`

Key response fields:

```json
{
  "executed": true,
  "node_id": "identify_sequences",
  "status": "succeeded",
  "message": "...",
  "artifact_ids": [],
  "event_ids": [],
  "graph": {},
  "session": {}
}
```

`status` is the node's terminal `NodeStatus`. A node whose tool has no executor handler comes back as `failed` with the reason in `message`; it is never reported as `succeeded`.

### 7.8 `POST /api/reset`

Response:

```json
{
  "status": "reset",
  "session": {}
}
```

### 7.9 `POST /api/session` and `POST /api/cases/register`

Both routes share one request model and one handler.

Request body:

```json
{
  "case_id": "manual_case_001",
  "input_root": "/tmp/manual_case_001",
  "domain": "brain",
  "session_id": "manual-session"
}
```

`case_id` and `input_root` are required. `domain` defaults to `prostate`. `session_id` is generated if omitted. Extra fields are rejected.

Response:

```json
{
  "status": "registered",
  "session": {},
  "graph": {},
  "events": []
}
```

### 7.10 `POST /api/execute/rerun-from-node`

Request body:

```json
{
  "node_id": "segment_prostate",
  "reason": "operator rerun"
}
```

`reason` defaults to `"operator rerun"`.

Response:

```json
{
  "rerun": true,
  "node_id": "segment_prostate",
  "reason": "operator rerun",
  "affected_nodes": [],
  "event": {},
  "graph": {},
  "session": {}
}
```

`affected_nodes` is the target node plus everything downstream that was invalidated. See `V4_EXECUTOR_RECOVERY.md`.

### 7.11 `GET /api/events/stream`

Server-Sent Events. Query parameters: `after_event_id` (optional cursor) and `poll_interval` (seconds, `0.1`–`5.0`, default `0.5`).

The stream emits, in order:

1. one `event: stream_ready` frame with `{"status": "connected", "graph_id": "...", "after_event_id": ...}`
2. any backlog after the cursor, then new events as they land

Each event frame uses the `GraphEvent.event_type` as the SSE `event:` name, the serialized `GraphEvent` as `data:`, and the `event_id` as the SSE `id:`. While idle the server writes `: keep-alive` comment lines.

```ts
const es = new EventSource(`${apiBaseUrl}/api/events/stream?after_event_id=${cursor ?? ""}`);
es.addEventListener("node_finished", (e) => applyEvent(JSON.parse(e.data)));
```

Because the event name varies per event type, an `onmessage` handler alone will not see these frames — either subscribe per event type or attach listeners for the types listed in `V4_STATE_API_LIFECYCLE.md` §3.2.

## 8. Artifact URI rules

`ArtifactRef.uri` currently usually looks like this:

```text
artifacts/graph-prostate-demo/05_generate-report/report.json
```

The frontend should uniformly convert it to:

```text
${API_BASE_URL}/artifacts/graph-prostate-demo/05_generate-report/report.json
```

A suggested helper:

```ts
export function artifactUrl(apiBaseUrl: string, uri: string): string {
  if (/^(https?:|data:|blob:)/.test(uri)) return uri;
  const normalized = uri.replace(/^\/+/, "").replace(/^artifacts\//, "");
  return `${apiBaseUrl.replace(/\/$/, "")}/artifacts/${normalized}`;
}
```

## 9. Suggested minimal frontend page state

For a rewrite, the recommended minimal state is just:

- `session`
- `graph`
- `events`
- `selectedNodeId`
- `selectedArtifactId`
- `plannerHealth`
- `bridgeHealth`
- `toolCatalog`
- `runtimeProfiles`

Drag-and-drop canvas layout is best managed entirely on the frontend, for example:

- `nodeLayouts: Record<string, { x: number; y: number }>`
- whether to persist it back to the backend can be decided later

## 10. What the backend currently assumes about the frontend

The backend does not require the existing static page to be used.

It only assumes the frontend will:

- fetch `session / graph / events`
- send `chat`
- send `patch`
- send `execute`
- open `/artifacts/...`

The UI can therefore be rewritten completely, with no need to stay compatible with the existing DOM structure.

## 11. Interaction blocks worth keeping in a rewrite

These four blocks are worth keeping, but their implementation can be redone entirely:

- `chat panel`
- `graph canvas`
- `artifact viewer`
- `inspector`

## 12. Unstable items

The following are not yet frozen:

- whether the Brain's output is upgraded to a structured graph patch
- whether node layout is persisted by the backend
- whether runtime profiles are refined further into a job launcher schema
- whether graph nodes gain more domain-specific UI metadata

The frontend should therefore model these as optional fields rather than hard-coding them.

## 13. Recommended next step

A local frontend can start against just these six endpoints:

- `GET /api/health`
- `GET /api/planner/health`
- `GET /api/session`
- `POST /api/chat`
- `POST /api/execute/next`
- `GET /artifacts/...`

Get these four things working first:

- chat
- square graph canvas
- node selection
- artifact preview

Once those four work, add `patch / apply proposal / runtime profiles` incrementally.

Note on the event timeline: this six-endpoint starter set deliberately omits `GET /api/events`, and the shipped static page in `apps/web/app.js` only fetches `/api/events` once per refresh. That is a starter shortcut, not a recommendation. As soon as the timeline needs to update during execution, use `GET /api/events/stream` (§7.11) rather than re-fetching `/api/events` on a timer.
