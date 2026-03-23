from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from packages.schemas import ActionGraph, ActionNode, CaseState, GraphEvent, MockSession, create_mock_session


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_identifier(prefix: str) -> str:
    stamp = _utc_now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid4().hex[:8]}"


def _normalize_domain(domain: str) -> str:
    text = re.sub(r"[^a-z0-9_-]+", "-", str(domain or "unknown").strip().lower()).strip("-")
    return text or "unknown"


def _normalize_case_id(case_id: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(case_id or "").strip()).strip("-")
    return text or f"case-{uuid4().hex[:8]}"


def _event(event_id: str, graph_id: str, actor_type: str, actor_id: str, event_type: str, target_id: str, payload: Dict[str, object]) -> GraphEvent:
    return GraphEvent(
        event_id=event_id,
        graph_id=graph_id,
        actor_type=actor_type,  # type: ignore[arg-type]
        actor_id=actor_id,
        event_type=event_type,
        target_id=target_id,
        payload=dict(payload or {}),
    )


def _initial_events(graph_id: str, *, case_id: str, domain: str, input_root: str, graph_status: str, node_count: int) -> List[GraphEvent]:
    return [
        _event(
            "event-0001",
            graph_id,
            "system",
            "api",
            "case_registered",
            case_id,
            {"case_id": case_id, "domain": domain, "input_root": input_root},
        ),
        _event(
            "event-0002",
            graph_id,
            "system",
            "api",
            "graph_initialized",
            graph_id,
            {"status": graph_status, "node_count": node_count},
        ),
    ]


def _generic_graph(case_id: str, domain: str, graph_id: str) -> ActionGraph:
    intake_node = ActionNode(
        node_id="intake_case",
        kind="planner",
        title="Case Intake",
        action_type="read_case",
        status="succeeded",
        outputs={"case_summary": f"{domain} case registered and awaiting planner proposal."},
        checks=["case root recorded"],
        owner="supervisor",
        editable=False,
        notes="Session initialized without a domain-specific execution graph.",
    )
    return ActionGraph(
        graph_id=graph_id,
        case_id=case_id,
        domain=domain,
        status="draft",
        version=1,
        root_goal=f"Prepare a {domain} MRI workflow for case {case_id}.",
        nodes=[intake_node],
        edges=[],
        artifacts=[],
        events=[],
        proposals=[],
        patch_history=[],
    )


def create_registered_session(
    *,
    case_id: str,
    input_root: str,
    domain: str = "prostate",
    session_id: Optional[str] = None,
    graph_id: Optional[str] = None,
) -> MockSession:
    normalized_case_id = _normalize_case_id(case_id)
    normalized_domain = _normalize_domain(domain)
    session_name = str(session_id or _make_identifier("session"))
    graph_name = str(graph_id or _make_identifier(f"graph-{normalized_domain}"))

    if normalized_domain == "prostate":
        base = create_mock_session()
        graph = deepcopy(base.graph)
        graph.graph_id = graph_name
        graph.case_id = normalized_case_id
        graph.domain = normalized_domain
        graph.version = 1
        graph.artifacts = []
        graph.events = []
        graph.proposals = []
        graph.patch_history = []
        graph.root_goal = f"Inspect the prostate MRI for case {normalized_case_id}, register ADC to T2w, segment the gland, and draft a short report."
    else:
        graph = _generic_graph(normalized_case_id, normalized_domain, graph_name)

    graph.events = _initial_events(
        graph.graph_id,
        case_id=normalized_case_id,
        domain=normalized_domain,
        input_root=str(input_root),
        graph_status=str(graph.status),
        node_count=len(graph.nodes),
    )

    first_runnable = next(
        (node.node_id for node in graph.nodes if str(node.status) in {"planned", "ready", "running"}),
        None,
    )
    case_state = CaseState(
        case_id=normalized_case_id,
        domain=normalized_domain,
        input_root=str(input_root),
        sequence_index={},
        available_modalities=[],
        active_graph_id=graph.graph_id,
        active_node_id=first_runnable,
        selected_artifacts=[],
        last_error=None,
        last_event_id=graph.events[-1].event_id if graph.events else None,
        ui_focus={"panel": "graph", "selected_node": "" if first_runnable is None else first_runnable},
    )
    return MockSession(
        session_id=session_name,
        case_state=case_state,
        graph=graph,
        chat_history=[],
    )


def create_reset_session(template: MockSession, *, preserve_session_id: bool = True) -> MockSession:
    return create_registered_session(
        case_id=template.case_state.case_id,
        input_root=template.case_state.input_root,
        domain=template.case_state.domain,
        session_id=template.session_id if preserve_session_id else None,
    )
