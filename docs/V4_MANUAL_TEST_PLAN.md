# V4 Manual Test Plan

更新日期：2026-03-20

这份文档是给你做实际人工测试用的。

目标不是一次性覆盖所有细节，而是用最少的测试轮次回答三个问题：

1. 现在的 `v4` 是否已经能作为真实 workstation backend 使用
2. 核心闭环是否真的成立
3. 剩下的问题更偏产品体验，还是底层真实性

## 1. 测试前准备

推荐环境：

- API 运行在当前机器
- planner LLM 走 `esplhpc-cp082`
- GPU-heavy segmentation 允许走 `cp082`

推荐先确认这些命令：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
PYTHONPATH=/home/longz2/common/medgemma/MRI_Agent_v4 .venv/bin/pytest -q
```

```bash
curl http://127.0.0.1:8008/api/health
curl http://127.0.0.1:8008/api/planner/health
```

如果 API 没起：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
PYTHONPATH=/home/longz2/common/medgemma/MRI_Agent_v4 .venv/bin/python -m apps.api.main
```

## 2. 第一轮：Happy Path

目标：

- 确认一条 prostate case 从 chat 到 report 真能闭环

建议输入：

- case: `/common/longz2/medgemma/MRI_Agent/demo/cases/sub-057`
- chat:
  `Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.`

你要看：

- `/api/chat` 返回 `mode=graph`
- `/api/graph` 出现完整 workflow
- `/api/execute/until-done` 最终 `graph_status=completed`
- `report.json` 存在
- `clinical_report.md` 存在
- `report.json` 里 `lesion_assessment_meta.segmentation_usable=true`
- `clinical_report.md` 不再出现：
  - `missing ADC and/or segmentation issues`
  - `Pipeline could not reliably assess lesions`

判定：

- 如果这些都成立，说明当前最核心的真实性闭环已经成立

## 3. 第二轮：Patch / Review / Continue

目标：

- 确认 planner patch 和 human-in-the-loop 现在是真能力，不是摆设

建议流程：

1. 先注册同一个 case
2. chat 输入：
   `pause before segmentation`
3. 查看 graph/proposal
4. apply latest proposal
5. 执行到 checkpoint
6. 再继续执行到完成

你要看：

- planner 返回 `mode=patch`
- graph 中真的插入了 review checkpoint
- apply patch 后 graph version 有变化
- execution 会停在 checkpoint，而不是直接越过
- 继续执行后最终仍可完成

判定：

- 如果成立，说明 graph patch 现在已经是可操作能力

## 4. 第三轮：Recovery / Rerun

目标：

- 确认失败后不是只能 reset 整图

建议流程：

1. 找一个节点手动触发失败，或选择已有失败 run
2. 调 `POST /api/execute/rerun-from-node`
3. 再执行

你要看：

- 目标 node 变成 `ready`
- downstream 变成 `planned`
- old artifact 没被覆盖
- new artifact 带新的 attempt
- graph 最后能恢复执行

重点观察：

- event stream
- artifact metadata
- attempt history

判定：

- 如果成立，说明 recovery 这条线已经从 demo 进入可用状态

## 5. 第四轮：Runtime / GPU / Container

目标：

- 确认 GPU-heavy tool 确实在正确 runtime 上跑

建议看：

- `segment_prostate` 相关 node outputs
- runtime case state
- provenance / runtime profile

你要确认：

- `runtime_profile=apptainer-medgemma` 或预期 profile
- `launcher=apptainer`
- `host=esplhpc-cp082`
- 产物真实存在，不是字符串

如果你想直接做 smoke：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from packages.tools.runtime import run_v3_tool

repo = Path.cwd().resolve()
run_dir = repo / "tmp_manual_runtime_check"
artifacts_dir = run_dir / "artifacts"
case_state_path = run_dir / "case_state.json"
run_dir.mkdir(parents=True, exist_ok=True)
artifacts_dir.mkdir(parents=True, exist_ok=True)
case_state_path.write_text(json.dumps({
    "case_id": "manual-runtime-check",
    "run_id": "manual-runtime-check",
    "stage_outputs": {},
    "stage_meta": {},
    "artifacts_index": [],
    "summary": {},
    "metadata": {},
}), encoding="utf-8")

t2w_ref = (repo / "artifacts" / "graph-prostate-demo" / "03_segment-prostate" / "t2w_input.nii.gz").resolve()
result = run_v3_tool(
    "segment_prostate",
    {"t2w_ref": str(t2w_ref), "output_subdir": "04_segment-prostate"},
    case_id="manual-runtime-check",
    run_id="manual-runtime-check",
    run_dir=run_dir,
    artifacts_dir=artifacts_dir,
    case_state_path=case_state_path,
    runtime_profile_override="apptainer-medgemma",
)
print(result.runtime_profile, result.launcher, result.host)
print(result.data.get("prostate_mask_path"))
PY
```

## 6. 第五轮：前端联调体验

目标：

- 判断剩下的问题偏 backend 还是 frontend UX

你实际使用前端时重点看：

- graph 更新是否及时
- artifact 点击是否直达
- report 是否容易读
- checkpoint / rerun 是否容易理解
- provenance 是否足够看懂

这轮你主要记三类问题：

- `真实性问题`
- `控制流问题`
- `展示/交互问题`

## 7. 建议你记录的问题格式

每个问题尽量按这个格式记：

```text
标题：
步骤：
预期：
实际：
涉及 graph_id / node_id：
涉及 artifact 路径：
是否可稳定复现：
```

## 8. 我最建议你先测的三项

如果你时间有限，先做这三个：

1. prostate happy path 到 report
2. pause-before-segmentation patch
3. rerun-from-node

这三项最能说明当前 `v4` 是不是已经从“工程修复阶段”进入“可以真实试用阶段”。
