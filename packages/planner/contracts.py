from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from packages.schemas.models import V4BaseModel


PlannerMode = Literal["graph", "patch", "reply"]
PlannerLLMStatus = Literal["llm", "heuristic", "disabled", "error", "llm_filtered", "not_used"]


class PlannerReply(V4BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class IntentConstraint(V4BaseModel):
    kind: str
    value: str
    required: bool = True
    source_text: str


class IntentPreference(V4BaseModel):
    kind: str
    value: str
    source_text: str


class IntentSpec(V4BaseModel):
    intent_id: str
    intent_type: Literal["graph_request", "patch_request", "reply_request"]
    domain: str
    normalized_request: str
    explicit_requested_capabilities: List[str] = Field(default_factory=list)
    inferred_requested_capabilities: List[str] = Field(default_factory=list)
    constraints: List[IntentConstraint] = Field(default_factory=list)
    preferences: List[IntentPreference] = Field(default_factory=list)
    target_graph_id: Optional[str] = None
    target_node_id: Optional[str] = None
    patch_anchor: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class PlannerMetadata(V4BaseModel):
    intent: str
    source: Literal["heuristic", "llm", "hybrid"]
    llm_status: PlannerLLMStatus
    model: Optional[str] = None
    base_url: Optional[str] = None
    latency_ms: Optional[int] = None
    validation_passed: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    extras: Dict[str, Any] = Field(default_factory=dict)


class PlannerResult(V4BaseModel):
    mode: PlannerMode
    intent_spec: Optional[IntentSpec] = None
    reply: Optional[PlannerReply] = None
    graph: Optional[Dict[str, Any]] = None
    patch: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
    planner_metadata: PlannerMetadata
    patch_reason: Optional[str] = None
