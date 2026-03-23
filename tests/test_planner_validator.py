from __future__ import annotations

from packages.planner.compiler import build_intent_spec, compile_intent_spec
from packages.planner.contracts import IntentSpec
from packages.planner.service import create_default_brain_service
from packages.planner.validator import validate_graph_semantics
from packages.schemas.models import ActionGraph, ActionNode, CaseState


def _case_state(domain: str) -> CaseState:
    return CaseState(case_id=f"{domain}_case", domain=domain, input_root=f"/tmp/{domain}_case")


def test_validator_accepts_prostate_feature_analysis_graph() -> None:
    intent = build_intent_spec(
        user_message="Inspect this prostate case, detect lesion, perform feature analysis, and report findings.",
        case_state=_case_state("prostate"),
        graph=None,
        domain="prostate",
        requested_capabilities=["lesion", "roi_features", "report"],
        available_capabilities=["full_pipeline", "register", "segment", "report", "lesion", "roi_features"],
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
    brain = create_default_brain_service()
    reply = brain.reply(
        user_message="Inspect this prostate case, detect lesion, perform feature analysis, and report findings.",
        graph=ActionGraph(
            graph_id="graph-prostate",
            case_id="prostate_case",
            domain="prostate",
            root_goal="prostate planning",
            nodes=[],
            edges=[],
            artifacts=[],
            events=[],
            proposals=[],
            patch_history=[],
        ),
        case_state=_case_state("prostate"),
        chat_history=[],
    )

    errors = validate_graph_semantics(
        IntentSpec.model_validate(reply["intent_spec"]),
        result.graph,
        result.compiler_input,
    )

    assert errors == []


def test_validator_flags_missing_explicit_roi_feature_capability() -> None:
    brain = create_default_brain_service()
    reply = brain.reply(
        user_message="Inspect this prostate case, detect lesion, feature analysis, and give me a report.",
        graph=ActionGraph(
            graph_id="graph-prostate",
            case_id="prostate_case",
            domain="prostate",
            root_goal="prostate planning",
            nodes=[],
            edges=[],
            artifacts=[],
            events=[],
            proposals=[],
            patch_history=[],
        ),
        case_state=_case_state("prostate"),
        chat_history=[],
    )

    broken_graph = ActionGraph(
        graph_id="graph-prostate",
        case_id="prostate_case",
        domain="prostate",
        root_goal="broken prostate planning",
        nodes=[
            ActionNode(node_id="intake_case", kind="planner", title="Case Intake", action_type="read_case", status="succeeded"),
            ActionNode(
                node_id="identify_sequences",
                kind="tool",
                title="Identify Sequences",
                action_type="identify_sequences",
                tool_name="identify_sequences",
                status="planned",
            ),
            ActionNode(
                node_id="register_to_reference",
                kind="tool",
                title="Register ADC to T2w",
                action_type="register_to_reference",
                tool_name="register_to_reference",
                status="planned",
                depends_on=["identify_sequences"],
            ),
            ActionNode(
                node_id="segment_prostate",
                kind="tool",
                title="Segment Prostate",
                action_type="segment_prostate",
                tool_name="segment_prostate",
                status="planned",
                depends_on=["register_to_reference"],
            ),
            ActionNode(
                node_id="generate_report",
                kind="tool",
                title="Generate Report",
                action_type="generate_report",
                tool_name="generate_report",
                status="planned",
                depends_on=["segment_prostate"],
            ),
        ],
        edges=[],
        artifacts=[],
        events=[],
        proposals=[],
        patch_history=[],
    )

    errors = validate_graph_semantics(
        IntentSpec.model_validate(reply["intent_spec"]),
        broken_graph,
        reply["planner_metadata"]["extras"]["compiler_input"],
    )

    assert "graph does not cover explicit requested capability: roi_features" in errors
