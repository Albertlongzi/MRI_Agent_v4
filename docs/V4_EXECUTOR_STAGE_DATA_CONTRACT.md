# V4 Executor Stage Data Contract

更新日期：2026-03-20

## 1. 目标

这份文档定义 `MRI_Agent_v4` prostate demo 当前 executor 的最小真实契约。

重点不是“节点跑过了什么”，而是：

- 哪些输出只是 UI/审计信息
- 哪些输出是 downstream 真正消费的关键路径
- 哪些文件如果不存在，节点就不得宣告为 `succeeded`

当前策略：

- `identify_sequences`、`register_to_reference`、`segment_prostate`、`package_vlm_evidence`、`generate_report` 都走真实 v3 tool bridge
- executor 对关键输出路径做 existence validation
- `generate_report` 额外做 downstream consistency validation
- 任一关键输出缺失或 report 语义自相矛盾时，节点标记为 `failed`，graph 不得自然进入误导性的 `completed`

## 2. 统一语义

- `ok`: tool 调用本身没有抛异常
- `consumable`: tool 的关键 downstream 输出真实存在，且满足最小一致性校验
- `succeeded`: 仅当 `ok=true` 且 `consumable=true`
- `failed`: tool 抛异常，或关键输出缺失，或 downstream consistency check 失败

runtime `case_state.json` 的每条 stage record 现在同时记录：

- `ok`
- `consumable`
- `data`
- `validation`

## 3. Stage Contract

### 3.1 `identify_sequences`

输入：

- `dicom_case_dir`

显示用途字段：

- `mapping`
- `confidence`
- `series`
- `note`

downstream 真依赖：

- `mapping`
- `series_inventory_path`
- `dicom_meta_path`
- `dicom_headers_index_path`

必须真实存在的路径：

- `series_inventory_path`
- `dicom_meta_path`
- `dicom_headers_index_path`

下游消费者：

- `register_to_reference`
- `segment_prostate`
- `package_vlm_evidence`
- `generate_report`

### 3.2 `register_to_reference`

输入：

- `fixed`
- `moving`

显示用途字段：

- `qc_pngs`
- `qc_metrics`
- `note`
- `warnings`

downstream 真依赖：

- `resampled_path`
- `transform_path`

必须真实存在的路径：

- `resampled_path`
- `transform_path`

下游消费者：

- 当前 prostate demo 的后续 report/evidence 主要依赖 stage success 和 artifact index
- 后续若加入 lesion / feature / QC 节点，应直接消费 `resampled_path`

### 3.3 `segment_prostate`

输入：

- `t2w_ref`

显示用途字段：

- `note`
- `warnings`
- `degraded_mode`

downstream 真依赖：

- `prostate_mask_path`
- `zone_mask_path`
- `t2w_input_path`

必须真实存在的路径：

- `prostate_mask_path`
- `zone_mask_path`
- `t2w_input_path`

下游消费者：

- `package_vlm_evidence`
- `generate_report`
- 以后新增的 lesion / ROI feature 节点

说明：

- `degraded_mode=true` 仍允许节点 `succeeded`
- 但前提是 fallback 仍然真实写出了可读 mask/NIfTI 文件
- 也就是说 degraded 不等于 fake

### 3.4 `package_vlm_evidence`

输入：

- `case_state_path`

显示用途字段：

- `summary`

downstream 真依赖：

- `vlm_evidence_path`

必须真实存在的路径：

- `vlm_evidence_path`

下游消费者：

- `generate_report`
- 前端 evidence 面板

### 3.5 `generate_report`

输入：

- `case_state_path`
- `domain`

显示用途字段：

- `report_txt_path`

downstream 真依赖：

- `report_json_path`
- `clinical_report_path`

必须真实存在的路径：

- `report_json_path`
- `clinical_report_path`

额外一致性校验：

- 如果 `segment_prostate` 节点已经 `succeeded`，则 `report_json_path` 中
  `lesion_assessment_meta.segmentation_usable` 不得为 `false`

失败语义：

- 如果 report 文件缺失，`generate_report` 失败
- 如果 report 内容否认一个已经成功的 segmentation，`generate_report` 失败

## 4. 当前 prostate demo 关键落盘路径

按默认 graph `graph-prostate-demo`：

- runtime state: `runtime/graph-prostate-demo/case_state.json`
- identify artifacts: `artifacts/graph-prostate-demo/01_identify-sequences/`
- register artifacts: `artifacts/graph-prostate-demo/02_register-adc/`
- segment artifacts: `artifacts/graph-prostate-demo/03_segment-prostate/`
- vlm bundle: `artifacts/graph-prostate-demo/04_package-vlm-evidence/`
- report artifacts: `artifacts/graph-prostate-demo/05_generate-report/`

## 5. Agent 4 可直接检查的 pass/fail 规则

- `register_to_reference` 为 `succeeded` 时，`resampled_path` 与 `transform_path` 必须存在
- `segment_prostate` 为 `succeeded` 时，`prostate_mask_path`、`zone_mask_path`、`t2w_input_path` 必须存在
- `generate_report` 为 `succeeded` 时，`report_json_path` 与 `clinical_report_path` 必须存在
- 如果 `segment_prostate` 为 `succeeded`，`report.json` 不得包含 `segmentation_usable: false`
- graph 最终为 `completed` 时，所有关键 stage record 必须 `consumable=true`
