from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from apps.api.execution_runner import ExecutionRunner, RunnerBusy
from packages.planner import create_default_brain_service
from packages.state import create_default_store
from packages.tools import (
    bridge_health,
    discover_capabilities,
    discover_domains,
    discover_tools,
    resolve_tool_runtime_profile,
    summarize_runtime_profiles,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class PatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class SessionRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    input_root: str
    domain: str = "prostate"
    session_id: Optional[str] = None


class RerunFromNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    reason: str = "operator rerun"


app = FastAPI(title="MRI_Agent_v4 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORE = create_default_store()
BRAIN = create_default_brain_service()


def _runner_wall_clock_s() -> float:
    raw = os.environ.get("MRI_AGENT_V4_RUN_TIMEOUT_S", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 900.0
    return value if value > 0 else 900.0


# Background execution: POST /api/execute/* reserves the runner and returns at
# once; GET /api/execute/status reports progress without ever waiting on the
# store lock. STORE.execute_next() itself is untouched and still synchronous.
RUNNER = ExecutionRunner(STORE, max_wall_clock_s=_runner_wall_clock_s())


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "mri_agent_v4_api"}


@app.get("/api/tools/bridge/health")
def get_tool_bridge_health() -> Dict[str, Any]:
    return bridge_health()


@app.get("/api/tools")
def get_tools() -> Dict[str, Any]:
    tools = discover_tools()
    return {
        "count": len(tools),
        "tools": tools,
    }


@app.get("/api/domains")
def get_domains() -> Dict[str, Any]:
    domains = discover_domains()
    return {
        "count": len(domains),
        "domains": domains,
    }


@app.get("/api/capabilities")
def get_capabilities() -> Dict[str, Any]:
    return discover_capabilities()


@app.get("/api/planner/health")
def get_planner_health() -> Dict[str, Any]:
    return BRAIN.health()


@app.get("/api/runtime/profiles")
def get_runtime_profiles() -> Dict[str, Any]:
    return summarize_runtime_profiles()


@app.get("/api/runtime/tools/{tool_name}")
def get_tool_runtime_profile(tool_name: str) -> Dict[str, Any]:
    return resolve_tool_runtime_profile(tool_name)


@app.get("/api/session")
def get_session() -> Dict[str, object]:
    return STORE.snapshot_session().model_dump(mode="json")


@app.get("/api/graph")
def get_graph() -> Dict[str, object]:
    return STORE.snapshot_graph().model_dump(mode="json")


@app.get("/api/events")
def get_events(after_event_id: Optional[str] = Query(default=None)) -> List[Dict[str, object]]:
    after_value = after_event_id if isinstance(after_event_id, str) else None
    return [event.model_dump(mode="json") for event in STORE.events_for_active_graph(after_event_id=after_value)]


def _encode_sse(event_name: str, payload: Dict[str, Any], *, event_id: Optional[str] = None) -> str:
    lines: List[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    raw = json.dumps(payload, ensure_ascii=False)
    for line in raw.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


@app.get("/api/events/stream")
async def stream_events(
    after_event_id: Optional[str] = Query(default=None),
    poll_interval: float = Query(default=0.5, ge=0.1, le=5.0),
) -> StreamingResponse:
    async def event_source():
        cursor = after_event_id if isinstance(after_event_id, str) else None
        graph = STORE.snapshot_graph()
        yield _encode_sse(
            "stream_ready",
            {
                "status": "connected",
                "graph_id": graph.graph_id,
                "after_event_id": after_event_id,
            },
        )
        while True:
            events = STORE.events_for_active_graph(after_event_id=cursor)
            if events:
                for event in events:
                    yield _encode_sse(
                        str(event.event_type),
                        event.model_dump(mode="json"),
                        event_id=str(event.event_id),
                    )
                    cursor = str(event.event_id)
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/session")
def register_session(req: SessionRegistrationRequest) -> Dict[str, object]:
    session = STORE.register_session(
        case_id=req.case_id,
        input_root=req.input_root,
        domain=req.domain,
        session_id=req.session_id,
    )
    return {
        "status": "registered",
        "session": session.model_dump(mode="json"),
        "graph": session.graph.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in session.graph.events],
    }


@app.post("/api/cases/register")
def register_case(req: SessionRegistrationRequest) -> Dict[str, object]:
    return register_session(req)


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> Dict[str, object]:
    patch_reason = BRAIN.suggest_patch_reason(req.message)
    planner_result: Dict[str, Any] = {}
    try:
        planner_result = BRAIN.reply(
            user_message=req.message,
            graph=STORE.snapshot_graph(),
            case_state=STORE.snapshot_session().case_state,
            chat_history=STORE.snapshot_session().chat_history,
        )
    except Exception as exc:
        planner_result = {
            "mode": "error",
            "error": str(exc),
            "reply": None,
            "patch_reason": patch_reason,
        }

    planner_mode = str(planner_result.get("mode") or "mock")
    warnings = planner_result.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
        planner_result["warnings"] = warnings
    if planner_mode == "graph" and isinstance(planner_result.get("graph"), dict):
        try:
            STORE.replace_graph(
                planner_result["graph"],
                author_id="brain",
                reason=str(planner_result.get("patch_reason") or "planner_graph"),
            )
        except Exception as exc:
            warnings.append(f"graph_stage_failed: {exc}")

    reply = planner_result.get("reply")
    response = STORE.post_chat(
        req.message,
        assistant_reply=reply if isinstance(reply, dict) else None,
        reply_source=planner_mode,
        reply_metadata={k: v for k, v in planner_result.items() if k not in {"reply"}},
    )
    staged_patch = False
    if planner_mode == "patch" and isinstance(planner_result.get("patch"), dict):
        try:
            patch = STORE.stage_patch(planner_result["patch"], author_id="brain")
            response["patch"] = patch.model_dump(mode="json")
            response["proposal_count"] = len(STORE.snapshot_graph().proposals)
            staged_patch = True
        except Exception as exc:
            warnings.append(f"patch_stage_failed: {exc}")
    patch_reason = planner_result.get("patch_reason")
    if patch_reason and not staged_patch and not STORE.snapshot_graph().proposals:
        patch = STORE.preview_patch(reason=str(patch_reason), author_id="brain")
        response["patch"] = patch.model_dump(mode="json")
        response["proposal_count"] = len(STORE.snapshot_graph().proposals)
    response["graph"] = STORE.snapshot_graph().model_dump(mode="json")
    response["session"] = STORE.snapshot_session().model_dump(mode="json")
    response["planner"] = planner_result
    return response


@app.post("/api/patch")
def post_patch(req: PatchRequest) -> Dict[str, object]:
    patch = STORE.preview_patch(reason=req.reason)
    if isinstance(patch, BaseModel):
        return {
            "patch": patch.model_dump(mode="json"),
            "proposal_count": len(STORE.snapshot_graph().proposals),
        }
    return patch


@app.post("/api/proposals/apply-latest")
def apply_latest_proposal() -> Dict[str, object]:
    return STORE.apply_latest_proposal()


def _start_run(mode: str, *, max_steps: Optional[int] = None) -> Dict[str, object]:
    try:
        return RUNNER.start(mode, max_steps=max_steps)
    except RunnerBusy as busy:
        detail = dict(busy.status)
        detail["started"] = False
        detail["reason"] = "busy"
        detail["message"] = str(busy)
        raise HTTPException(status_code=409, detail=detail) from busy


def _reject_if_running(action: str) -> None:
    status = RUNNER.busy_payload()
    if status.get("running"):
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "reason": "busy",
                "message": f"cannot {action} while run {status.get('run_token')} is in flight",
                **status,
            },
        )


@app.post("/api/execute/next")
def execute_next() -> Dict[str, object]:
    """Start one node on a background thread and return immediately.

    The response reports which node is about to run; poll
    ``GET /api/execute/status`` for progress and the final graph.
    """
    return _start_run("next")


@app.post("/api/execute/until-done")
def execute_until_done(max_steps: int = Query(default=20, ge=1, le=200)) -> Dict[str, object]:
    """Start the run-to-end loop on a background thread and return immediately.

    The runner drives the loop as repeated ``STORE.execute_next()`` calls, so
    the store lock is released between nodes and ``/api/execute/status``
    publishes genuine per-node progress. Stop conditions match
    ``ExecutorStore.execute_until_done``: no runnable node, graph completed or
    failed, or ``max_steps`` reached.
    """
    return _start_run("until_done", max_steps=max_steps)


@app.get("/api/execute/status")
def execute_status(include_graph: bool = Query(default=True)) -> Dict[str, object]:
    """Runner state. Instant even while a node is executing.

    While a run is in flight ``graph``/``session`` come from a snapshot taken
    before the run started (or after the last completed step) — see
    ``graph_source`` — because reading the live store would block on the lock
    the executing node holds. When idle, the live snapshot is served.
    """
    return RUNNER.status(include_graph=include_graph)


@app.post("/api/execute/rerun-from-node")
def rerun_from_node(req: RerunFromNodeRequest) -> Dict[str, object]:
    _reject_if_running("rerun from a node")
    return STORE.rerun_from_node(req.node_id, reason=req.reason, actor_id="operator")


@app.post("/api/reset")
def reset_demo() -> Dict[str, object]:
    _reject_if_running("reset")
    session = STORE.reset()
    # The finished run's result/steps/node_id refer to the graph we just threw
    # away; leaving them queryable means /api/execute/status describes nodes
    # that no longer exist.
    RUNNER.forget_finished_run()
    return {
        "status": "reset",
        "session": session.model_dump(mode="json"),
    }


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.mount("/artifacts", StaticFiles(directory=STORE.artifact_root), name="artifacts")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
