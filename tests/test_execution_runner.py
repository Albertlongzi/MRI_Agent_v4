"""Contract tests for the API-level background execution runner.

The runner exists because ``DurableSessionStore.execute_next()`` holds the
store lock for the whole node execution. These tests pin the two properties the
UI depends on:

* a poll of the runner's status never waits on the store lock, so it stays fast
  while a node is executing;
* the runner never reports a state it does not know — a crashed, lost or
  overrunning thread surfaces as an error, not as a permanent spinner.
"""

from __future__ import annotations

import copy
import importlib
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.api.execution_runner import ExecutionRunner, RunnerBusy, select_next_node_id
from packages.executor.store import MockExecutorStore
from packages.state.store import DurableSessionStore


# ── fake store ────────────────────────────────────────────────────────────
# Reproduces the one behaviour that matters here: execute_next() holds the same
# lock that snapshot_graph() needs, for the whole duration of the "node run".


class FakeStore:
    def __init__(
        self,
        node_ids: List[str],
        *,
        step_seconds: float = 0.0,
        gate: Optional[threading.Event] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.graph: Dict[str, Any] = {
            "graph_id": "graph-fake",
            "status": "ready",
            "nodes": [
                {
                    "node_id": node_id,
                    "title": node_id,
                    "status": "planned",
                    "depends_on": ([node_ids[i - 1]] if i else []),
                }
                for i, node_id in enumerate(node_ids)
            ],
        }
        self.step_seconds = step_seconds
        self.gate = gate
        self.raises = raises
        self.calls = 0

    # public store surface used by the runner
    def snapshot_graph(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.graph)

    def snapshot_session(self) -> Dict[str, Any]:
        with self._lock:
            return {"session_id": "session-fake", "graph": copy.deepcopy(self.graph)}

    def execute_next(self) -> Dict[str, Any]:
        with self._lock:  # exactly what DurableSessionStore does
            self.calls += 1
            if self.raises is not None:
                raise self.raises
            if self.gate is not None:
                self.gate.wait(timeout=30.0)
            if self.step_seconds:
                time.sleep(self.step_seconds)
            node = next(
                (n for n in self.graph["nodes"] if n["status"] not in {"succeeded", "failed"}),
                None,
            )
            if node is None:
                self.graph["status"] = "completed"
                return {
                    "executed": False,
                    "reason": "no runnable node",
                    "graph": copy.deepcopy(self.graph),
                    "session": self.snapshot_session(),
                }
            node["status"] = "succeeded"
            if all(n["status"] == "succeeded" for n in self.graph["nodes"]):
                self.graph["status"] = "completed"
            else:
                self.graph["status"] = "running"
            return {
                "executed": True,
                "node_id": node["node_id"],
                "status": "succeeded",
                "message": f"executed {node['node_id']}",
                "artifact_ids": [],
                "event_ids": [],
                "graph": copy.deepcopy(self.graph),
                "session": self.snapshot_session(),
            }


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── status shape ──────────────────────────────────────────────────────────


def test_idle_status_has_the_documented_shape() -> None:
    runner = ExecutionRunner(FakeStore(["a", "b"]))
    status = runner.status()

    for key in (
        "running",
        "run_token",
        "node_id",
        "mode",
        "elapsed_s",
        "error",
        "graph",
        "session",
    ):
        assert key in status, f"missing required status key: {key}"

    assert status["running"] is False
    assert status["run_token"] is None
    assert status["node_id"] is None
    assert status["mode"] is None
    assert status["error"] is None
    assert status["graph_source"] == "live"
    assert status["graph"]["nodes"][0]["node_id"] == "a"


def test_status_without_graph_omits_the_payload() -> None:
    runner = ExecutionRunner(FakeStore(["a"]))
    status = runner.status(include_graph=False)
    assert "graph" not in status
    assert "session" not in status


# ── the point of the whole layer: polls do not block ──────────────────────


def test_polls_stay_fast_while_a_node_holds_the_store_lock() -> None:
    """The store lock is held for the whole 'node run'; status must not wait."""
    store = FakeStore(["slow_node", "next_node"], step_seconds=1.5)
    runner = ExecutionRunner(store)

    t0 = time.monotonic()
    started = runner.start("next")
    start_latency = time.monotonic() - t0

    assert started["started"] is True
    assert started["running"] is True
    assert started["node_id"] == "slow_node"
    assert start_latency < 0.5, f"POST-equivalent blocked for {start_latency:.3f}s"

    latencies: List[float] = []
    saw_running = False
    while runner.is_running():
        p0 = time.monotonic()
        status = runner.status()
        latencies.append(time.monotonic() - p0)
        if status["running"]:
            saw_running = True
            assert status["node_id"] == "slow_node"
        time.sleep(0.05)

    assert saw_running, "run finished before a single poll observed it"
    assert len(latencies) >= 5, "expected several polls during a 1.5s node"
    assert max(latencies) < 0.3, f"a poll blocked for {max(latencies):.3f}s"

    final = runner.status()
    assert final["running"] is False
    assert final["error"] is None
    assert final["node_id"] == "slow_node"
    assert final["node_id_confirmed"] is True
    assert final["node_id_mismatch"] is None
    assert final["result"]["status"] == "succeeded"
    assert final["graph_source"] == "live"


def test_elapsed_seconds_rise_while_running() -> None:
    gate = threading.Event()
    runner = ExecutionRunner(FakeStore(["a"], gate=gate))
    runner.start("next")
    try:
        first = runner.status(include_graph=False)["elapsed_s"]
        time.sleep(0.25)
        second = runner.status(include_graph=False)["elapsed_s"]
        assert second > first
    finally:
        gate.set()
    assert _wait_until(lambda: not runner.is_running())
    finished = runner.status(include_graph=False)
    assert finished["elapsed_s"] > 0
    assert finished["finished_at"] is not None


def test_graph_served_during_a_run_is_a_labelled_pre_run_snapshot() -> None:
    gate = threading.Event()
    store = FakeStore(["a", "b"], gate=gate)
    runner = ExecutionRunner(store)
    runner.start("next")
    try:
        status = runner.status()
        assert status["graph_source"] == "pre_run_snapshot"
        # Nothing is synthesised: the snapshot still shows the pre-run status.
        assert status["graph"]["nodes"][0]["status"] == "planned"
        assert status["graph_captured_at"]
    finally:
        gate.set()
    assert _wait_until(lambda: not runner.is_running())


# ── busy rejection ────────────────────────────────────────────────────────


def test_second_start_is_rejected_and_names_the_in_flight_token() -> None:
    gate = threading.Event()
    store = FakeStore(["a", "b"], gate=gate)
    runner = ExecutionRunner(store)

    first = runner.start("next")
    try:
        with pytest.raises(RunnerBusy) as excinfo:
            runner.start("next")
        detail = excinfo.value.status
        assert detail["running"] is True
        assert detail["run_token"] == first["run_token"]
        assert detail["node_id"] == "a"
        assert first["run_token"] in str(excinfo.value)
    finally:
        gate.set()

    assert _wait_until(lambda: not runner.is_running())
    # Exactly one execution happened — the rejected start was not queued.
    assert store.calls == 1

    second = runner.start("next")
    assert second["run_token"] != first["run_token"]
    assert _wait_until(lambda: not runner.is_running())
    assert store.calls == 2


def test_unknown_mode_is_rejected_before_reserving() -> None:
    runner = ExecutionRunner(FakeStore(["a"]))
    with pytest.raises(ValueError):
        runner.start("sideways")
    assert runner.is_running() is False


# ── failure surfacing ─────────────────────────────────────────────────────


def test_worker_exception_is_captured_and_ends_the_run() -> None:
    store = FakeStore(["a"], raises=RuntimeError("nnUNet exploded"))
    runner = ExecutionRunner(store)
    runner.start("next")

    assert _wait_until(lambda: not runner.is_running()), "crashed run never finished"
    status = runner.status()
    assert status["running"] is False
    assert status["error"] == "RuntimeError: nnUNet exploded"
    assert status["error_type"] == "RuntimeError"
    assert "nnUNet exploded" in status["traceback"]
    assert status["finished_at"] is not None
    # And the runner is usable again, not wedged.
    assert runner.start("next")["started"] is True
    assert _wait_until(lambda: not runner.is_running())


def test_lost_thread_is_reported_instead_of_spinning_forever(monkeypatch) -> None:
    """A worker that dies without reporting must not leave running: true."""
    runner = ExecutionRunner(FakeStore(["a"]))
    monkeypatch.setattr(ExecutionRunner, "_run", lambda self, *a, **k: None)

    runner.start("next")
    assert _wait_until(lambda: not runner.is_running(), timeout=5.0)
    status = runner.status(include_graph=False)
    assert status["running"] is False
    assert status["error_type"] == "RunnerThreadLost"
    assert "without reporting" in status["error"]


def test_wall_clock_guard_flags_an_overrunning_run() -> None:
    gate = threading.Event()
    runner = ExecutionRunner(FakeStore(["a"], gate=gate), max_wall_clock_s=0.2)
    runner.start("next")
    try:
        assert _wait_until(
            lambda: runner.status(include_graph=False)["timed_out"], timeout=5.0
        ), "wall-clock guard never fired"
        status = runner.status(include_graph=False)
        assert status["timed_out"] is True
        assert "wall-clock guard" in status["error"]
        # The thread really is still alive, so the runner says so rather than
        # pretending the run is over.
        assert status["running"] is True
        assert status["thread_alive"] is True
    finally:
        gate.set()
    assert _wait_until(lambda: not runner.is_running())


def test_store_busy_at_start_is_reported_not_swallowed() -> None:
    store = FakeStore(["a"])
    runner = ExecutionRunner(store, start_snapshot_timeout_s=0.1)

    held = threading.Event()
    release = threading.Event()

    def _hog() -> None:
        with store._lock:  # a different thread, so the RLock is genuinely contended
            held.set()
            release.wait(timeout=10.0)

    hog = threading.Thread(target=_hog, daemon=True)
    hog.start()
    assert held.wait(timeout=5.0)
    try:
        payload = runner.start("next")
    finally:
        release.set()
        hog.join(timeout=5.0)

    assert payload["running"] is False
    assert payload["error_type"] == "StoreBusy"
    assert store.calls == 0
    # and the runner is not left reserved
    assert runner.is_running() is False


def test_snapshot_failure_at_start_is_reported_not_left_reserved(monkeypatch) -> None:
    store = FakeStore(["a"])
    runner = ExecutionRunner(store)
    monkeypatch.setattr(
        FakeStore, "snapshot_graph", lambda self: (_ for _ in ()).throw(OSError("disk gone"))
    )
    payload = runner.start("next")
    assert payload["running"] is False
    assert payload["error_type"] == "OSError"
    assert "disk gone" in payload["error"]
    assert store.calls == 0
    assert runner.is_running() is False


# ── until_done: per-node progress ─────────────────────────────────────────


def test_until_done_publishes_progress_node_by_node() -> None:
    store = FakeStore(["a", "b", "c"], step_seconds=0.25)
    runner = ExecutionRunner(store)
    started = runner.start("until_done", max_steps=10)
    assert started["mode"] == "until_done"
    assert started["node_id"] == "a"

    seen_nodes: List[str] = []
    seen_step_counts: List[int] = []
    per_node_clock_restarted = False
    while runner.is_running():
        status = runner.status(include_graph=False)
        if status["running"] and status["node_id"] and status["node_id"] not in seen_nodes:
            seen_nodes.append(status["node_id"])
        seen_step_counts.append(status["steps_completed"])
        # The per-node clock must describe the node on screen, not the loop.
        if status["steps_completed"] >= 1 and status["node_elapsed_s"] < status["elapsed_s"]:
            per_node_clock_restarted = True
        time.sleep(0.03)

    assert seen_nodes == ["a", "b", "c"], f"per-node progress was not published: {seen_nodes}"
    assert max(seen_step_counts) >= 2
    final = runner.status(include_graph=False)
    assert final["running"] is False
    assert final["error"] is None
    assert [step["node_id"] for step in final["steps"]] == ["a", "b", "c"]
    assert final["result"]["step_count"] == 3
    assert final["result"]["graph_status"] == "completed"
    assert per_node_clock_restarted, "node_elapsed_s never restarted between nodes"
    # Each node's own duration, not the cumulative one.
    assert all(step["seconds"] < final["elapsed_s"] for step in final["steps"][1:])


def test_until_done_honours_max_steps() -> None:
    store = FakeStore(["a", "b", "c"])
    runner = ExecutionRunner(store)
    runner.start("until_done", max_steps=2)
    assert _wait_until(lambda: not runner.is_running())
    status = runner.status(include_graph=False)
    assert status["steps_completed"] == 2
    assert store.calls == 2


# ── the mirrored selection rule must not drift from the executor ──────────


def test_select_next_node_id_mirrors_the_executor_selection(tmp_path: Path) -> None:
    """The node id published before a run must be the node the executor picks."""
    store = MockExecutorStore(root_dir=tmp_path)

    def _check() -> Optional[str]:
        expected = store._select_next_node()
        expected_id = None if expected is None else expected.node_id
        actual = select_next_node_id(store.snapshot_graph().model_dump(mode="json"))
        assert actual == expected_id, f"mirror drifted: {actual!r} != {expected_id!r}"
        return expected_id

    nodes = store._session.graph.nodes
    assert _check() is not None

    # Walk the whole pipeline by marking each selected node succeeded.
    guard = 0
    while _check() is not None and guard < 50:
        guard += 1
        picked = store._select_next_node()
        assert picked is not None
        picked.status = "succeeded"
    assert _check() is None

    # A running node always wins, whatever else is pending.
    nodes[-1].status = "running"
    assert _check() == nodes[-1].node_id

    # Unsatisfied dependencies must not be selected.
    for node in nodes:
        node.status = "planned"
    assert _check() == nodes[0].node_id

    # "patched" is runnable, "blocked" is not.
    nodes[0].status = "succeeded"
    nodes[1].status = "patched"
    assert _check() == nodes[1].node_id
    nodes[1].status = "blocked"
    assert _check() != nodes[1].node_id


def test_select_next_node_id_tolerates_missing_or_odd_graphs() -> None:
    assert select_next_node_id(None) is None
    assert select_next_node_id({}) is None
    assert select_next_node_id({"nodes": []}) is None
    assert select_next_node_id({"nodes": [{"node_id": "x", "status": "succeeded"}]}) is None
    assert (
        select_next_node_id({"nodes": [{"node_id": "x", "status": "planned", "depends_on": None}]})
        == "x"
    )


# ── against the real durable store ────────────────────────────────────────


def test_runner_over_the_real_durable_store(tmp_path: Path) -> None:
    """End to end against DurableSessionStore, whose lock is the actual problem."""
    store = DurableSessionStore(root_dir=tmp_path / "ws", db_path=tmp_path / "state.sqlite3")
    store.register_session(
        case_id="runner_case",
        input_root=str(tmp_path / "runner_case"),
        domain="prostate",
        session_id="session-runner",
    )
    expected_first = select_next_node_id(store.snapshot_graph().model_dump(mode="json"))
    assert expected_first is not None

    runner = ExecutionRunner(store)
    started = runner.start("next")
    assert started["node_id"] == expected_first

    assert _wait_until(lambda: not runner.is_running(), timeout=120.0)
    status = runner.status()
    assert status["running"] is False
    assert status["error"] is None, status["error"]
    assert status["node_id"] == expected_first
    assert status["node_id_confirmed"] is True
    assert status["node_id_mismatch"] is None
    assert status["graph_source"] == "live"
    # The node really moved on in the store, and the runner reported the store's
    # own verdict for it rather than assuming success.
    node = next(n for n in status["graph"]["nodes"] if n["node_id"] == expected_first)
    assert node["status"] == status["result"]["status"]
    assert node["status"] in {"succeeded", "failed", "blocked"}


# ── API endpoints ─────────────────────────────────────────────────────────


@pytest.fixture()
def api_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import ``apps.api.main`` against a throwaway workspace (see test_state_api)."""
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
        sys.modules.pop("apps.api.main", None)


def test_execute_endpoints_start_a_run_and_reject_a_second(api_module) -> None:
    from fastapi import HTTPException

    module = api_module
    gate = threading.Event()
    store = FakeStore(["a", "b"], gate=gate)
    module.RUNNER = ExecutionRunner(store)

    started = module.execute_next()
    assert started["started"] is True
    assert started["node_id"] == "a"
    assert started["mode"] == "next"

    try:
        with pytest.raises(HTTPException) as excinfo:
            module.execute_next()
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["reason"] == "busy"
        assert excinfo.value.detail["started"] is False
        assert excinfo.value.detail["run_token"] == started["run_token"]

        # Mutating endpoints refuse to interleave with a run in flight.
        with pytest.raises(HTTPException) as reset_exc:
            module.reset_demo()
        assert reset_exc.value.status_code == 409

        status = module.execute_status()
        assert status["running"] is True
        assert status["node_id"] == "a"
        assert status["graph_source"] == "pre_run_snapshot"
    finally:
        gate.set()

    assert _wait_until(lambda: not module.RUNNER.is_running())
    done = module.execute_status(include_graph=False)
    assert done["running"] is False
    assert done["error"] is None
    assert done["result"]["node_id"] == "a"


def test_execute_status_endpoint_reports_a_crashed_run(api_module) -> None:
    module = api_module
    module.RUNNER = ExecutionRunner(FakeStore(["a"], raises=ValueError("boom")))
    module.execute_next()
    assert _wait_until(lambda: not module.RUNNER.is_running())
    status = module.execute_status(include_graph=False)
    assert status["running"] is False
    assert status["error"] == "ValueError: boom"


# ── regression: the publish-then-start window ─────────────────────────────


def test_unstarted_thread_is_not_reported_as_lost() -> None:
    """A published-but-not-yet-started thread must not fabricate a failure.

    `start()` publishes `self._thread` under the runner lock but calls
    `thread.start()` after releasing it. In that window `thread.is_alive()` is
    False for a perfectly healthy thread, and the reconciler used to conclude
    the worker had died: it reported `RunnerThreadLost` and `running: false`
    for a run that then went on to execute normally -- which also unlocked a
    second concurrent start. `ident` stays None until the thread actually runs,
    so it distinguishes "not started yet" from "ran and vanished".
    """
    runner = ExecutionRunner(FakeStore(["a"]))
    with runner._lock:  # noqa: SLF001 - exercising the internal window on purpose
        runner._running = True
        runner._phase = "executing"
        runner._run_token = "run-test"
        runner._started_at_mono = time.monotonic()
        runner._thread = threading.Thread(target=lambda: None, daemon=True)

    status = runner.status(include_graph=False)

    assert status["running"] is True, "a not-yet-started thread must not end the run"
    assert status["error"] is None
    assert status["error_type"] is None


def test_concurrent_polling_never_fabricates_a_lost_thread() -> None:
    """Hammer start() with a concurrent poller; no run may report a lost thread."""
    runner = ExecutionRunner(FakeStore([f"n{i}" for i in range(40)]))
    seen: List[Dict[str, Any]] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            seen.append(runner.status(include_graph=False))

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        for _ in range(40):
            runner.start("next")
            assert _wait_until(lambda: not runner.is_running(), timeout=10.0)
    finally:
        stop.set()
        poller.join(timeout=5.0)

    lost = [s for s in seen if s.get("error_type") == "RunnerThreadLost"]
    assert not lost, f"fabricated {len(lost)} lost-thread failures out of {len(seen)} polls"
