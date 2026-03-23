# V4 Compiler Layer

更新日期：2026-03-20

这份文档定义 `MRI_Agent_v4` 当前最小 compiler 输入。

目标很明确：

- 不让模型直接拼最终 graph
- 让 planner 先形成 `IntentSpec`
- 再由 compiler 按 tool contract / dependency rule / capability expansion rule 材料化成 graph

## 1. Compiler 输入

当前 compiler 的输入对象是 `IntentSpec`，核心字段包括：

- `intent`
- `domain`
- `user_message`
- `case_id`
- `graph_id`
- `root_goal`
- `requested_capabilities`
- `available_capabilities`
- `available_tools`
- `case_state`

这个输入只描述意图，不描述最终节点列表。

## 2. Tool Contracts

tool contract 是 compiler 的第一层元数据。

当前最小字段：

- `tool_name`
- `domains`
- `capabilities`
- `required_inputs`
- `produced_outputs`
- `runtime_profile`
- `notes`

用途：

- 告诉 compiler 某个 tool 能做什么
- 告诉 compiler 某个 tool 需要什么前置输入
- 告诉 compiler 这个 tool 大概率落在哪个 runtime profile 上

## 3. Dependency Rules

dependency rule 是 compiler 的第二层元数据。

当前最小字段：

- `rule_name`
- `target_tool`
- `depends_on`
- `reason`

用途：

- 把“tool 之间怎么接”从 graph 里提出来
- 让 graph 生成遵循规则，而不是靠模型顺手写节点顺序

## 4. Capability Expansion Rules

capability expansion rule 是 compiler 的第三层元数据。

当前最小字段：

- `rule_name`
- `domain`
- `when_any`
- `select_tools`
- `reason`
- `dependency_overrides`

用途：

- 根据 capability 自动补工具
- 根据 capability 自动补依赖
- 让能力扩展成为规则，而不是硬模板

### Prostate lesion example

如果 intent 里出现 `lesion`、`classify` 或 `roi_features`，compiler 会额外展开：

- `detect_lesion_candidates`
- `extract_roi_features`

这两个节点不是最终 graph 的“手写模板”，而是 capability expansion rule 的结果。

## 5. Compiler 输出

compiler 的输出是已材料化的 `ActionGraph`，同时保留 trace：

- `selected_tools`
- `applied_rules`
- `compiler_input`
- `warnings`

这让 planner 可以继续做自然语言解释，但不能绕过 compiler 直接构 graph。

## 6. 当前最小落地

已落地的 domain：

- `prostate`
- `brain`
- `cardiac`

已落地的 compiler 目标：

- tool auto-selection
- dependency filling
- graph generation
- prostate lesion expansion
- explicit capability coverage validation
