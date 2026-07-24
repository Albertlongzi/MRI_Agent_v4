# V4 State / API Lifecycle

Audit date: 2026-03-20
Revised: 2026-07-24 — §4 now records the graph/patch approval asymmetry.

This document freezes the current minimal durable state, session registration, event stream, and reset/recovery semantics of `MRI_Agent_v4`.

## 1. Canonical State Shape

Canonical state is managed by `packages/state/store.py`, backed by SQLite.

Default database path:

- `MRI_Agent_v4/state/v4_state.sqlite3`

Override via environment variable:

- `MRI_AGENT_V4_STATE_DB=/path/to/custom.sqlite3`

The current minimal durable data model:

- `sessions`
  - active session metadata
  - current graph id / version / status
  - clean reset template (`template_session_json`)
  - chat history
- `case_states`
  - current `CaseState` snapshot
- `graph_snapshots`
  - current `ActionGraph` snapshot
- `events`
  - append-only event log
  - deduplicated by `(session_id, graph_id, event_id)`
- `patches`
  - persisted proposal / applied patches

Within that model:

- `graph_snapshots.graph_json` is the authoritative snapshot of the current graph.
- `events` is append-only history.
- `template_session_json` is the clean source for reset; it is never polluted by execution state.

## 2. Session Lifecycle

### 2.1 Boot

When the API starts:

1. Open the SQLite schema.
2. Read the record with `sessions.is_active=1`.
3. If one exists:
   - restore `case_state`
   - restore `graph_snapshot`
   - restore `chat_history`
4. If none exists:
   - bootstrap the default demo session
   - write it to SQLite immediately

### 2.2 Registration

New sessions are created via `POST /api/session` or `POST /api/cases/register`.

Request body:

```json
{
  "case_id": "manual_case_001",
  "input_root": "/tmp/manual_case_001",
  "domain": "brain",
  "session_id": "manual-session"
}
```

Semantics:

- `case_id` is required
- `input_root` is required
- `domain` is optional and defaults to `prostate`
- `session_id` is optional and is generated automatically if omitted

After registration:

- the new session is marked active
- the initial graph snapshot is written
- the initial events are appended:
  - `case_registered`
  - `graph_initialized`

### 2.3 Reset

`POST /api/reset` now means:

1. Delete the runtime/artifact workspace belonging to the current graph.
2. Regenerate a new graph id from the clean template.
3. Preserve the session's case/domain/input information.
4. Set the new graph as the active graph.
5. Regenerate the runtime workspace for the new run from an empty state.

This guarantees that:

- old `stage_outputs` cannot pollute the new run
- both the graph and the workspace are new after a reset
- on repeated reruns, `call_id` restarts from the clean state of the new graph

## 3. Eventing

### 3.1 Pull API

- `GET /api/events`
- `GET /api/events?after_event_id=event-0002`

Returns the event list for the currently active graph; history from older graphs is never mixed in.

### 3.2 SSE

- `GET /api/events/stream`
- `GET /api/events/stream?after_event_id=event-0002`

Returns `text/event-stream`.

Current behavior:

- once the connection is established, a `stream_ready` is sent first
- then the backlog is replayed, if any
- then SQLite is polled continuously for new events on the currently active graph
- keep-alive comment lines are sent while idle

The frontend can consume these directly:

- `event: stream_ready`
- `event: case_registered`
- `event: node_started`
- `event: node_finished`
- `event: patch_previewed`
- other graph events

## 4. Chat / Planner Wiring

The current minimal wiring rules for `POST /api/chat`:

- `planner.mode="graph"`:
  - write the `graph` returned by the planner into the currently active graph
  - append a `graph_replaced` event
- `planner.mode="patch"`:
  - prefer the typed `patch` returned by the planner
  - persist it as a proposal into the graph and into SQLite
- only when the planner does not supply a typed patch, and only when `graph.proposals` is currently empty:
  - fall back to the older heuristic `preview_patch(reason=...)`

This guarantees that the API no longer leaves planner proposals sitting in the response only.

The two paths are not symmetric, and the difference matters operationally:

- the `graph` path writes straight through. `STORE.replace_graph(...)` makes the planner's graph the active graph during the chat turn itself. Nothing is added to `graph.proposals`, and there is no accept, reject, or undo endpoint for it.
- the `patch` path is gated. `STORE.stage_patch(...)` only appends to `graph.proposals`; the change takes effect only on `POST /api/proposals/apply-latest`.

So `POST /api/proposals/apply-latest` gates patches only. A single chat message that the planner routes to `mode="graph"` replaces the operator's current graph with no human confirmation. Two further details of `replace_graph`: it forces the incoming graph's `case_id` and `domain` to the active session's values, so a `brain` graph compiled against a session registered as `prostate` is stored with `domain="prostate"`; and if it raises, `/api/chat` swallows the failure into `planner.warnings` as `graph_stage_failed: ...` and still returns 200.

See `V4_PLANNER_OUTPUT_CONTRACT.md` §7.

## 5. Stable API Surface

Kept for compatibility:

- `GET /api/session`
- `GET /api/graph`
- `GET /api/events`
- `POST /api/chat`
- `POST /api/patch`
- `POST /api/proposals/apply-latest`
- `POST /api/execute/next`
- `POST /api/execute/until-done`
- `POST /api/execute/rerun-from-node`
- `POST /api/reset`

Newly added:

- `POST /api/session`
- `POST /api/cases/register`
- `GET /api/events/stream`

## 6. Verified Behaviors

Verified:

- After an API restart, the active session can be restored from SQLite.
- `POST /api/session` can create a session for a non-demo case.
- SSE returns `stream_ready` plus backlog events.
- The prostate demo can complete one round of real execution, in which `segment_prostate` reaches `<gpu-node>` over SSH via its runtime profile.
- After `reset -> rerun`, the graph id changes, the old runtime workspace is deleted, and the new run's `segment_prostate-001` is not contaminated by old records.

## 7. Minimal Validation Commands

### 7.1 Local API

```bash
cd /path/to/MRI_Agent_v4
PYTHONPATH=/path/to/MRI_Agent_v4/.venv/lib/python3.9/site-packages:$PYTHONPATH python run_demo.py
```

```bash
curl -s http://127.0.0.1:8008/api/health
```

```bash
curl -s -X POST http://127.0.0.1:8008/api/session \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"manual_case_001","input_root":"/tmp/manual_case_001","domain":"brain","session_id":"manual-session"}'
```

```bash
curl -N http://127.0.0.1:8008/api/events/stream
```

### 7.2 Recovery

1. Register a session.
2. Stop the API.
3. Restart the API.
4. Request `GET /api/session` again.
5. You should see the same active session / graph restored.

### 7.3 GPU Path

The current runtime profile for `segment_prostate`, as returned by `GET /api/runtime/tools/segment_prostate`:

- `profile_id=apptainer-medgemma`
- `launcher=apptainer`
- `ssh_host=${MRI_AGENT_V4_GPU_HOST}`
- `gpu=true`

`cp082-qwen-vllm` remains defined as the non-container SSH fallback, but it is no longer what `segment_prostate` resolves to. See `V4_TOOL_RUNTIME_STRATEGY.md` for the full mapping.

The control plane can therefore initiate execution from a submit node with no GPU, while the GPU step is forwarded to the configured GPU host.
