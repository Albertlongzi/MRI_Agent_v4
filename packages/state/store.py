from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from packages.executor.store import MockExecutorStore
from packages.schemas import ActionGraph, CaseState, ExecutionPatch, GraphEvent, MockSession
from packages.schemas import create_mock_session

from .session_factory import create_registered_session, create_reset_session


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _model_copy(model: Any) -> Any:
    copier = getattr(model, "model_copy", None)
    if callable(copier):
        return copier(deep=True)
    return deepcopy(model)


def _next_event_id(graph: ActionGraph) -> str:
    return f"event-{len(graph.events) + 1:04d}"


def _focus_node_id(graph: ActionGraph) -> Optional[str]:
    running = next((node.node_id for node in graph.nodes if str(node.status) == "running"), None)
    if running is not None:
        return running
    return next((node.node_id for node in graph.nodes if str(node.status) in {"planned", "ready"}), None)


class DurableSessionStore:
    def __init__(
        self,
        *,
        root_dir: Optional[Path | str] = None,
        db_path: Optional[Path | str] = None,
        session_factory=create_mock_session,
    ) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[2]
        self._state_dir = self._root_dir / "state"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        default_db = self._state_dir / "v4_state.sqlite3"
        env_db = os.environ.get("MRI_AGENT_V4_STATE_DB", "").strip()
        self._db_path = Path(db_path) if db_path is not None else Path(env_db) if env_db else default_db
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._session_factory = session_factory
        self._executor = MockExecutorStore(root_dir=self._root_dir, session_factory=session_factory)
        self._template_session = _model_copy(self._executor.snapshot_session())
        self._ensure_schema()
        self._restore_or_bootstrap()

    @property
    def artifact_root(self) -> Path:
        return self._executor.artifact_root

    @property
    def runtime_root(self) -> Path:
        return self._executor.runtime_root

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    input_root TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    graph_version INTEGER NOT NULL,
                    graph_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    template_session_json TEXT NOT NULL,
                    chat_history_json TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_active
                ON sessions(is_active)
                WHERE is_active = 1;

                CREATE TABLE IF NOT EXISTS case_states (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    input_root TEXT NOT NULL,
                    case_state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS graph_snapshots (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
                    graph_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    graph_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    graph_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    parent_event_id TEXT,
                    UNIQUE(session_id, graph_id, event_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_graph_seq
                ON events(session_id, graph_id, seq);

                CREATE TABLE IF NOT EXISTS patches (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    graph_id TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    result TEXT NOT NULL,
                    patch_json TEXT NOT NULL,
                    UNIQUE(session_id, graph_id, patch_id)
                );
                """
            )

    def _load_active_session(self) -> Optional[MockSession]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.session_id, s.template_session_json, s.chat_history_json, c.case_state_json, g.graph_json
                FROM sessions AS s
                JOIN case_states AS c ON c.session_id = s.session_id
                JOIN graph_snapshots AS g ON g.session_id = s.session_id
                WHERE s.is_active = 1
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        graph = ActionGraph.model_validate(json.loads(str(row["graph_json"])))
        case_state = CaseState.model_validate(json.loads(str(row["case_state_json"])))
        chat_history = json.loads(str(row["chat_history_json"]))
        session = MockSession(
            session_id=str(row["session_id"]),
            case_state=case_state,
            graph=graph,
            chat_history=list(chat_history if isinstance(chat_history, list) else []),
        )
        self._template_session = MockSession.model_validate(json.loads(str(row["template_session_json"])))
        return session

    def _restore_or_bootstrap(self) -> None:
        with self._lock:
            active = self._load_active_session()
            if active is None:
                bootstrap = _model_copy(self._executor.snapshot_session())
                self._template_session = _model_copy(bootstrap)
                self._executor.load_session(bootstrap, initial_session=self._template_session)
                self._persist_current_state(mark_active=True)
                return
            self._executor.load_session(active, initial_session=self._template_session)

    def _persist_events(self, conn: sqlite3.Connection, session: MockSession) -> None:
        for event in session.graph.events:
            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    session_id, graph_id, event_id, ts, actor_type, actor_id, event_type, target_id, payload_json, parent_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.graph.graph_id,
                    event.event_id,
                    event.ts.isoformat(),
                    event.actor_type,
                    event.actor_id,
                    event.event_type,
                    event.target_id,
                    _json_dumps(event.payload),
                    event.parent_event_id,
                ),
            )

    def _persist_patches(self, conn: sqlite3.Connection, session: MockSession) -> None:
        for patch in [*session.graph.proposals, *session.graph.patch_history]:
            conn.execute(
                """
                INSERT INTO patches (session_id, graph_id, patch_id, timestamp, result, patch_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, graph_id, patch_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    result = excluded.result,
                    patch_json = excluded.patch_json
                """,
                (
                    session.session_id,
                    session.graph.graph_id,
                    patch.patch_id,
                    patch.timestamp.isoformat(),
                    patch.result,
                    _json_dumps(patch.model_dump(mode="json")),
                ),
            )

    def _persist_current_state(self, *, mark_active: bool = True) -> None:
        session = self._executor.snapshot_session()
        now = _utc_now().isoformat()
        with self._connect() as conn:
            created_row = conn.execute(
                "SELECT created_at FROM sessions WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            created_at = str(created_row["created_at"]) if created_row is not None else now
            if mark_active:
                conn.execute("UPDATE sessions SET is_active = 0 WHERE is_active = 1 AND session_id <> ?", (session.session_id,))
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, case_id, domain, input_root, graph_id, graph_version, graph_status,
                    created_at, updated_at, is_active, template_session_json, chat_history_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    case_id = excluded.case_id,
                    domain = excluded.domain,
                    input_root = excluded.input_root,
                    graph_id = excluded.graph_id,
                    graph_version = excluded.graph_version,
                    graph_status = excluded.graph_status,
                    updated_at = excluded.updated_at,
                    is_active = excluded.is_active,
                    template_session_json = excluded.template_session_json,
                    chat_history_json = excluded.chat_history_json
                """,
                (
                    session.session_id,
                    session.case_state.case_id,
                    session.case_state.domain,
                    session.case_state.input_root,
                    session.graph.graph_id,
                    session.graph.version,
                    session.graph.status,
                    created_at,
                    now,
                    1 if mark_active else 0,
                    _json_dumps(self._template_session.model_dump(mode="json")),
                    _json_dumps(session.chat_history),
                ),
            )
            conn.execute(
                """
                INSERT INTO case_states (session_id, case_id, domain, input_root, case_state_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    case_id = excluded.case_id,
                    domain = excluded.domain,
                    input_root = excluded.input_root,
                    case_state_json = excluded.case_state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    session.case_state.case_id,
                    session.case_state.domain,
                    session.case_state.input_root,
                    _json_dumps(session.case_state.model_dump(mode="json")),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO graph_snapshots (session_id, graph_id, version, graph_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    graph_id = excluded.graph_id,
                    version = excluded.version,
                    graph_json = excluded.graph_json,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    session.graph.graph_id,
                    session.graph.version,
                    _json_dumps(session.graph.model_dump(mode="json")),
                    now,
                ),
            )
            self._persist_events(conn, session)
            self._persist_patches(conn, session)

    def _load_session(self, session: MockSession, *, template_session: Optional[MockSession] = None) -> None:
        self._template_session = _model_copy(template_session) if template_session is not None else _model_copy(self._template_session)
        self._executor.load_session(session, initial_session=self._template_session)
        self._persist_current_state(mark_active=True)

    def _purge_workspace(self, graph_id: str) -> None:
        artifact_dir = self.artifact_root / graph_id
        runtime_dir = self.runtime_root / graph_id
        for path in (artifact_dir, runtime_dir):
            if path.is_symlink():
                path.unlink(missing_ok=True)
                continue
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            if path.exists() and path.is_file():
                path.unlink(missing_ok=True)

    def snapshot_session(self) -> MockSession:
        with self._lock:
            return self._executor.snapshot_session()

    def snapshot_graph(self) -> ActionGraph:
        with self._lock:
            return self._executor.snapshot_graph()

    def snapshot_events(self) -> List[GraphEvent]:
        with self._lock:
            return self._executor.snapshot_events()

    def events_for_active_graph(self, *, after_event_id: Optional[str] = None) -> List[GraphEvent]:
        with self._lock:
            session = self._executor.snapshot_session()
        after_value = after_event_id if isinstance(after_event_id, str) and after_event_id.strip() else None
        with self._connect() as conn:
            params: List[Any] = [session.session_id, session.graph.graph_id]
            sql = """
                SELECT seq, event_id, ts, actor_type, actor_id, event_type, target_id, payload_json, parent_event_id
                FROM events
                WHERE session_id = ? AND graph_id = ?
            """
            if after_value:
                after_row = conn.execute(
                    "SELECT seq FROM events WHERE session_id = ? AND graph_id = ? AND event_id = ?",
                    (session.session_id, session.graph.graph_id, after_value),
                ).fetchone()
                if after_row is not None:
                    sql += " AND seq > ?"
                    params.append(int(after_row["seq"]))
            sql += " ORDER BY seq ASC"
            rows = conn.execute(sql, tuple(params)).fetchall()
        events: List[GraphEvent] = []
        for row in rows:
            events.append(
                GraphEvent(
                    event_id=str(row["event_id"]),
                    graph_id=session.graph.graph_id,
                    ts=datetime.fromisoformat(str(row["ts"])),
                    actor_type=str(row["actor_type"]),  # type: ignore[arg-type]
                    actor_id=str(row["actor_id"]),
                    event_type=str(row["event_type"]),
                    target_id=str(row["target_id"]),
                    payload=json.loads(str(row["payload_json"])),
                    parent_event_id=row["parent_event_id"],
                )
            )
        return events

    def register_session(
        self,
        *,
        case_id: str,
        input_root: str,
        domain: str = "prostate",
        session_id: Optional[str] = None,
    ) -> MockSession:
        with self._lock:
            session = create_registered_session(
                case_id=case_id,
                input_root=input_root,
                domain=domain,
                session_id=session_id,
            )
            self._template_session = _model_copy(session)
            self._executor.load_session(session, initial_session=self._template_session)
            self._persist_current_state(mark_active=True)
            return self._executor.snapshot_session()

    def replace_graph(self, graph: ActionGraph | Dict[str, Any], *, author_id: str = "brain", reason: str = "planner_graph") -> Dict[str, Any]:
        graph_model = graph if isinstance(graph, ActionGraph) else ActionGraph.model_validate(graph)
        with self._lock:
            session = self._executor.snapshot_session()
            session.graph = _model_copy(graph_model)
            session.graph.case_id = session.case_state.case_id
            session.graph.domain = session.case_state.domain
            event = GraphEvent(
                event_id=_next_event_id(session.graph),
                graph_id=session.graph.graph_id,
                actor_type="supervisor",
                actor_id=author_id,
                event_type="graph_replaced",
                target_id=session.graph.graph_id,
                payload={"reason": reason, "version": session.graph.version},
            )
            session.graph.events.append(event)
            focus_node = _focus_node_id(session.graph)
            session.case_state.active_graph_id = session.graph.graph_id
            session.case_state.active_node_id = focus_node
            session.case_state.selected_artifacts = []
            session.case_state.last_error = None
            session.case_state.last_event_id = event.event_id
            session.case_state.ui_focus = {"panel": "graph", "selected_node": "" if focus_node is None else focus_node}
            self._load_session(session)
            return {
                "graph_replaced": True,
                "graph": self._executor.snapshot_graph().model_dump(mode="json"),
                "session": self._executor.snapshot_session().model_dump(mode="json"),
            }

    def stage_patch(self, patch: ExecutionPatch | Dict[str, Any], *, author_id: Optional[str] = None) -> ExecutionPatch:
        patch_model = patch if isinstance(patch, ExecutionPatch) else ExecutionPatch.model_validate(patch)
        with self._lock:
            session = self._executor.snapshot_session()
            if str(patch_model.graph_id) != str(session.graph.graph_id):
                raise ValueError(f"patch graph mismatch: {patch_model.graph_id} != {session.graph.graph_id}")
            if int(patch_model.applies_to_version) != int(session.graph.version):
                raise ValueError(f"patch version mismatch: {patch_model.applies_to_version} != {session.graph.version}")
            session.graph.proposals.append(_model_copy(patch_model))
            event = GraphEvent(
                event_id=_next_event_id(session.graph),
                graph_id=session.graph.graph_id,
                actor_type=patch_model.author_type,
                actor_id=author_id or patch_model.author_id,
                event_type="patch_previewed",
                target_id=patch_model.patch_id,
                payload={"reason": patch_model.reason, "operations": len(patch_model.operations)},
            )
            session.graph.events.append(event)
            session.case_state.last_event_id = event.event_id
            self._load_session(session)
            return _model_copy(patch_model)

    def post_chat(
        self,
        message: str,
        *,
        assistant_reply: Optional[Dict[str, str]] = None,
        reply_source: str = "mock",
        reply_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            response = self._executor.post_chat(
                message,
                assistant_reply=assistant_reply,
                reply_source=reply_source,
                reply_metadata=reply_metadata,
            )
            self._persist_current_state(mark_active=True)
            return response

    def preview_patch(self, *, reason: str, author_id: str = "supervisor") -> ExecutionPatch:
        with self._lock:
            patch = self._executor.preview_patch(reason=reason, author_id=author_id)
            self._persist_current_state(mark_active=True)
            return patch

    def apply_latest_proposal(self) -> Dict[str, Any]:
        with self._lock:
            response = self._executor.apply_latest_proposal()
            self._persist_current_state(mark_active=True)
            return response

    def apply_patch(self, patch: ExecutionPatch | Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            response = self._executor.apply_patch(patch)
            self._persist_current_state(mark_active=True)
            return response

    def rerun_from_node(self, node_id: str, *, reason: str = "operator rerun", actor_id: str = "operator") -> Dict[str, Any]:
        with self._lock:
            response = self._executor.rerun_from_node(node_id, reason=reason, actor_id=actor_id)
            self._persist_current_state(mark_active=True)
            return response

    def execute_next(self) -> Dict[str, Any]:
        with self._lock:
            response = self._executor.execute_next()
            self._persist_current_state(mark_active=True)
            return response

    def execute_until_done(self, max_steps: int = 20) -> Dict[str, Any]:
        with self._lock:
            response = self._executor.execute_until_done(max_steps=max_steps)
            self._persist_current_state(mark_active=True)
            return response

    def reset(self, *, purge_artifacts: bool = True) -> MockSession:
        with self._lock:
            current = self._executor.snapshot_session()
            if purge_artifacts:
                self._purge_workspace(current.graph.graph_id)
            clean_template = create_reset_session(self._template_session)
            self._template_session = _model_copy(clean_template)
            self._executor.load_session(clean_template, initial_session=self._template_session)
            self._persist_current_state(mark_active=True)
            return self._executor.snapshot_session()


def create_default_store() -> DurableSessionStore:
    return DurableSessionStore()
