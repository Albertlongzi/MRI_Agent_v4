"""Compatibility exports for API-layer schema usage.

The canonical data models live in `packages.schemas`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from packages.schemas import (
    ActionEdge,
    ActionGraph,
    ActionNode,
    ArtifactRef,
    CaseState,
    ExecutionPatch,
    GraphEvent,
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


__all__ = [
    "ActionEdge",
    "ActionGraph",
    "ActionNode",
    "ArtifactRef",
    "CaseState",
    "ChatRequest",
    "ExecutionPatch",
    "GraphEvent",
]
