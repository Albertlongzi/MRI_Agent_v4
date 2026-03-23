from __future__ import annotations

from packages.planner.service import create_default_brain_service
from packages.schemas.mock_data import create_mock_session


def test_planner_emits_semantic_intent_spec_for_graph_request() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()
    result = brain.reply(
        user_message="Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    intent_spec = result["intent_spec"]
    assert intent_spec["intent_type"] == "graph_request"
    assert intent_spec["domain"] == "prostate"
    assert set(intent_spec["explicit_requested_capabilities"]) >= {"register", "segment", "report"}
    assert "full_pipeline" in intent_spec["inferred_requested_capabilities"]
    assert any(pref["kind"] == "report_length" and pref["value"] == "short" for pref in intent_spec["preferences"])


def test_planner_tracks_explicit_feature_analysis_capability() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()
    result = brain.reply(
        user_message="Inspect this prostate case, register ADC to T2, segment the gland, detect lesion, feature analysis and finally give me a report.",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    intent_spec = result["intent_spec"]
    tool_names = [node.get("tool_name") for node in result["graph"]["nodes"] if node.get("tool_name")]

    assert intent_spec["intent_type"] == "graph_request"
    assert "lesion" in intent_spec["explicit_requested_capabilities"]
    assert "roi_features" in intent_spec["explicit_requested_capabilities"]
    assert "detect_lesion_candidates" in tool_names
    assert "extract_roi_features" in tool_names


def test_planner_tracks_explicit_feature_extract_phrase() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()
    result = brain.reply(
        user_message="Inspect this prostate case, register ADC to T2, segment the gland, detect lesion, feature extract and give me a short report.",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    intent_spec = result["intent_spec"]
    assert "roi_features" in intent_spec["explicit_requested_capabilities"]


def test_planner_emits_semantic_intent_spec_for_patch_request() -> None:
    brain = create_default_brain_service()
    session = create_mock_session()
    result = brain.reply(
        user_message="pause before segmentation",
        graph=session.graph,
        case_state=session.case_state,
        chat_history=session.chat_history,
    )

    intent_spec = result["intent_spec"]
    assert intent_spec["intent_type"] == "patch_request"
    assert intent_spec["patch_anchor"] == "before_segmentation"
    assert intent_spec["target_node_id"] == "segment_prostate"
    assert any(constraint["kind"] == "ordering" and constraint["value"] == "before_segmentation" for constraint in intent_spec["constraints"])
