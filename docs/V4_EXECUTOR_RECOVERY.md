# V4 Executor Recovery / Rerun Semantics

审计日期：2026-03-20

本文冻结 `MRI_Agent_v4` 当前 executor recovery 语义：rerun-from-node、attempt history、failure -> patch -> continue。

## 1. 新增状态与字段

`ActionNode` 新增最小 recovery 字段：

- `attempt_count`
- `current_attempt_id`
- `rerun_from`
- `supersedes`
- `attempt_history`

`attempt_history` 的单条记录最小包含：

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

artifact metadata 现在也会带：

- `attempt_id`
- `rerun_from`
- `supersedes`

runtime `case_state.json` 的 stage record 现在会带：

- `attempt_id`
- `rerun_from`
- `supersedes`

## 2. Recovery 状态机

当前最小状态机：

- `failed`
  - 节点执行失败，graph 进入 `failed`
- `patched`
  - human patch 已落地，目标节点被标记为可继续恢复
  - graph 进入 `paused`
- `ready`
  - 节点可直接重新执行
- `running`
  - 当前 attempt 正在执行
- `succeeded`
  - 当前 attempt 成功
- `planned`
  - downstream 已失效，但要等上游恢复后再执行

语义区分：

- `reset`
  - 新 graph / 新 run
  - 清掉旧 runtime workspace
- `rerun-from-node`
  - 同一 graph 上的新 attempt
  - upstream 保留
  - target + downstream 失效后重跑

## 3. Rerun-From-Node 定义

`POST /api/execute/rerun-from-node`

请求体：

```json
{
  "node_id": "segment_prostate",
  "reason": "manual QC rerun"
}
```

精确定义：

1. 找到目标节点。
2. 计算目标节点及其所有 downstream。
3. upstream 节点保持不变。
4. target 节点：
   - 记录 `rerun_from=<target>`
   - 记录 `supersedes=<latest_attempt_id>`
   - 状态改为 `ready`
5. downstream 节点：
   - 记录 `rerun_from=<target>`
   - 状态改为 `planned`
6. 当前 graph version +1。
7. 追加 `rerun_requested` event。

注意：

- rerun 不会删除旧 artifact。
- rerun 也不会覆盖旧 attempt history。
- 新 attempt 真正开始时，才会生成新的 `attempt_id`。

## 4. Failure -> Patch -> Continue

当前闭环：

1. 某 node `failed`
2. operator/apply patch
3. patch 直接影响的 node 标为 `patched`
4. downstream 标为 `planned`
5. graph 从 `failed` 转为 `paused`
6. 调 `execute_next` 或 `execute_until_done`
7. executor 从 `patched` node 继续执行

当前 patch apply 后的 invalidation 规则：

- `update_node`
- `reroute_dependency`
- `insert_checkpoint`

这些操作会触发 target + downstream 失效并重跑。

当前 human patch 边界：

- `editable=false` 的 node 不允许 `update_node`
- `editable=false` 的 node 不允许 `reroute_dependency`

## 5. 旧 Artifact 如何保留

保留策略：

- graph-level `artifacts` 只追加，不回收旧 artifact
- 新 attempt 产物写入新的 step output 目录
- 旧 artifact 保留其旧的 `attempt_id`
- 新 artifact 带新的 `attempt_id`
- node 当前 `artifact_refs` 只指向当前 attempt 的最新产物

因此：

- audit 可以同时看到 old/new attempt artifact
- 不会出现静默覆盖
- rerun 后 UI 可以按 `artifact.metadata.attempt_id` 分组

## 6. Event 与 Graph Version

事件策略：

- `node_started` / `node_finished` / `node_failed` payload 带 `attempt_id`
- `artifact_added` payload 带 `attempt_id`
- `rerun_requested` 明确记录受影响节点
- `patch_applied` 明确记录 `affected_nodes`

version 策略：

- 每次 node 执行完成或失败：`graph.version += 1`
- 每次 patch apply：`graph.version += 1`
- 每次 rerun request：`graph.version += 1`

attempt 与 version 关系：

- version 是 graph mutation 序列号
- attempt 是 node execution 序列号
- 二者不等价，但都可审计

## 7. API 变化

新增：

- `POST /api/execute/rerun-from-node`

保留：

- `POST /api/proposals/apply-latest`
- `POST /api/execute/next`
- `POST /api/execute/until-done`

当前“continue”语义不需要新接口：

- patch apply 后直接继续调 `execute_next` / `execute_until_done`

## 8. 已验证路径

自动化已验证：

- 故障注入后 `failed -> patch -> continue -> succeeded`
- `rerun-from-node` 后 old/new attempt artifact 并存
- 相关回归共 `7 passed`

命令：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
PYTHONPATH=/home/longz2/common/medgemma/MRI_Agent_v4/.venv/lib/python3.9/site-packages:$PYTHONPATH \
python -m pytest -q tests/test_executor_recovery.py tests/test_executor_contracts.py tests/test_state_api.py
```

真实 GPU 验证：

- 已确认执行面会把 GPU step 转到 `ssh esplhpc-cp082`
- 已确认 rerun request、attempt history、runtime stage records 都能落盘
- 当前 `segment_prostate` 在远端 `apptainer` worker 上因为环境缺 `pydantic_core` 而失败

这说明：

- recovery/rerun 机制本身已工作
- 当前残余阻塞在 runtime/container 环境，不在本轮 executor scope

## 9. 残余限制

- 还没有 bounded retry policy；当前是 operator-driven rerun
- 还没有专门的 graph-level recovery UI
- patch 影响节点的判定仍是最小规则，不是完整静态分析
- 远端 `apptainer` worker 环境仍可能阻塞真实 GPU rerun 成功收敛
