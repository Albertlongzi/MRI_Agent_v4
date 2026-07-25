from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from packages.schemas import ActionEdge, ActionGraph, ActionNode, CaseState, GraphEvent
from packages.tools.compiler_metadata import build_compiler_input, get_domain_rulebook, get_tool_contract


@dataclass(frozen=True)
class CompilerIntentSpec:
    intent: str
    domain: str
    user_message: str
    case_id: str
    graph_id: str
    root_goal: str
    requested_capabilities: List[str]
    available_capabilities: List[str]
    available_tools: List[str]
    case_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompilerResult:
    graph: ActionGraph
    compiler_input: Dict[str, Any]
    selected_tools: List[str]
    applied_rules: List[str]
    warnings: List[str]


def build_intent_spec(
    *,
    user_message: str,
    case_state: CaseState,
    graph: Optional[ActionGraph],
    domain: str,
    requested_capabilities: Sequence[str],
    available_capabilities: Sequence[str],
    available_tools: Sequence[str],
    intent: str = "graph_domain_workup",
) -> CompilerIntentSpec:
    case_id = str(case_state.case_id or f"{domain}_case")
    graph_id = str(graph.graph_id if graph is not None else f"draft-{case_id}-compiler")
    root_goal = str(user_message or "").strip() or f"{domain} workup"
    return CompilerIntentSpec(
        intent=intent,
        domain=domain,
        user_message=str(user_message or ""),
        case_id=case_id,
        graph_id=graph_id,
        root_goal=root_goal,
        requested_capabilities=[str(item) for item in requested_capabilities if str(item).strip()],
        available_capabilities=[str(item) for item in available_capabilities if str(item).strip()],
        available_tools=[str(item) for item in available_tools if str(item).strip()],
        case_state={
            "case_id": case_state.case_id,
            "domain": case_state.domain,
            "input_root": case_state.input_root,
            "available_modalities": list(case_state.available_modalities),
            "sequence_index_keys": sorted(case_state.sequence_index.keys()),
        },
    )


_TOOL_BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "identify_sequences": {
        "title": "Identify Sequences",
        "action_type": "identify_sequences",
        "depends_on": [],
        "kind": "tool",
        "inputs": {"dicom_case_dir": "@case.input"},
        "outputs": {"mapping": {}, "series_inventory_path": "", "dicom_meta_path": "", "dicom_headers_index_path": ""},
        "checks": ["sequence inventory complete"],
        "owner": "executor",
    },
    "register_to_reference": {
        "title": "Register ADC to T2w",
        "action_type": "register_to_reference",
        "depends_on": ["identify_sequences"],
        "kind": "tool",
        "inputs": {"fixed": "@seq.T2w", "moving": "@seq.ADC"},
        "outputs": {"resampled_path": "", "transform_path": ""},
        "checks": ["registration outputs present"],
        "owner": "executor",
    },
    "segment_prostate": {
        "title": "Segment Prostate",
        "action_type": "segment_prostate",
        "depends_on": ["register_to_reference"],
        "kind": "tool",
        "inputs": {"t2w_ref": "@seq.T2w"},
        "outputs": {"prostate_mask_path": "", "zone_mask_path": "", "t2w_input_path": ""},
        "checks": ["mask output present"],
        "owner": "executor",
    },
    "detect_lesion_candidates": {
        "title": "Detect Lesion Candidates",
        "action_type": "detect_lesion_candidates",
        "depends_on": ["segment_prostate"],
        "kind": "tool",
        "inputs": {"mask_path": "@segment.prostate_mask_path", "aligned_path": "@register.resampled_path"},
        "outputs": {"lesion_candidates_path": ""},
        "checks": ["candidate list present"],
        "owner": "executor",
    },
    "extract_roi_features": {
        "title": "Extract ROI Features",
        "action_type": "extract_roi_features",
        "depends_on": ["detect_lesion_candidates"],
        "kind": "tool",
        "inputs": {"case_state_path": "@runtime.case_state_path"},
        "outputs": {"feature_table_path": ""},
        "checks": ["feature table written"],
        "owner": "executor",
    },
    "package_vlm_evidence": {
        "title": "Package VLM Evidence",
        "action_type": "package_vlm_evidence",
        "depends_on": ["extract_roi_features", "segment_prostate", "classify_brain_glioma_grade", "classify_cardiac_cine_disease"],
        "kind": "tool",
        "inputs": {"case_state_path": "@runtime.case_state_path"},
        "outputs": {"vlm_evidence_path": ""},
        "checks": ["evidence bundle written"],
        "owner": "executor",
    },
    "generate_report": {
        "title": "Generate Report",
        "action_type": "generate_report",
        "depends_on": ["package_vlm_evidence"],
        "kind": "tool",
        "inputs": {"case_state_path": "@runtime.case_state_path"},
        "outputs": {"report_path": ""},
        "checks": ["report includes evidence links"],
        "owner": "planner",
    },
    "brats_mri_segmentation": {
        "title": "Segment Brain Tumor",
        "action_type": "brats_mri_segmentation",
        "depends_on": ["identify_sequences"],
        "kind": "tool",
        "inputs": {"dicom_case_dir": "@case.input"},
        "outputs": {"segmentation_path": ""},
        "checks": ["segmentation output present"],
        "owner": "executor",
    },
    "classify_brain_glioma_grade": {
        "title": "Classify Glioma Grade",
        "action_type": "classify_brain_glioma_grade",
        "depends_on": ["extract_roi_features"],
        "kind": "tool",
        "inputs": {"case_state_path": "@runtime.case_state_path"},
        "outputs": {"classification_path": ""},
        "checks": ["classification output present"],
        "owner": "executor",
    },
    "reconstruct_grappa": {
        "title": "Reconstruct k-space (GRAPPA)",
        "action_type": "reconstruct_grappa",
        "depends_on": [],
        "kind": "tool",
        "inputs": {"h5_path": "@case.input"},
        "outputs": {"reconstructed_nifti": "", "mode": "", "output_shape": []},
        "checks": ["reconstructed volume present"],
        "owner": "executor",
    },
    "segment_cardiac_cine": {
        "title": "Segment Cardiac Cine",
        "action_type": "segment_cardiac_cine",
        "depends_on": ["identify_sequences"],
        "kind": "tool",
        "inputs": {"cine_ref": "@case.input"},
        "outputs": {"seg_path": ""},
        "checks": ["segmentation output present"],
        "owner": "executor",
    },
    "classify_cardiac_cine_disease": {
        "title": "Classify Cardiac Cine Disease",
        "action_type": "classify_cardiac_cine_disease",
        "depends_on": ["segment_cardiac_cine"],
        "kind": "tool",
        "inputs": {"case_state_path": "@runtime.case_state_path"},
        "outputs": {"classification_path": ""},
        "checks": ["classification output present"],
        "owner": "executor",
    },
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_tools(selected_tools: Sequence[str], tool_order: Sequence[str]) -> List[str]:
    ordered = list(tool_order)
    seen: set[str] = set()
    out: List[str] = []
    for tool in ordered:
        if tool in selected_tools and tool not in seen:
            seen.add(tool)
            out.append(tool)
    for tool in selected_tools:
        if tool not in seen:
            seen.add(tool)
            out.append(tool)
    return out


_INPUT_SCAN_LIMIT = 2000


def _input_suffixes(case_state: Optional[Dict[str, Any]]) -> List[str]:
    """Suffixes of the files a case actually points at.

    Reads the real filesystem because the case input is the only honest signal for
    "is this raw k-space or an image series?" -- ``available_modalities`` /
    ``sequence_index`` are populated by ``identify_sequences``, which cannot run
    before a k-space case has been reconstructed.  Anything unreadable yields no
    suffixes, so an input rule can only ever *add* a tool on positive evidence.
    """
    raw = str((case_state or {}).get("input_root") or "").strip()
    if not raw:
        return []
    try:
        path = Path(raw).expanduser()
        if path.is_file():
            return [path.suffix.lower()]
        if not path.is_dir():
            return []
        suffixes: List[str] = []
        seen: set[str] = set()
        # A case directory can be a DICOM series with thousands of files; only the
        # distinct suffixes matter, and the scan is capped so compiling a graph can
        # never turn into a directory walk of unbounded cost.
        for index, item in enumerate(path.iterdir()):
            if index >= _INPUT_SCAN_LIMIT:
                break
            if not item.is_file():
                continue
            suffix = item.suffix.lower()
            if suffix not in seen:
                seen.add(suffix)
                suffixes.append(suffix)
        return suffixes
    except OSError:
        return []


def _select_tools(
    domain: str,
    requested_capabilities: Sequence[str],
    case_state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str], List[str]]:
    rulebook = get_domain_rulebook(domain)
    selected = list(rulebook.get("entry_tools") or [])
    applied_rules: List[str] = []
    warnings: List[str] = []
    requested = {str(cap).strip() for cap in requested_capabilities if str(cap).strip()}

    for rule in rulebook.get("capability_rules") or []:
        when_any = {str(item).strip() for item in rule.get("when_any") or [] if str(item).strip()}
        if requested.intersection(when_any):
            applied_rules.append(str(rule.get("rule_name") or ""))
            selected.extend(str(tool) for tool in rule.get("select_tools") or [] if str(tool).strip())

    input_rules = list(rulebook.get("input_rules") or [])
    if input_rules:
        suffixes = set(_input_suffixes(case_state))
        for rule in input_rules:
            wanted = {str(item).strip().lower() for item in rule.get("when_input_suffix_any") or [] if str(item).strip()}
            if not wanted or not suffixes.intersection(wanted):
                continue
            rule_name = str(rule.get("rule_name") or "")
            if rule_name and rule_name not in applied_rules:
                applied_rules.append(rule_name)
            selected.extend(str(tool) for tool in rule.get("select_tools") or [] if str(tool).strip())

    tool_order = list(rulebook.get("tool_order") or [])
    normalized = _normalize_tools(selected, tool_order)
    for tool_name in normalized:
        if tool_name not in _TOOL_BLUEPRINTS:
            warnings.append(f"compiler tool blueprint missing for {tool_name}")
    return normalized, applied_rules, warnings


def _resolve_dependency(tool_name: str, selected_tools: Sequence[str], rulebook: Dict[str, Any]) -> List[str]:
    for rule in rulebook.get("dependency_rules") or []:
        if str(rule.get("target_tool") or "") != tool_name:
            continue
        candidates = [str(item).strip() for item in rule.get("depends_on") or [] if str(item).strip()]
        ordered: List[str] = []
        for candidate in candidates:
            if candidate in selected_tools and candidate not in ordered:
                ordered.append(candidate)
        if ordered:
            return ordered
    index = selected_tools.index(tool_name)
    if index > 0:
        return [selected_tools[index - 1]]
    return []


def _graph_node(tool_name: str, depends_on: Sequence[str]) -> ActionNode:
    blueprint = dict(_TOOL_BLUEPRINTS.get(tool_name) or {})
    title = str(blueprint.get("title") or tool_name.replace("_", " ").title())
    action_type = str(blueprint.get("action_type") or tool_name)
    return ActionNode(
        node_id=tool_name,
        kind=str(blueprint.get("kind") or "tool"),
        title=title,
        action_type=action_type,
        tool_name=tool_name,
        status="planned",
        depends_on=list(depends_on),
        inputs=dict(blueprint.get("inputs") or {}),
        outputs=dict(blueprint.get("outputs") or {}),
        checks=list(blueprint.get("checks") or []),
        artifact_refs=[],
        owner=str(blueprint.get("owner") or "executor"),
        editable=True,
        notes="compiler materialized",
    )


def _build_pipeline(
    domain: str,
    requested_capabilities: Sequence[str],
    case_state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str], List[str]]:
    selected_tools, applied_rules, warnings = _select_tools(domain, requested_capabilities, case_state)
    return selected_tools, applied_rules, warnings


def compile_intent_spec(intent_spec: CompilerIntentSpec) -> CompilerResult:
    domain = str(intent_spec.domain or "prostate").strip().lower() or "prostate"
    rulebook = get_domain_rulebook(domain)
    selected_tools, applied_rules, warnings = _build_pipeline(
        domain, intent_spec.requested_capabilities, intent_spec.case_state
    )
    compiler_input = build_compiler_input(intent_spec=intent_spec.to_dict(), selected_tools=selected_tools)

    nodes: List[ActionNode] = [
        ActionNode(
            node_id="intake_case",
            kind="planner",
            title="Case Intake",
            action_type="read_case",
            status="succeeded",
            depends_on=[],
            inputs={"case_id": intent_spec.case_id},
            outputs={"case_summary": intent_spec.root_goal},
            checks=["case metadata attached"],
            artifact_refs=[],
            owner="supervisor",
            editable=False,
            notes="compiler entrypoint",
        )
    ]

    for index, tool_name in enumerate(selected_tools):
        # ``_resolve_dependency`` already returns [] for the first selected tool, so the
        # head of the chain is never wired to the presentational ``intake_case`` node.
        # ``identify_sequences`` is no longer special-cased to []: when a case starts
        # from raw k-space the rulebook makes it depend on ``reconstruct_grappa``, and
        # silently dropping that edge would let the executor inventory a case
        # directory that has no image series in it yet.
        depends_on = _resolve_dependency(tool_name, selected_tools, rulebook)
        if not depends_on and index > 0 and nodes:
            depends_on = [nodes[-1].node_id]
        nodes.append(_graph_node(tool_name, depends_on))

    edges: List[ActionEdge] = []
    edge_idx = 1
    for node in nodes[1:]:
        for depends_on in node.depends_on or []:
            edges.append(
                ActionEdge(
                    edge_id=f"edge-{edge_idx:04d}",
                    from_node=str(depends_on),
                    to_node=node.node_id,
                    type="control",
                )
            )
            edge_idx += 1

    events = [
        GraphEvent(
            event_id="event-0001",
            graph_id=intent_spec.graph_id,
            actor_type="human",
            actor_id="compiler-request",
            event_type="graph_requested",
            target_id=intent_spec.graph_id,
            payload={"intent": intent_spec.intent, "domain": domain, "requested_capabilities": list(intent_spec.requested_capabilities)},
        ),
        GraphEvent(
            event_id="event-0002",
            graph_id=intent_spec.graph_id,
            actor_type="supervisor",
            actor_id="compiler",
            event_type="graph_proposed",
            target_id=intent_spec.graph_id,
            payload={"node_count": len(nodes), "selected_tools": list(selected_tools), "applied_rules": list(applied_rules)},
        ),
    ]

    graph = ActionGraph(
        graph_id=intent_spec.graph_id,
        case_id=intent_spec.case_id,
        domain=domain,
        status="draft",
        version=1,
        root_goal=intent_spec.root_goal,
        nodes=nodes,
        edges=edges,
        artifacts=[],
        events=events,
        proposals=[],
        patch_history=[],
    )

    return CompilerResult(
        graph=graph,
        compiler_input=compiler_input,
        selected_tools=selected_tools,
        applied_rules=applied_rules,
        warnings=warnings,
    )
