from __future__ import annotations

from typing import Dict, List

from .models import (
    ActionEdge,
    ActionGraph,
    ActionNode,
    ArtifactRef,
    CaseState,
    ExecutionPatch,
    GraphEvent,
    MockSession,
    PatchOperation,
)
from packages.tools import resolve_demo_case


def _default_demo_input_root() -> str:
    demo_case = resolve_demo_case("prostate")
    if demo_case is not None:
        return str(demo_case)
    return "/demo/cases/prostate_demo_001"


def _mock_nodes() -> List[ActionNode]:
    return [
        ActionNode(
            node_id="intake_case",
            kind="planner",
            title="Case Intake",
            action_type="read_case",
            status="succeeded",
            outputs={"case_summary": "Prostate case with T2w, ADC, DWI_highb present."},
            checks=["case root exists", "modalities indexed"],
            owner="supervisor",
            editable=False,
        ),
        ActionNode(
            node_id="identify_sequences",
            title="Identify Sequences",
            action_type="identify_sequences",
            tool_name="identify_sequences",
            status="planned",
            inputs={"dicom_case_dir": "@case.input"},
            outputs={"mapping": {}},
            checks=["sequence inventory complete"],
            owner="executor",
        ),
        ActionNode(
            node_id="register_adc",
            title="Register ADC to T2w",
            action_type="register_to_reference",
            tool_name="register_to_reference",
            status="planned",
            depends_on=["identify_sequences"],
            inputs={"fixed": "@seq.T2w", "moving": "@seq.ADC"},
            outputs={"resampled_path": "", "transform_path": ""},
            checks=["fixed is T2w", "moving is non-T2w"],
            owner="executor",
        ),
        ActionNode(
            node_id="segment_prostate",
            title="Segment Prostate",
            action_type="segment_prostate",
            tool_name="segment_prostate",
            status="planned",
            depends_on=["register_adc"],
            inputs={"t2w_ref": "@seq.T2w"},
            outputs={"prostate_mask_path": ""},
            checks=["mask output present"],
            owner="planner",
        ),
        ActionNode(
            node_id="package_vlm_evidence",
            title="Package VLM Evidence",
            action_type="package_vlm_evidence",
            tool_name="package_vlm_evidence",
            status="planned",
            depends_on=["segment_prostate"],
            inputs={"case_state_path": "@runtime.case_state_path"},
            outputs={"vlm_evidence_path": ""},
            checks=["evidence bundle written"],
            owner="executor",
        ),
        ActionNode(
            node_id="generate_report",
            title="Generate Report",
            action_type="generate_report",
            tool_name="generate_report",
            status="planned",
            depends_on=["package_vlm_evidence"],
            inputs={"domain": "prostate", "case_state_path": "@runtime.case_state_path"},
            outputs={"report_path": ""},
            checks=["report includes evidence links"],
            owner="planner",
        ),
    ]


def _mock_edges() -> List[ActionEdge]:
    return [
        ActionEdge(edge_id="edge-1", from_node="intake_case", to_node="identify_sequences", type="control"),
        ActionEdge(edge_id="edge-2", from_node="identify_sequences", to_node="register_adc", type="control"),
        ActionEdge(edge_id="edge-3", from_node="register_adc", to_node="segment_prostate", type="control"),
        ActionEdge(edge_id="edge-4", from_node="segment_prostate", to_node="package_vlm_evidence", type="control"),
        ActionEdge(edge_id="edge-5", from_node="package_vlm_evidence", to_node="generate_report", type="control"),
    ]


def _mock_artifacts() -> List[ArtifactRef]:
    return []


def _mock_events(graph_id: str) -> List[GraphEvent]:
    return [
        GraphEvent(
            event_id="event-1",
            graph_id=graph_id,
            actor_type="human",
            actor_id="demo-user",
            event_type="graph_requested",
            target_id=graph_id,
            payload={"intent": "Inspect this prostate case and generate a short report."},
        ),
        GraphEvent(
            event_id="event-2",
            graph_id=graph_id,
            actor_type="supervisor",
            actor_id="supervisor",
            event_type="graph_proposed",
            target_id=graph_id,
            payload={"node_count": 6, "status": "ready"},
        ),
        GraphEvent(
            event_id="event-3",
            graph_id=graph_id,
            actor_type="system",
            actor_id="executor",
            event_type="graph_ready",
            target_id=graph_id,
            payload={"next_node": "identify_sequences"},
        ),
    ]


def create_mock_session() -> MockSession:
    graph_id = "graph-prostate-demo"
    nodes = _mock_nodes()
    graph = ActionGraph(
        graph_id=graph_id,
        case_id="prostate_demo_001",
        domain="prostate",
        status="ready",
        version=1,
        root_goal="Inspect the prostate MRI, register ADC to T2w, segment the gland, and draft a short report.",
        nodes=nodes,
        edges=_mock_edges(),
        artifacts=_mock_artifacts(),
        events=_mock_events(graph_id),
    )
    case_state = CaseState(
        case_id="prostate_demo_001",
        domain="prostate",
        input_root=_default_demo_input_root(),
        sequence_index={},
        available_modalities=[],
        active_graph_id=graph_id,
        active_node_id="identify_sequences",
        selected_artifacts=[],
        last_event_id="event-3",
        ui_focus={"panel": "graph", "selected_node": "identify_sequences"},
    )
    return MockSession(
        session_id="session-demo",
        case_state=case_state,
        graph=graph,
        chat_history=[
            {
                "role": "user",
                "content": "Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report.",
            },
            {
                "role": "assistant",
                "content": "I proposed a 6-node ActionGraph. The next step is a real identify_sequences pass against the demo prostate case.",
            },
        ],
    )


def create_patch_preview(graph: ActionGraph, *, reason: str) -> ExecutionPatch:
    return ExecutionPatch(
        patch_id="patch-preview-1",
        graph_id=graph.graph_id,
        author_type="human",
        author_id="demo-user",
        reason=reason,
        applies_to_version=graph.version,
        result="preview",
        operations=[
            PatchOperation(
                op="insert_checkpoint",
                target="segment_prostate",
                value={
                    "after_node": "register_adc",
                    "title": "Review Registration Output",
                    "kind": "review",
                },
            )
        ],
    )


def create_chat_reply(user_message: str, graph: ActionGraph) -> Dict[str, str]:
    lowered = user_message.strip().lower()
    if "pause" in lowered or "review" in lowered:
        return {
            "role": "assistant",
            "content": "Proposed a review checkpoint after registration. You can inspect artifacts before segmentation continues.",
        }
    if "report" in lowered:
        return {
            "role": "assistant",
            "content": "The graph already includes evidence packaging and report nodes. Next runnable step remains registration, followed by segmentation, evidence packaging, and report drafting.",
        }
    return {
        "role": "assistant",
        "content": (
            "I kept the current ActionGraph and updated the conversation context. "
            f"The active graph still has {len(graph.nodes)} nodes with registration in progress."
        ),
    }
