# V4 Open Issues

更新日期：2026-03-20

这份文档只记录 `MRI_Agent_v4` 当前还没有完全解决的问题。

## 1. 仍未完全解决的核心问题

### 1.1 Planner 还不是完整的通用 Brain

当前已经支持：

- `IntentSpec -> compiler -> validator` 主链
- `graph / patch / reply` 结构化输出
- prostate / brain / cardiac 三类最小 graph synthesis
- `AGENT.md / SOUL.md` 风格的 semantic planner prompt 资产

但还没完全解决：

- patch 类型仍然偏少，主要还是 `insert_checkpoint`
- 多轮 follow-up 还没有稳定做成“基于当前图的 graph patch merge”
- `SubagentProposal` 还没有真正落地
- optional LLM intent extraction 默认仍关闭，当前主要依赖 deterministic extraction

### 1.2 Retry 仍然是 operator-driven，不是 bounded retry

当前已经支持：

- `rerun-from-node`
- attempt history
- patch 后继续执行

但还没完全解决：

- `retry_policy` 还没有进入 shared schema
- executor 还没有 typed bounded retry / retry budget

### 1.3 Prostate lesion / ROI 链仍然存在“假完成”问题

当前 graph 层已经可以正确编排：

- `segment_prostate -> detect_lesion_candidates -> extract_roi_features -> package_vlm_evidence -> generate_report`

但还没完全解决的关键问题是：

- `detect_lesion_candidates` 和 `extract_roi_features` 在 executor 里仍走 generic handler，而不是真实 v3 tool handler
- 节点状态可能显示为 `succeeded/completed`，但实际只生成占位 `json/txt/svg`，没有下游可消费的 `candidates_path / lesion_mask_path / feature_table_path`
- `package_vlm_evidence` 和 `generate_report` 因此只能看到 `partial` 级别证据
- 最终 report 会出现这类症状：
  - `ROI features unavailable`
  - `Lesion tool status: not_assessable`
  - `lesion candidate geometry unavailable`

这意味着：

- 当前 prostate demo 已经不是“完全断裂”，但仍不能宣称 lesion evidence 链 fully productized
- graph UI 的“8/8 done” 不等于 lesion-level evidence contract 真实闭环

收口标准应当是：

- `detect_lesion_candidates` 输出真实 `candidates_path / lesion_mask_path`
- `extract_roi_features` 输出真实 `feature_table_path`
- 这三类输出都通过 runtime contract 校验并写入 case state
- `generate_report` 在没有真实 lesion/ROI 证据时应 fail 或显式 yellow-state，而不是把 node 正常跑完后再在报告里退化

### 1.4 Tool runtime 还不是 fully self-contained

当前已经支持：

- `inproc`
- `ssh`
- `ssh + apptainer`
- runtime provenance

但还没完全解决：

- `apptainer-medgemma` 仍复用 host 的 `qwen_vllm` env
- 还不是 fully baked 的 domain image
- `nnunet` 相关 tool 还没有独立容器 profile

### 1.5 Patch 影响范围判定仍是最小规则

当前 patch / recovery 已经可用，但还不是完整静态分析：

- 目前能正确处理最常见的 target + downstream invalidation
- 还没有更强的 dependency-aware legality analysis

### 1.6 Viewer 还不是医学影像工作站级别

当前 viewer 已经能看：

- text / json / svg / 普通 image artifact
- artifact rail / raw link / inspector metadata

但还没完全解决的关键问题是：

- 不能直接渲染 `nii.gz` / volumetric image stack
- 不能把 `T2w / ADC / DWI` 作为同一空间下的切片 viewer 来联动
- 不能把 `prostate mask / zone mask / lesion mask / lesion candidates` 作为 overlay 层叠加在 viewer 上
- 当前 artifact rail 倾向于把 report/json/svg 当成主预览对象，缺少“影像优先”的选择逻辑
- lesion tool 当前也没有稳定产出 overlay png / contour bundle，所以即使 graph 有节点，viewer 也缺少可视化材料

建议的 viewer 收口方向是：

- 增加 NIfTI-aware slice viewer（axial/sagittal/coronal 至少先支持 axial）
- 支持多层 overlay：base image / prostate mask / zone mask / lesion mask / candidates
- 增加 windowing、slice scroll、opacity toggle、series switch
- artifact 元数据里补 `spatial_ref / modality / overlay_for / derived_from`
- 将 segmentation / lesion 相关 artifact 在 rail 中优先分组，而不是和 report text 平铺在一起

### 1.7 前端 recovery / provenance 展示还没做完整

- recovery 控件还不是完整工作站级别 UI
- runtime provenance 还没完整进入 artifact inspector 展示

## 2. 当前不再是主阻塞的问题

以下问题已经不再算主阻塞：

- planner 不输出结构化结果
- prostate demo 中间数据契约完全断裂
- state 只有内存态、没有 durable store
- `cp082` 上完全没有可用的 GPU runtime path
- `report.json` 与 `clinical_report.md` 的旧冲突文案

## 3. 当前阶段判断

如果按 backend MVP 判断：

- 当前已经可以进入真实人工测试阶段

如果按“完整 v4 workstation”判断：

- 当前仍然是 `strong MVP / pre-release`
- 还不应宣称所有能力都已经产品化
