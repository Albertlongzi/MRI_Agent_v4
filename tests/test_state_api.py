from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

# Derived from this file so the suite runs from any checkout and any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.state.store import DurableSessionStore


@pytest.fixture()
def isolated_api_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import ``apps.api.main`` against a throwaway workspace.

    ``apps/api/main.py`` builds ``STORE = create_default_store()`` at module
    scope, and the default store roots itself at the repo checkout. Importing it
    unpatched therefore creates ``state/``, ``runtime/`` and ``artifacts/``
    inside the working tree. Redirecting the factory (and the DB env var that
    ``DurableSessionStore`` also honours) keeps the import confined to tmp_path.
    """
    workspace = tmp_path / "api_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "api_state.sqlite3"

    monkeypatch.setenv("MRI_AGENT_V4_STATE_DB", str(db_path))
    monkeypatch.setattr(
        "packages.state.create_default_store",
        lambda: DurableSessionStore(root_dir=workspace, db_path=db_path),
    )

    sys.modules.pop("apps.api.main", None)
    module = importlib.import_module("apps.api.main")
    try:
        yield module
    finally:
        # Do not leave a module whose STORE points at a deleted tmp_path.
        sys.modules.pop("apps.api.main", None)


def test_durable_store_restores_registered_session(tmp_path: Path) -> None:
    root_dir = tmp_path / "workspace"
    root_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "state.sqlite3"

    store = DurableSessionStore(root_dir=root_dir, db_path=db_path)
    session = store.register_session(
        case_id="brain_case_001",
        input_root=str(tmp_path / "brain_case_001"),
        domain="brain",
        session_id="session-brain",
    )

    restored = DurableSessionStore(root_dir=root_dir, db_path=db_path)
    restored_session = restored.snapshot_session()

    assert restored_session.session_id == session.session_id
    assert restored_session.graph.graph_id == session.graph.graph_id
    assert restored_session.case_state.domain == "brain"
    assert restored_session.case_state.active_graph_id == session.graph.graph_id


def test_reset_allocates_clean_graph_workspace(tmp_path: Path) -> None:
    root_dir = tmp_path / "workspace"
    root_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "state.sqlite3"

    store = DurableSessionStore(root_dir=root_dir, db_path=db_path)
    session = store.register_session(
        case_id="prostate_case_001",
        input_root=str(tmp_path / "prostate_case_001"),
        domain="prostate",
        session_id="session-prostate",
    )
    old_graph_id = session.graph.graph_id
    old_runtime_dir = store.runtime_root / old_graph_id
    old_runtime_dir.mkdir(parents=True, exist_ok=True)
    (old_runtime_dir / "stale.txt").write_text("stale\n", encoding="utf-8")

    reset_session = store.reset()

    assert reset_session.session_id == "session-prostate"
    assert reset_session.graph.graph_id != old_graph_id
    assert not old_runtime_dir.exists()
    assert len(reset_session.graph.events) == 2
    assert reset_session.case_state.active_graph_id == reset_session.graph.graph_id


def test_api_registration_and_sse_contract(tmp_path: Path, isolated_api_module) -> None:
    module = isolated_api_module

    # The imported API must not have written store state into the checkout.
    assert module.STORE.db_path.is_relative_to(tmp_path)
    assert module.STORE.runtime_root.is_relative_to(tmp_path)
    assert module.STORE.artifact_root.is_relative_to(tmp_path)

    response = module.register_session(
        module.SessionRegistrationRequest(
            case_id="api_case_001",
            input_root=str(tmp_path / "api_case_001"),
            domain="brain",
            session_id="session-api",
        )
    )
    assert response["status"] == "registered"
    assert response["session"]["session_id"] == "session-api"
    assert response["graph"]["domain"] == "brain"

    events = module.get_events(after_event_id=None)
    assert len(events) >= 2
    assert events[0]["event_type"] == "case_registered"

    async def _collect_stream() -> str:
        stream = await module.stream_events(after_event_id=None, poll_interval=0.1)
        chunks = []
        iterator = stream.body_iterator
        try:
            for _ in range(3):
                chunk = await iterator.__anext__()
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
                if "event: case_registered" in "".join(chunks):
                    break
        finally:
            await iterator.aclose()
        return "".join(chunks)

    body = asyncio.run(_collect_stream())
    assert "event: stream_ready" in body
    assert "event: case_registered" in body
