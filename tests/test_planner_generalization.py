from __future__ import annotations

from packages.planner.service import create_default_brain_service
from packages.schemas.mock_data import create_mock_session
from packages.schemas.models import ActionGraph, CaseState


def _empty_graph(domain: str) -> ActionGraph:
    return ActionGraph(
        graph_id=f"graph-{domain}",
        case_id=f"{domain}_case",
        domain=domain,
        root_goal=f"{domain} planning",
        nodes=[],
        edges=[],
        artifacts=[],
        events=[],
        proposals=[],
        patch_history=[],
    )


def test_planner_keeps_prostate_graph_path() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()
    result = brain.reply(
        user_message="Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    assert result["mode"] == "graph"
    assert result["graph"]["domain"] == "prostate"
    tool_names = [node.get("tool_name") for node in result["graph"]["nodes"] if node.get("tool_name")]
    assert "register_to_reference" in tool_names
    assert "segment_prostate" in tool_names
    assert "generate_report" in tool_names
    assert result["planner_metadata"]["extras"]["domain"] == "prostate"


def test_planner_builds_brain_classification_graph() -> None:
    brain = create_default_brain_service()
    case_state = CaseState(case_id="brain_case", domain="brain", input_root="/tmp/brain_case")
    result = brain.reply(
        user_message="Plan a brain MRI workflow to segment the tumor, classify glioma grade, and report findings.",
        graph=_empty_graph("brain"),
        case_state=case_state,
        chat_history=[],
    )

    assert result["mode"] == "graph"
    assert result["graph"]["domain"] == "brain"
    tool_names = [node.get("tool_name") for node in result["graph"]["nodes"] if node.get("tool_name")]
    assert "brats_mri_segmentation" in tool_names
    assert "extract_roi_features" in tool_names
    assert "classify_brain_glioma_grade" in tool_names
    assert "segment_prostate" not in tool_names
    assert result["planner_metadata"]["extras"]["domain"] == "brain"
    assert "classify" in result["planner_metadata"]["extras"]["requested_capabilities"]


def test_planner_builds_cardiac_report_graph() -> None:
    brain = create_default_brain_service()
    case_state = CaseState(case_id="cardiac_case", domain="cardiac", input_root="/tmp/cardiac_case")
    result = brain.reply(
        user_message="Create a cardiac cine workflow and generate a short report.",
        graph=_empty_graph("cardiac"),
        case_state=case_state,
        chat_history=[],
    )

    assert result["mode"] == "graph"
    assert result["graph"]["domain"] == "cardiac"
    tool_names = [node.get("tool_name") for node in result["graph"]["nodes"] if node.get("tool_name")]
    assert "segment_cardiac_cine" in tool_names
    assert "generate_report" in tool_names
    assert "register_to_reference" not in tool_names
    assert result["planner_metadata"]["extras"]["domain"] == "cardiac"
