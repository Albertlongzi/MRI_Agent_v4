# V4 Planner Output Contract

审计日期：2026-03-20

本文定义 `packages/planner` 当前对外返回的主输出 contract。

目标很明确：

- planner 主输出不再是纯聊天文本
- planner 现在优先返回 `graph` 或 `patch`
- `reply` 仅作为 operator-facing 附属说明

## 1. 返回顶层结构

`BrainService.reply(...)` 返回一个 JSON-serializable dict，字段如下：

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

说明：

- `mode="graph"` 时，`graph` 是主结果，`patch` 为空。
- `mode="patch"` 时，`patch` 是主结果，`graph` 为空。
- `mode="reply"` 时，没有结构化 proposal，只有附属文本回复。
- `reply` 永远是附属输出，不应再被视为 planner 主结果。

## 2. 当前已实现的两条主路径

### 2.1 Semantic IR -> draft graph

当前 planner 的内部顺序是：

1. 从用户输入提取 `IntentSpec`
2. 可选地让 LLM 在 semantic planner prompt 约束下补一个 intent draft
3. 基于 `IntentSpec` 推导 domain / capabilities / constraints / preferences
4. 交给 compiler 生成 `ActionGraph`
5. 用 semantic validator 检查 graph 是否漏掉用户明确要求

这让 graph 不再直接承载语义解析职责，而只是 compiler 输出。

当前 `IntentSpec` 的最小语义字段是：

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

提取规则：

- `explicit_requested_capabilities` 只收用户直接点名的能力，如 `register / segment / classify / report`
- `explicit_requested_capabilities` 现在也支持 `lesion / roi_features`
- `inferred_requested_capabilities` 只收编译器补全用的能力，如 `full_pipeline`
- `constraints` 记录硬约束，如 `before_segmentation`、`human_review_required`
- `preferences` 记录软偏好，如 `short report`

### 2.2 自然语言 -> draft graph

当前已实现最小路径已经从 prostate-only 推到 domain-aware / capability-aware：

- planner 会结合 `user_message`
- `case_state.domain`
- 当前 domain catalog / capability catalog

共同决定：

- graph 属于哪个 domain
- 应该落哪些 capability
- 对应选择哪些 tool

当前支持的 domain：

- `prostate`
- `brain`
- `cardiac`

当前支持的 materialized capability：

- `full_pipeline`
- `register`
- `segment`
- `classify`
- `report`

示例：

- prostate workup: `identify_sequences -> register_to_reference -> segment_prostate -> package_vlm_evidence -> generate_report`
- brain classify/report: `identify_sequences -> brats_mri_segmentation -> extract_roi_features -> classify_brain_glioma_grade -> generate_report`
- cardiac report: `identify_sequences -> segment_cardiac_cine -> generate_report`

graph 仍保留一个已完成的 `intake_case` planner node 作为 workflow 入口。

### 2.3 自然语言 -> typed patch

当前已实现最小路径：

- 输入 `pause before segmentation`
- planner 生成合法 `ExecutionPatch`
- patch 目标落在当前 active graph 上

当前 patch proposal 使用：

- `op="insert_checkpoint"`
- `target="<segmentation node id>"`
- `value.after_node="<registration node id>"`

其效果是：

- 在 registration 后插入一个人工 review checkpoint
- 再恢复到 segmentation 节点继续执行

## 3. 最小自检规则

planner 在返回结构化 proposal 前，会做最小合法性校验。

### Graph 校验

- `node_id` 必须唯一
- `depends_on` 不能引用不存在的节点
- `tool_name` 必须属于当前已知 tool catalog
- `edge.from_node` / `edge.to_node` 不能引用不存在的节点

### Semantic graph 校验

- graph 不能漏掉用户显式要求的 capability
- 显式要求的 `roi_features` 不能被简化成只做 segmentation/report
- `reply` 文本不能替代 graph 覆盖校验

### Patch 校验

- `insert_checkpoint.target` 必须是现有 graph node
- `value.after_node` 必须是现有 graph node

校验结果写入：

- `planner_metadata.validation_passed`
- `planner_metadata.validation_errors`
- `warnings`

## 4. 降级语义

LLM 不再决定 planner 是否能产出主结果。

当前语义是：

- graph / patch proposal 由 deterministic heuristic planner 负责生成
- LLM 只负责补一段短的 operator-facing `reply`

因此即使 LLM down：

- planner 仍可返回 `mode="graph"` 或 `mode="patch"` 的主结果
- `planner_metadata.llm_status` 会标记为 `disabled` / `error` / `llm_filtered`
- `warnings` 会带上降级原因

这保证了：

- planner 不会因为 chat layer 挂掉而失去 proposal 能力
- `reply` 失败不会阻断结构化 graph / patch 产出

## 5. 当前 intent 路由

已实现的 intent：

- `graph_domain_workup`
- `patch_review_before_segmentation`
- `reply`

当前最小 intent 规则：

- 包含 domain + workflow / segment / classify / register / report 语义的请求：走 `mode="graph"`
- 包含 `pause/review/checkpoint before segmentation`：走 `mode="patch"`
- 其他请求：走 `mode="reply"`

## 6. planner_metadata.extras 扩展

当前 `mode="graph"` 时，`planner_metadata.extras` 至少会包含：

- `domain`
- `proposal_kind`
- `requested_capabilities`
- `available_domain_capabilities`
- `selected_tools`
- `runtime_hints`

其中 `runtime_hints` 会给出每个已选 tool 当前解析到的 runtime profile，例如：

- `profile_id`
- `launcher`
- `gpu`
- `ssh_host`

这让 planner proposal 在 graph draft 阶段就能体现运行前提，尤其是 GPU tool 可通过 `ssh esplhpc-cp082` 执行。

## 7. 对 Agent 3 的最小 wiring 需求

本轮没有主动改 `apps/api/**`。

如果 Agent 3 要把 planner proposal 真正接进 API / state，最小 wiring 是：

1. 当 `/api/chat` 收到 `planner.mode="graph"` 时，把 `planner.graph` 写成一个 staged proposal，而不是只留在 response 里。
2. 当 `/api/chat` 收到 `planner.mode="patch"` 时，优先使用 `planner.patch`，不要再退回 heuristic `preview_patch(reason=...)`。
3. 给 graph proposal 增加“accept / replace active graph”的明确入口。

## 8. 复现方式

### Prostate Graph Draft Demo

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
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
cd /home/longz2/common/medgemma/MRI_Agent_v4
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
cd /home/longz2/common/medgemma/MRI_Agent_v4
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
