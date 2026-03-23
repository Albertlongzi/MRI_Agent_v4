# V4 Frontend Handoff

这份文档是给前端单独拆分开发用的。目标不是继续沿用当前静态页面，而是把 `MRI_Agent_v4` 的后端接口、数据模型、现有前端代码入口一次交接清楚，方便你在本地重写 UI。

## 1. 当前前端代码

当前前端还是一个无框架静态版本，代码都在：

- [index.html](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/index.html)
- [styles.css](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/styles.css)
- [app.js](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/app.js)

辅助说明：

- [apps/web/README.md](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/README.md)

如果你要本地重写前端，最推荐的做法是：

- 保留 `apps/api` 作为后端
- 你在本地单开一个新的前端项目，比如 `frontend/` 或单独 repo
- 只把这里的 API contract 当成数据源

## 2. 当前后端入口

后端入口在：

- [main.py](/home/longz2/common/medgemma/MRI_Agent_v4/apps/api/main.py)

后端当前已经允许跨域：

- `allow_origins=["*"]`
- `allow_methods=["*"]`
- `allow_headers=["*"]`

所以你本地独立前端可以直接请求 HPC 上的 API，只要网络/tunnel 通。

## 3. 推荐的本地前端适配方式

推荐两种模式：

### 3.1 本地 dev server + SSH tunnel

如果后端在 HPC 节点上跑：

- 本地浏览器访问你自己的前端 dev server，例如 `http://127.0.0.1:3000`
- 前端请求通过 tunnel 指向 HPC 后端，例如 `http://127.0.0.1:18008`

前端建议配置一个环境变量：

```bash
VITE_API_BASE_URL=http://127.0.0.1:18008
```

或者：

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:18008
```

### 3.2 本地 dev server 代理 `/api`

如果你用 Vite/Next.js，也可以直接把 `/api` 代理到后端。

这样前端代码里只要请求：

```text
/api/session
/api/graph
/api/chat
```

## 4. 稳定 API 面

下面这些接口可以视为当前前端应直接依赖的稳定面。

### Health / Planner

- `GET /api/health`
- `GET /api/planner/health`

用途：

- 顶部状态条
- 显示 Brain 是否接到本地 `vLLM`

### Session / Graph / Events

- `GET /api/session`
- `GET /api/graph`
- `GET /api/events`

用途：

- 初始化页面主状态
- 刷新 graph、chat、artifact、event timeline

### Chat / Patch / Execute

- `POST /api/chat`
- `POST /api/patch`
- `POST /api/proposals/apply-latest`
- `POST /api/execute/next`
- `POST /api/execute/until-done`
- `POST /api/reset`

用途：

- 聊天
- 人类插入 review checkpoint
- 应用 proposal
- 单步执行/全流程执行
- reset session

### Tool / Runtime Metadata

- `GET /api/tools`
- `GET /api/domains`
- `GET /api/capabilities`
- `GET /api/tools/bridge/health`
- `GET /api/runtime/profiles`
- `GET /api/runtime/tools/{tool_name}`

用途：

- 工具目录
- domain / capability 面板
- runtime profile 可视化

### Artifact Serving

- `GET /artifacts/...`

用途：

- 直接渲染 `json/txt/svg`
- 以后也可扩展到 image / nifti viewer

## 5. 最重要的数据模型

模型定义在：

- [models.py](/home/longz2/common/medgemma/MRI_Agent_v4/packages/schemas/models.py)

前端最需要关心的是这几个：

- `MockSession`
- `CaseState`
- `ActionGraph`
- `ActionNode`
- `ActionEdge`
- `ArtifactRef`
- `GraphEvent`
- `ExecutionPatch`

## 6. 推荐的前端 TypeScript 接口

你本地重写前端时，可以先按下面这套来建类型。

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

## 7. 接口返回格式

### 7.1 `GET /api/session`

返回 `MockSession`。

### 7.2 `GET /api/graph`

返回 `ActionGraph`。

### 7.3 `GET /api/events`

返回 `GraphEvent[]`。

### 7.4 `POST /api/chat`

请求体：

```json
{
  "message": "who are you"
}
```

响应重点字段：

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
  "planner": {
    "mode": "llm | llm_filtered | error | disabled",
    "patch_reason": null
  },
  "patch": {},
  "proposal_count": 1
}
```

前端适配建议：

- 优先用 `response.session` 和 `response.graph` 覆盖本地状态
- `response.reply` 只作为兜底显示
- `response.patch` 存在时，说明 Brain 或 heuristic 提出了新 proposal

### 7.5 `POST /api/patch`

请求体：

```json
{
  "reason": "Add a human review checkpoint before segmentation."
}
```

响应：

```json
{
  "patch": {},
  "proposal_count": 1
}
```

### 7.6 `POST /api/proposals/apply-latest`

响应：

- 成功时带 `graph` 和 `session`
- 没有 proposal 时：

```json
{
  "applied": false,
  "reason": "no proposals available",
  "graph": {}
}
```

### 7.7 `POST /api/execute/next`

响应重点：

```json
{
  "executed": true,
  "node_id": "identify_sequences",
  "status": "succeeded",
  "message": "...",
  "artifact_ids": [],
  "graph": {},
  "session": {}
}
```

### 7.8 `POST /api/reset`

响应：

```json
{
  "status": "reset",
  "session": {}
}
```

## 8. Artifact URI 规则

`ArtifactRef.uri` 目前通常长这样：

```text
artifacts/graph-prostate-demo/05_generate-report/report.json
```

前端应统一转成：

```text
${API_BASE_URL}/artifacts/graph-prostate-demo/05_generate-report/report.json
```

建议封装：

```ts
export function artifactUrl(apiBaseUrl: string, uri: string): string {
  if (/^(https?:|data:|blob:)/.test(uri)) return uri;
  const normalized = uri.replace(/^\/+/, "").replace(/^artifacts\//, "");
  return `${apiBaseUrl.replace(/\/$/, "")}/artifacts/${normalized}`;
}
```

## 9. 前端最小页面状态建议

如果你本地重写，我建议最小 state 只保留：

- `session`
- `graph`
- `events`
- `selectedNodeId`
- `selectedArtifactId`
- `plannerHealth`
- `bridgeHealth`
- `toolCatalog`
- `runtimeProfiles`

拖拽画布布局建议完全前端本地管理，例如：

- `nodeLayouts: Record<string, { x: number; y: number }>`
- 后期再决定要不要持久化回后端

## 10. 目前后端对前端的假设

目前后端并不要求你使用现有静态页面。

它只假设前端会：

- 拉取 `session / graph / events`
- 发送 `chat`
- 发送 `patch`
- 发送 `execute`
- 打开 `/artifacts/...`

所以你可以完全重写 UI，不需要兼容现有 DOM 结构。

## 11. 你本地重写时我建议保留的交互块

推荐保留这四块，但实现方式你可以全部重做：

- `chat panel`
- `graph canvas`
- `artifact viewer`
- `inspector`

## 12. 不稳定项

下面这些目前还不算冻结：

- Brain 输出是否升级成 structured graph patch
- node layout 是否后端持久化
- runtime profile 是否进一步细化为 job launcher schema
- graph 节点是否补充更多 domain-specific UI metadata

所以前端最好把这些做成可选字段，不要写死。

## 13. 最推荐的下一步

你本地前端可以先只接这 6 个接口：

- `GET /api/health`
- `GET /api/planner/health`
- `GET /api/session`
- `POST /api/chat`
- `POST /api/execute/next`
- `GET /artifacts/...`

先把：

- chat
- square graph canvas
- node selection
- artifact preview

这四件事跑通，再逐步补 `patch / apply proposal / runtime profiles`。
