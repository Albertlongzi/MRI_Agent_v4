# MRI_Agent v4 Product Spec

## 1. Product Summary

`MRI_Agent v4` is a natural-language radiology workstation for MRI analysis and reporting. It keeps the BCER core idea of separating planning from deterministic execution, but it is not benchmark-first and not template-first.

The primary user experience is:

1. A clinician or operator describes the case in natural language.
2. The system proposes a structured action graph.
3. The user inspects, edits, approves, or partially reroutes the graph.
4. The system executes the graph deterministically and keeps the evidence visible.

The product should feel like a hybrid of:

- a radiology workstation
- a conversational copilot
- a graph-based workflow editor
- an auditable execution system

## 2. Product Goals

- Make MRI workflows easier to drive with natural language.
- Keep the execution graph visible at all times.
- Preserve deterministic, traceable, and replayable tool execution.
- Let humans steer the workflow when the system is uncertain.
- Present outputs as evidence-backed artifacts rather than opaque chat responses.
- Support both single-case interactive use and later batch-style review.
- Allow later evolution toward a supervisor-led multi-subagent system without
  fragmenting state.

## 3. Target Users

- Radiology researchers who want to prototype MRI workflows quickly.
- ML / agent engineers who need a visible, debuggable execution graph.
- Advanced operators who want to inspect and reroute workflows during a case.
- Domain experts who need evidence, logs, and reports tied to a case.

## 4. Non-Goals

- Not a benchmark harness.
- Not a template-driven planner UI.
- Not a general-purpose code interpreter.
- Not a replacement for a clinical PACS or full enterprise RIS.
- Not a free-form tool sandbox with unrestricted tool access.
- Not a multi-user orchestration platform in the first version.

## 5. BCER Principles Kept

`v4` keeps the parts of BCER that matter for product reliability:

- `Brain` proposes structured actions from natural language and case context.
- `Cerebellum` executes typed actions deterministically.
- The system validates schemas, paths, scope, and stage order.
- Failures can trigger repair, retry, human patching, or halting.
- Every step should leave evidence in artifacts and event logs.
- The user should always be able to see the current graph state.

## 6. Core Workflows

### 6.1 Case Intake

- The user loads a case directory, study, or series bundle.
- The system indexes available modalities, files, and key metadata.
- The UI shows what was detected and what is missing.

### 6.2 Natural-Language Planning

- The user asks for an analysis in plain language.
- The Brain turns the request into a structured action graph proposal.
- The graph is shown before execution.
- The user can approve, edit, or narrow the graph.

### 6.3 Interactive Execution

- The executor runs nodes in order.
- Results are written to artifacts and event streams.
- The graph updates live with node status, logs, and outputs.
- The user can pause execution, inspect a node, or reroute from a point in the graph.

### 6.4 Human-in-the-Loop Repair

- If a node fails or produces weak output, the system can propose a repair.
- The user can accept a repair, edit inputs manually, or skip the branch.
- The graph should preserve the failed state and the patched state for auditability.

### 6.5 Report And Review

- The system summarizes findings into a report draft.
- Evidence links remain attached to the report.
- The user can review the report side by side with the graph and viewer.

## 7. UI Concept

The UI should be a radiology workstation, not a plain chat app.

### 7.1 Chat Pane

- Receives natural-language requests.
- Shows concise model responses and action proposals.
- Supports follow-up questions, clarifications, and operator commands.
- Can reference the current case, node, artifact, or report.

### 7.2 Graph Pane

- Shows the current action graph as connected nodes.
- Displays pending, running, succeeded, failed, patched, and blocked states.
- Lets the user inspect node dependencies and rerun from a selected node.
- Makes the planner output legible as a workflow rather than a blob of text.

### 7.3 Viewer Pane

- Displays MRI slices, overlays, masks, and quality-control outputs.
- Lets the user link a visual selection to a graph node or artifact.
- Supports evidence inspection during planning and after execution.

### 7.4 Inspector Pane

- Shows node arguments, resolved inputs, outputs, logs, and validation state.
- Shows artifact metadata and provenance.
- Supports manual edits, approvals, and rerun controls.

## 8. MVP Boundaries

The first `v4` MVP should be intentionally narrow.

### In Scope

- One web workstation.
- One natural-language chat loop.
- One live action graph.
- One deterministic execution path.
- Human inspection and patching.
- Artifact and evidence tracking.
- A small set of stable MRI tools.

### Out Of Scope

- Full clinical deployment.
- Multi-user collaboration.
- Fine-grained permission systems.
- Full DICOM workstation parity.
- All prior benchmark arms and fault-injection modes.
- Every tool from the old codebase.
- Specialist subagents as a required capability in the first MVP.

## 9. Domain Scope

### In Scope

- Brain MRI
- Prostate MRI
- Cardiac MRI

### Conditionally In Scope

- DICOM ingestion and metadata inspection
- NIfTI-based downstream processing
- Report drafting and evidence packaging

### Out Of Scope For MVP

- Non-MRI modalities as first-class domains
- Broad cross-domain generalist workflow support
- Open-ended medical coding or billing workflows

## 10. Success Criteria

`v4` is successful if it can do the following reliably:

- Turn a natural-language MRI request into a visible action graph.
- Let the user understand and change the graph before execution.
- Execute the graph deterministically with traceable artifacts.
- Keep the viewer, graph, and report synchronized with the same case state.
- Recover from common workflow errors without losing provenance.
- Produce outputs that a human can inspect and trust.

Secondary success criteria:

- The system is pleasant enough to use for repeated radiology workflow review.
- The product reduces cognitive load compared with shell-only or template-only use.
- The architecture stays understandable as the system grows.

## 11. Roadmap Phases

### Phase 0: Product Definition

- Freeze product goals and non-goals.
- Define the first supported domains and tools.
- Decide the initial viewer stack and backend split.

### Phase 1: Skeleton Workstation

- Create the repo skeleton.
- Stand up the web UI shell.
- Add session and case state plumbing.
- Render a dummy graph and artifact inspector.

### Phase 2: ActionGraph IR

- Define the structured graph schema.
- Add event streaming and artifact references.
- Make the graph persist across planning and execution.

### Phase 3: BCER-Core Runtime

- Connect Brain planning to Cerebellum execution.
- Add validation, repair, and human patch flow.
- Execute a short MRI workflow end to end.

### Phase 4: Radiology UI

- Wire the chat, graph, viewer, and inspector together.
- Add artifact drill-down and evidence linking.
- Support rerun-from-node and manual edits.

### Phase 5: Domain Expansion

- Add stable workflows for brain, prostate, and cardiac cases.
- Improve report generation and evidence packaging.
- Polish the operator experience for repeated use.
- Add supervisor-led specialist subagents where they improve planning, recovery,
  and reporting without splitting the source of truth.

## 12. Product Positioning

`MRI_Agent v4` should be positioned as a controllable radiology workstation with agentic assistance, not as a benchmark suite or a generic chatbot. The design should make the graph visible, the data traceable, and the human operator in control.
