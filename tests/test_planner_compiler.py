from __future__ import annotations

from packages.planner.compiler import build_intent_spec, compile_intent_spec
from packages.schemas.models import ActionGraph, CaseState


def _case_state(domain: str) -> CaseState:
    return CaseState(case_id=f"{domain}_case", domain=domain, input_root=f"/tmp/{domain}_case")


def test_compiler_input_has_contract_dependency_and_expansion_sections() -> None:
    intent = build_intent_spec(
        user_message="Inspect this prostate lesion case and generate a report.",
        case_state=_case_state("prostate"),
        graph=None,
        domain="prostate",
        requested_capabilities=["lesion", "report"],
        available_capabilities=["full_pipeline", "register", "segment", "classify", "report", "lesion"],
        available_tools=[
            "identify_sequences",
            "register_to_reference",
            "segment_prostate",
            "detect_lesion_candidates",
            "extract_roi_features",
            "package_vlm_evidence",
            "generate_report",
        ],
    )

    result = compile_intent_spec(intent)

    assert "tool_contracts" in result.compiler_input
    assert "dependency_rules" in result.compiler_input
    assert "capability_expansion_rules" in result.compiler_input
    assert any(contract["tool_name"] == "detect_lesion_candidates" for contract in result.compiler_input["tool_contracts"])
    assert any(contract["tool_name"] == "extract_roi_features" for contract in result.compiler_input["tool_contracts"])
    assert "prostate-lesion-expansion" in result.applied_rules


def test_prostate_lesion_request_compiles_to_expanded_dependency_graph() -> None:
    intent = build_intent_spec(
        user_message="Inspect this prostate lesion case and report the findings.",
        case_state=_case_state("prostate"),
        graph=None,
        domain="prostate",
        requested_capabilities=["lesion", "report"],
        available_capabilities=["full_pipeline", "register", "segment", "classify", "report", "lesion"],
        available_tools=[
            "identify_sequences",
            "register_to_reference",
            "segment_prostate",
            "detect_lesion_candidates",
            "extract_roi_features",
            "package_vlm_evidence",
            "generate_report",
        ],
    )

    result = compile_intent_spec(intent)
    graph = result.graph
    tool_names = [node.tool_name for node in graph.nodes if node.tool_name]
    nodes_by_tool = {node.tool_name: node for node in graph.nodes if node.tool_name}

    assert tool_names[:2] == ["identify_sequences", "register_to_reference"]
    assert "segment_prostate" in tool_names
    assert "detect_lesion_candidates" in tool_names
    assert "extract_roi_features" in tool_names
    assert nodes_by_tool["detect_lesion_candidates"].depends_on == ["segment_prostate"]
    assert nodes_by_tool["extract_roi_features"].depends_on == ["detect_lesion_candidates"]
    assert nodes_by_tool["generate_report"].depends_on == ["package_vlm_evidence"]
