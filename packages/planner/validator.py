from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

from packages.schemas import ActionGraph

from .contracts import IntentSpec


_CAPABILITY_COVERAGE = {
    "register": {"register"},
    "segment": {"segment", "prostate_segmentation", "tumor_segmentation"},
    "classify": {"classify", "grade"},
    "lesion": {"lesion", "lesion_detection"},
    "report": {"report"},
    "roi_features": {"roi_features", "feature_extraction"},
}


def _iter_excluded_capabilities(intent_spec: IntentSpec) -> Set[str]:
    excluded: Set[str] = set()
    for constraint in intent_spec.constraints:
        if str(constraint.kind) == "exclude_capability" and str(constraint.value).strip():
            excluded.add(str(constraint.value).strip())
    return excluded


def _collect_graph_capabilities(graph: ActionGraph, compiler_input: Dict[str, Any]) -> Set[str]:
    by_tool: Dict[str, Set[str]] = {}
    for contract in compiler_input.get("tool_contracts") or []:
        if not isinstance(contract, dict):
            continue
        tool_name = str(contract.get("tool_name") or "").strip()
        if not tool_name:
            continue
        by_tool[tool_name] = {str(item).strip() for item in contract.get("capabilities") or [] if str(item).strip()}

    graph_capabilities: Set[str] = set()
    for node in graph.nodes:
        tool_name = str(node.tool_name or "").strip()
        if tool_name:
            graph_capabilities.update(by_tool.get(tool_name, set()))
    return graph_capabilities


def validate_graph_semantics(intent_spec: IntentSpec, graph: ActionGraph, compiler_input: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if intent_spec.intent_type != "graph_request":
        return errors

    graph_capabilities = _collect_graph_capabilities(graph, compiler_input)
    excluded_capabilities = _iter_excluded_capabilities(intent_spec)

    for capability in intent_spec.explicit_requested_capabilities:
        capability_name = str(capability).strip()
        if (
            not capability_name
            or capability_name == "full_pipeline"
            or capability_name in excluded_capabilities
        ):
            continue
        accepted = _CAPABILITY_COVERAGE.get(capability_name, {capability_name})
        if not graph_capabilities.intersection(accepted):
            errors.append(f"graph does not cover explicit requested capability: {capability_name}")

    tool_names = [str(node.tool_name or "").strip() for node in graph.nodes if str(node.tool_name or "").strip()]
    if not tool_names:
        errors.append("graph contains no materialized tool nodes")

    return errors
