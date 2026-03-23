# V4 Tool Runtime Strategy

`MRI_Agent_v4` should not rely on a single monolithic environment forever, and it should also avoid a one-container-per-tool design by default.

The practical target is:

- one small `control-plane` runtime for the API, planner, executor, metadata tools, and deterministic packaging/report steps
- a small number of shared execution runtimes for heavy or conflicting tool stacks
- explicit `runtime_profile` assignment per tool
- executor-side dispatch that resolves a tool to `inproc`, `subprocess`, `ssh`, or `apptainer`

This keeps the orchestration layer stable while allowing new tools to land without poisoning the whole stack.

## Recommended Runtime Model

`v4` should use a layered runtime strategy:

1. `control-plane`
   - purpose: API, planner, executor, lightweight tools, report packaging
   - launcher: `inproc`
   - environment: local `.venv`
   - GPU: no

2. `legacy-qwen-vllm`
   - purpose: near-term bridge for tools that already run in the existing `qwen_vllm` conda env
   - launcher: `subprocess`
   - environment: shared conda env on compute nodes
   - GPU: yes when needed

3. `cp082-qwen-vllm`
   - purpose: explicit SSH dispatch to `esplhpc-cp082` when the control node has no visible GPU
   - launcher: `ssh`
   - environment: shared `qwen_vllm` conda env on the remote host
   - GPU: yes

4. `apptainer-medgemma`
   - purpose: remote `ssh + apptainer exec` path on `esplhpc-cp082`
   - launcher: `apptainer`
   - environment: pinned base container plus bind-mounted shared repo and host conda env
   - GPU: yes

5. `nnunet-gpu`
   - purpose: segmentation/classification workloads with specialized torch or nnUNet constraints
   - launcher: `subprocess`
   - environment: dedicated GPU-capable env
   - GPU: yes

These profiles are declared in [configs/tool_runtime_profiles.json](/home/longz2/common/medgemma/MRI_Agent_v4/configs/tool_runtime_profiles.json).

## Why Not One Big Env

A single giant environment becomes brittle once the tool catalog grows:

- torch, monai, nnUNet, vLLM, and serving dependencies tend to conflict
- GPU and CPU toolchains evolve on different cadences
- debugging environment regressions becomes harder than debugging the tools
- reproducibility gets worse as ad hoc packages accumulate

The `qwen_vllm` env is still useful as a transitional bridge, but it should become one named runtime profile, not the system-wide assumption.

## Why Not One Tiny Container Per Tool

Per-tool containers sound clean, but they create their own operational tax:

- image sprawl
- repeated CUDA and torch stacks
- slower rollout and test cycles
- more bind-mount and cache-management complexity on HPC
- harder interactive debugging when many tools share 90% of the same dependencies

Use isolated per-tool containers only for genuinely difficult stacks, not as the default packaging rule.

## HPC Recommendation

On HPC, prefer `Apptainer` over Docker for isolated GPU runtimes.

This aligns with the existing `v3` direction:

- [start_vllm_server_apptainer.sh](/home/longz2/common/medgemma/MRI_Agent/scripts/start_vllm_server_apptainer.sh)
- [start_medgemma_server_apptainer.sh](/home/longz2/common/medgemma/MRI_Agent/scripts/start_medgemma_server_apptainer.sh)

For `v4`, the recommended near-term pattern is:

- keep `control-plane` in a normal Python env
- keep `legacy-qwen-vllm` as the bridge for tools already validated there
- keep `cp082-qwen-vllm` as the explicit non-container GPU fallback on the shared HPC host
- move the most fragile GPU/model-serving stacks behind `Apptainer`
- split off an `nnunet-gpu` profile once those tools need a cleaner boundary

## Dispatch Rule

The planner should not choose the runtime directly.

Instead:

1. planner proposes a tool-capability node
2. executor validates the node
3. executor resolves the node's `runtime_profile`
4. runtime launcher executes via `inproc`, `subprocess`, `ssh`, or `apptainer`
5. artifacts and provenance flow back into the canonical graph

This keeps runtime policy deterministic and auditable.

## Near-Term Implementation

## Current Implemented State

As of 2026-03-20, the runtime layer now supports:

- real `ssh` dispatch to `esplhpc-cp082`
- real `apptainer` dispatch, including `ssh + apptainer exec`
- runtime provenance attached to tool results via `runtime_provenance`
- explicit launcher failure semantics for missing profile, missing container image, and invalid bind sources

The current high-signal runtime assignments are:

- `identify_sequences` -> `control-plane`
- `register_to_reference` -> `control-plane`
- `package_vlm_evidence` -> `control-plane`
- `generate_report` -> `control-plane`
- `segment_prostate` -> `apptainer-medgemma`
- `cp082-qwen-vllm` remains available as the shared-env SSH fallback path

Short term:

- keep the current real `v3` bridges on `control-plane`
- expose runtime-profile metadata through the API
- annotate executed nodes with `runtime_profile`

Next:

- extend runtime provenance into richer executor events and artifact metadata
- migrate more GPU-heavy tools from pure `subprocess` to the container path once their remote env assumptions are pinned
- split a dedicated `nnunet` container profile if those stacks stop fitting the shared `qwen_vllm` env

## Design Rule

Add new tools by assigning them to an existing runtime profile first.

Create a new profile only when the dependency or launcher boundary is materially different.
