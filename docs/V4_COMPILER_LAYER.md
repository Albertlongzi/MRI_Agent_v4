# V4 Compiler Layer

Last updated: 2026-03-20

This document defines the minimal compiler input currently used by `MRI_Agent_v4`.

The goal is deliberately narrow:

- the model must not assemble the final graph itself
- the planner produces an `IntentSpec` first
- the compiler then materializes that spec into a graph, driven by tool contracts, dependency rules, and capability expansion rules

## 1. Compiler Input

The compiler's input object is an `IntentSpec`. Its core fields are:

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

This input describes intent only. It does not describe the final node list.

## 2. Tool Contracts

Tool contracts are the compiler's first layer of metadata.

The current minimal field set:

- `tool_name`
- `domains`
- `capabilities`
- `required_inputs`
- `produced_outputs`
- `runtime_profile`
- `notes`

They tell the compiler:

- what a given tool can do
- what upstream inputs that tool requires
- which runtime profile the tool most likely lands on

## 3. Dependency Rules

Dependency rules are the compiler's second layer of metadata.

The current minimal field set:

- `rule_name`
- `target_tool`
- `depends_on`
- `reason`

They serve to:

- lift the question of how tools connect to each other out of the graph itself
- make graph generation follow explicit rules instead of relying on the model to write node ordering by hand

## 4. Capability Expansion Rules

Capability expansion rules are the compiler's third layer of metadata.

The current minimal field set:

- `rule_name`
- `domain`
- `when_any`
- `select_tools`
- `reason`
- `dependency_overrides`

They serve to:

- add tools automatically based on capability
- add dependencies automatically based on capability
- make capability expansion a matter of rules rather than hard-coded templates

### Prostate lesion example

If the intent mentions `lesion`, `classify`, or `roi_features`, the compiler additionally expands to:

- `detect_lesion_candidates`
- `extract_roi_features`

These two nodes are not a hand-written template baked into the final graph; they are the result of a capability expansion rule.

## 5. Compiler Output

The compiler emits a materialized `ActionGraph` along with a trace:

- `selected_tools`
- `applied_rules`
- `compiler_input`
- `warnings`

This lets the planner continue to provide natural-language explanations while preventing it from bypassing the compiler and constructing a graph directly.

## 6. Current Minimal Implementation

Domains implemented:

- `prostate`
- `brain`
- `cardiac`

Compiler goals implemented:

- tool auto-selection
- dependency filling
- graph generation
- prostate lesion expansion
- explicit capability coverage validation
