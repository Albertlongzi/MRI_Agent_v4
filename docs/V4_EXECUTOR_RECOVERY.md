# V4 Executor Recovery / Rerun Semantics

Audit date: 2026-03-20

This document freezes the executor recovery semantics currently implemented in `MRI_Agent_v4`: rerun-from-node, attempt history, and failure -> patch -> continue.

## 1. New States and Fields

`ActionNode` gains a minimal set of recovery fields:

- `attempt_count`
- `current_attempt_id`
- `rerun_from`
- `supersedes`
- `attempt_history`

Each record in `attempt_history` contains at least:

- `attempt_id`
- `status`
- `started_at`
- `finished_at`
- `rerun_from`
- `supersedes`
- `artifact_ids`
- `event_ids`
- `message`
- `error`
- `output_snapshot`

Artifact metadata now also carries:

- `attempt_id`
- `rerun_from`
- `supersedes`

Stage records in the runtime `case_state.json` now carry:

- `attempt_id`
- `rerun_from`
- `supersedes`

## 2. Recovery State Machine

The current minimal state machine:

- `failed`
  - The node failed to execute and the graph moves to `failed`
- `patched`
  - A human patch has landed and the target node is marked as ready to resume
  - The graph moves to `paused`
- `ready`
  - The node can be executed again immediately
- `running`
  - The current attempt is executing
- `succeeded`
  - The current attempt succeeded
- `planned`
  - Downstream work has been invalidated and waits for upstream recovery before it runs

The two rerun semantics are distinct:

- `reset`
  - A new graph / a new run
  - Clears the old runtime workspace
- `rerun-from-node`
  - A new attempt on the same graph
  - Upstream is preserved
  - The target plus its downstream are invalidated and re-run

## 3. Rerun-From-Node Definition

`POST /api/execute/rerun-from-node`

Request body:

```json
{
  "node_id": "segment_prostate",
  "reason": "manual QC rerun"
}
```

Precise definition:

1. Locate the target node.
2. Compute the target node and all of its downstream nodes.
3. Upstream nodes are left unchanged.
4. The target node:
   - Records `rerun_from=<target>`
   - Records `supersedes=<latest_attempt_id>`
   - Moves to status `ready`
5. Downstream nodes:
   - Record `rerun_from=<target>`
   - Move to status `planned`
6. The current graph version is incremented by 1.
7. A `rerun_requested` event is appended.

Note:

- A rerun never deletes old artifacts.
- A rerun never overwrites the existing attempt history.
- A new `attempt_id` is generated only once the new attempt actually starts.

## 4. Failure -> Patch -> Continue

The current loop:

1. Some node reaches `failed`
2. The operator applies a patch
3. Nodes directly affected by the patch are marked `patched`
4. Downstream nodes are marked `planned`
5. The graph moves from `failed` to `paused`
6. Call `execute_next` or `execute_until_done`
7. The executor resumes from the `patched` node

Invalidation rules that currently apply once a patch has been applied:

- `update_node`
- `reroute_dependency`
- `insert_checkpoint`

These operations invalidate the target plus its downstream nodes and cause them to be re-run.

Current boundaries on human patches:

- `update_node` is not allowed on a node with `editable=false`
- `reroute_dependency` is not allowed on a node with `editable=false`

## 5. How Old Artifacts Are Retained

Retention policy:

- The graph-level `artifacts` list is append-only; old artifacts are never reclaimed
- Outputs from a new attempt are written to a new step output directory
- Old artifacts keep their original `attempt_id`
- New artifacts carry the new `attempt_id`
- A node's current `artifact_refs` point only at the latest outputs of the current attempt

Consequently:

- An audit can see artifacts from both the old and the new attempt
- Nothing is ever silently overwritten
- After a rerun the UI can group artifacts by `artifact.metadata.attempt_id`

## 6. Events and Graph Version

Event policy:

- `node_started` / `node_finished` / `node_failed` payloads carry `attempt_id`
- `artifact_added` payloads carry `attempt_id`
- `rerun_requested` explicitly records the affected nodes
- `patch_applied` explicitly records `affected_nodes`

Version policy:

- Every time a node finishes or fails: `graph.version += 1`
- Every time a patch is applied: `graph.version += 1`
- Every time a rerun is requested: `graph.version += 1`

How attempts relate to versions:

- A version is a graph mutation sequence number
- An attempt is a node execution sequence number
- The two are not equivalent, but both are auditable

## 7. API Changes

Added:

- `POST /api/execute/rerun-from-node`

Unchanged:

- `POST /api/proposals/apply-latest`
- `POST /api/execute/next`
- `POST /api/execute/until-done`

The current "continue" semantics need no new endpoint:

- After a patch has been applied, simply call `execute_next` / `execute_until_done` again

## 8. Verified Paths

Verified by automated tests:

- `failed -> patch -> continue -> succeeded` after fault injection
- Old and new attempt artifacts coexist after `rerun-from-node`
- The related regression suite reports `7 passed`

Command:

```bash
cd /path/to/MRI_Agent_v4
PYTHONPATH=/path/to/MRI_Agent_v4/.venv/lib/python3.9/site-packages:$PYTHONPATH \
python -m pytest -q tests/test_executor_recovery.py tests/test_executor_contracts.py tests/test_state_api.py
```

Verified on real GPU hardware:

- Confirmed that the execution plane dispatches GPU steps over `ssh <gpu-node>`
- Confirmed that rerun requests, attempt history, and runtime stage records are all persisted to disk
- `segment_prostate` currently fails on the remote `apptainer` worker because that environment is missing `pydantic_core`

Which shows that:

- The recovery/rerun mechanism itself works
- The remaining blocker is the runtime/container environment, which is outside the scope of this round of executor work

## 9. Remaining Limitations

- There is no bounded retry policy yet; reruns are currently operator-driven
- There is no dedicated graph-level recovery UI yet
- Deciding which nodes a patch affects still uses a minimal rule set rather than full static analysis
- The remote `apptainer` worker environment can still block a real GPU rerun from converging successfully
