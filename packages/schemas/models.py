from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class V4BaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


GraphStatus = Literal["draft", "ready", "running", "paused", "completed", "failed"]
NodeKind = Literal["tool", "human", "planner", "review", "merge", "branch", "finalize"]
NodeStatus = Literal["planned", "ready", "running", "blocked", "succeeded", "failed", "skipped", "patched"]
EdgeType = Literal["control", "data", "approval", "feedback"]
ArtifactKind = Literal["nifti", "dicom_manifest", "json", "csv", "png", "svg", "text", "report", "log", "mask", "overlay", "table"]
ArtifactRole = Literal["input", "output", "evidence", "preview", "intermediate"]
ActorType = Literal["supervisor", "specialist", "human", "executor", "system"]
PatchResult = Literal["applied", "rejected", "superseded", "merged", "preview"]
ProposalStatus = Literal["draft", "proposed", "approved", "rejected"]


class ActionNode(V4BaseModel):
    node_id: str
    kind: NodeKind = "tool"
    title: str
    action_type: str
    tool_name: Optional[str] = None
    status: NodeStatus = "planned"
    depends_on: List[str] = Field(default_factory=list)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    checks: List[str] = Field(default_factory=list)
    artifact_refs: List[str] = Field(default_factory=list)
    owner: str = "supervisor"
    editable: bool = True
    notes: Optional[str] = None
    attempt_count: int = 0
    current_attempt_id: Optional[str] = None
    rerun_from: Optional[str] = None
    supersedes: Optional[str] = None
    attempt_history: List[Dict[str, Any]] = Field(default_factory=list)


class ActionEdge(V4BaseModel):
    edge_id: str
    from_node: str
    to_node: str
    type: EdgeType = "control"
    label: Optional[str] = None
    artifact_key: Optional[str] = None


class ArtifactRef(V4BaseModel):
    artifact_id: str
    node_id: str
    name: str
    kind: ArtifactKind
    uri: str
    role: ArtifactRole
    mime_type: Optional[str] = None
    visible: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphEvent(V4BaseModel):
    event_id: str
    graph_id: str
    ts: datetime = Field(default_factory=utc_now)
    actor_type: ActorType
    actor_id: str
    event_type: str
    target_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    parent_event_id: Optional[str] = None


class PatchOperation(V4BaseModel):
    op: str
    target: Optional[str] = None
    value: Dict[str, Any] = Field(default_factory=dict)


class ExecutionPatch(V4BaseModel):
    patch_id: str
    graph_id: str
    author_type: Literal["human", "supervisor", "specialist", "system"]
    author_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    reason: str
    operations: List[PatchOperation]
    applies_to_version: int
    result: PatchResult = "preview"


class CaseState(V4BaseModel):
    case_id: str
    domain: str
    input_root: str
    sequence_index: Dict[str, str] = Field(default_factory=dict)
    available_modalities: List[str] = Field(default_factory=list)
    active_graph_id: Optional[str] = None
    active_node_id: Optional[str] = None
    selected_artifacts: List[str] = Field(default_factory=list)
    last_error: Optional[str] = None
    last_event_id: Optional[str] = None
    ui_focus: Dict[str, str] = Field(default_factory=dict)


class ActionGraph(V4BaseModel):
    graph_id: str
    case_id: str
    domain: str
    status: GraphStatus = "draft"
    version: int = 1
    root_goal: str
    nodes: List[ActionNode]
    edges: List[ActionEdge]
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    events: List[GraphEvent] = Field(default_factory=list)
    proposals: List[ExecutionPatch] = Field(default_factory=list)
    patch_history: List[ExecutionPatch] = Field(default_factory=list)


class MockSession(V4BaseModel):
    session_id: str
    case_state: CaseState
    graph: ActionGraph
    chat_history: List[Dict[str, str]] = Field(default_factory=list)
