# MRI_Agent v4 Docs

这个目录现在只保留 `MRI_Agent_v4` 的核心设计、核心 contract、当前剩余问题，以及人工测试说明。

建议阅读顺序：

1. `V4_PRODUCT_SPEC.md`
2. `V4_ARCHITECTURE.md`
3. `V4_ACTION_GRAPH_SCHEMA.md`
4. `V4_IMPLEMENTATION_SEQUENCE.md`
5. `V4_OPEN_ISSUES.md`
6. `V4_MANUAL_TEST_PLAN.md`

核心设计：

- `V4_PRODUCT_SPEC.md`
- `V4_ARCHITECTURE.md`
- `V4_ACTION_GRAPH_SCHEMA.md`
- `V4_IMPLEMENTATION_SEQUENCE.md`

核心实现 contract：

- `V4_INTENT_SPEC.md`
- `V4_PLANNER_OUTPUT_CONTRACT.md`
- `V4_EXECUTOR_STAGE_DATA_CONTRACT.md`
- `V4_EXECUTOR_RECOVERY.md`
- `V4_STATE_API_LIFECYCLE.md`
- `V4_TOOL_RUNTIME_STRATEGY.md`

协作文档：

- `V4_FRONTEND_HANDOFF.md`

当前状态：

- `V4_OPEN_ISSUES.md`
- `V4_MANUAL_TEST_PLAN.md`

说明：

- 之前用于并行 agent 分工、阶段性审计、临时 readiness/freeze 的中间文档已经移除。
- 如果后续需要再次做阶段性攻关，建议在 `docs/` 外单独维护临时执行文档，避免污染核心设计面。
