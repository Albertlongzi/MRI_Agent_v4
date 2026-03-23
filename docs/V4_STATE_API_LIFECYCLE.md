# V4 State / API Lifecycle

审计日期：2026-03-20

本文冻结 `MRI_Agent_v4` 当前最小 durable state、session registration、event stream 与 reset/recovery 语义。

## 1. Canonical State Shape

当前 canonical state 由 `packages/state/store.py` 管理，底层使用 SQLite。

默认数据库路径：

- `MRI_Agent_v4/state/v4_state.sqlite3`

可通过环境变量覆盖：

- `MRI_AGENT_V4_STATE_DB=/path/to/custom.sqlite3`

当前最小 durable 数据模型：

- `sessions`
  - active session metadata
  - 当前 graph id / version / status
  - clean reset template (`template_session_json`)
  - chat history
- `case_states`
  - 当前 `CaseState` snapshot
- `graph_snapshots`
  - 当前 `ActionGraph` snapshot
- `events`
  - append-only event log
  - 按 `(session_id, graph_id, event_id)` 去重
- `patches`
  - proposal / applied patch 持久化

其中：

- `graph_snapshots.graph_json` 是当前 graph 的 authoritative snapshot。
- `events` 是 append-only 历史。
- `template_session_json` 是 reset 的 clean source，不跟随执行态污染。

## 2. Session Lifecycle

### 2.1 Boot

API 启动时：

1. 打开 SQLite schema。
2. 读取 `sessions.is_active=1` 的记录。
3. 如果存在：
   - 恢复 `case_state`
   - 恢复 `graph_snapshot`
   - 恢复 `chat_history`
4. 如果不存在：
   - bootstrap 默认 demo session
   - 立即写入 SQLite

### 2.2 Registration

新 session 通过 `POST /api/session` 或 `POST /api/cases/register` 创建。

请求体：

```json
{
  "case_id": "manual_case_001",
  "input_root": "/tmp/manual_case_001",
  "domain": "brain",
  "session_id": "manual-session"
}
```

语义：

- `case_id` 必填
- `input_root` 必填
- `domain` 可选，默认 `prostate`
- `session_id` 可选，不传则自动生成

注册后：

- 新 session 被标记为 active
- 写入初始 graph snapshot
- 追加初始 events：
  - `case_registered`
  - `graph_initialized`

### 2.3 Reset

`POST /api/reset` 现在的语义是：

1. 删除当前 graph 对应的 runtime/artifact workspace。
2. 使用 clean template 重新生成一个新的 graph id。
3. 保留 session 的 case/domain/input 信息。
4. 把新 graph 设为 active graph。
5. 新 run 的 runtime workspace 从空状态重新生成。

这保证：

- 旧 `stage_outputs` 不会污染新 run
- reset 后 graph/workspace 都是新的
- 连续 rerun 时 `call_id` 会从新 graph 的 clean state 重新开始

## 3. Eventing

### 3.1 Pull API

- `GET /api/events`
- `GET /api/events?after_event_id=event-0002`

返回当前 active graph 的事件列表，不会混入旧 graph 的历史事件。

### 3.2 SSE

- `GET /api/events/stream`
- `GET /api/events/stream?after_event_id=event-0002`

返回 `text/event-stream`。

当前行为：

- 连接建立后先发一个 `stream_ready`
- 然后回放 backlog（如果有）
- 再持续轮询 SQLite 中当前 active graph 的新事件
- 空闲时发送 keep-alive 注释行

前端可直接消费：

- `event: stream_ready`
- `event: case_registered`
- `event: node_started`
- `event: node_finished`
- `event: patch_previewed`
- 其他 graph events

## 4. Chat / Planner Wiring

`POST /api/chat` 当前最小接入规则：

- `planner.mode="graph"`：
  - 将 planner 返回的 `graph` 写入当前 active graph
  - 追加 `graph_replaced` event
- `planner.mode="patch"`：
  - 优先使用 planner 返回的 typed `patch`
  - 作为 proposal 持久化到 graph + SQLite
- 仅当 planner 没给 typed patch 时：
  - 才回退到旧的 heuristic `preview_patch(reason=...)`

这保证 API 不再只把 planner proposal 留在 response 里。

## 5. Stable API Surface

保留兼容：

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

新增：

- `POST /api/session`
- `POST /api/cases/register`
- `GET /api/events/stream`

## 6. Verified Behaviors

已验证：

- API 重启后 active session 能从 SQLite 恢复。
- `POST /api/session` 可创建非 demo case 的 session。
- SSE 可返回 `stream_ready` 与 backlog events。
- prostate demo 可完成一轮真实执行，其中 `segment_prostate` 通过 runtime profile SSH 到 `esplhpc-cp082`。
- `reset -> rerun` 后 graph id 变化，旧 runtime workspace 被删除，新 run 的 `segment_prostate-001` 不混入旧记录。

## 7. Minimal Validation Commands

### 7.1 Local API

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
PYTHONPATH=/home/longz2/common/medgemma/MRI_Agent_v4/.venv/lib/python3.9/site-packages:$PYTHONPATH python run_demo.py
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

1. 注册一个 session。
2. 停掉 API。
3. 重启 API。
4. 再次请求 `GET /api/session`。
5. 应看到同一个 active session / graph 被恢复。

### 7.3 GPU Path

`segment_prostate` 当前 runtime profile：

- `profile_id=cp082-qwen-vllm`
- `launcher=ssh`
- `ssh_host=esplhpc-cp082`

因此控制面可以在无 GPU 提交节点上发起执行，但 GPU step 会转发到 `esplhpc-cp082`。
