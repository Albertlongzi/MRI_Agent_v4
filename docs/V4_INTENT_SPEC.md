# V4 IntentSpec / Semantic Planning IR

更新日期：2026-03-20

这份文档定义 `MRI_Agent_v4` planner 的语义意图层。

目标不是直接生成最终 `ActionGraph`，而是先把用户输入归一化成一个更稳定、可编译的 `IntentSpec`。

当前实现已经支持两条来源：

- heuristic intent extraction
- optional LLM semantic intent extraction

即使启用 LLM，它也只能产出 `IntentSpec` 风格的语义对象，不能直接产出最终 graph。

## 1. IntentSpec

`IntentSpec` 是 planner 的最小语义对象，字段如下：

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

### 字段语义

- `intent_id`：给这次语义请求一个稳定 id，便于日志和审计。
- `intent_type`：`graph_request`、`patch_request`、`reply_request`。
- `domain`：当前请求归属的 domain，例如 `prostate`、`brain`、`cardiac`。
- `normalized_request`：清洗后的用户原文，去掉多余空白但不改变语义。
- `explicit_requested_capabilities`：用户直接点名的能力。
- `inferred_requested_capabilities`：编译器为补全 workflow 额外推导的能力。
- `constraints`：必须满足的硬约束。
- `preferences`：软偏好。
- `target_graph_id`：当前要编译到的 graph id，或 draft graph id。
- `target_node_id`：如果请求明显指向某个已有节点，这里可填。
- `patch_anchor`：语义级 patch 锚点，例如 `before_segmentation`。
- `notes`：补充说明，供 compiler 和审计使用。

## 2. 提取规则

### 2.1 Explicit requested capabilities

只收用户明确说出口的能力：

- `register`
- `segment`
- `classify`
- `report`
- `qa`
- `lesion`
- `roi_features`
- `full_pipeline`

### 2.2 Inferred requested capabilities

只收编译器补全所需的能力：

- 用户说 `workflow`、`workup`、`inspect`、`analyze` 时，可推 `full_pipeline`
- 当用户没有明确能力，但上下文明显是单病例工作流时，可补默认 `report`

### 2.3 Constraints

当前最小约束词表：

- `before_segmentation`
- `human_review_required`
- `exclude_capability`

### 2.4 Preferences

当前最小偏好词表：

- `report_length=short`
- `report_length=detailed`
- `risk_tolerance=conservative`

## 3. Compiler 关系

现在 planner 的内部顺序是：

1. 从用户输入提取 `IntentSpec`
2. 可选地让 LLM 在 `AGENT.md / SOUL.md` 约束下补一个 semantic intent draft
3. 合并显式 capability / constraint / preference
4. 再编译成 `ActionGraph` 或 `ExecutionPatch`

这意味着：

- graph 不再承载原始意图解析职责
- patch 不再只靠关键词 heuristics
- 以后如果要支持更多 domain，只需要先扩 `IntentSpec`，再扩 compiler

## 4. 当前实现边界

当前 `IntentSpec` 只是最小骨架，不是完整 planner ontology。

尚未做的部分：

- 约束冲突解析
- 多轮对话的 intent merge
- 分级 confidence
- 多 subagent proposal 合并
- 更强的 intent-level repair / follow-up graph patch merge
