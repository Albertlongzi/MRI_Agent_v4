"""Background execution runner for the API layer.

Why this exists
---------------
``DurableSessionStore.execute_next()`` holds ``self._lock`` for the whole node
execution (``packages/state/store.py``).  A cardiac nnUNet node takes ~23 s, and
during that window *every* store read — ``snapshot_graph()`` included — blocks.
So the executor's real ``node.status = "running"`` transition is invisible: the
UI can only show a spinner and then a finished graph.

This module adds a thin runner **on top of** the store instead of refactoring
the store's locking:

* ``start()`` records which node is about to run (read from a graph snapshot
  taken while nothing is in flight — never guessed), then hands the *existing,
  unchanged* synchronous ``store.execute_next()`` to a background thread and
  returns immediately.
* ``status()`` answers from the runner's own state, guarded by the runner's own
  lock.  While a run is in flight it never touches the store lock, so polling
  stays instant.  That is the entire point of this layer.

Honesty rules baked in (this project has been burned by fabricated state):

* The node id published while a step runs is the node the executor's own
  selection rule picks (``select_next_node_id`` mirrors
  ``ExecutorStore._select_next_node``).  When the step returns, the *actual*
  node id from the store's response replaces it and ``node_id_confirmed``
  flips to ``True``.  A disagreement is recorded in ``node_id_mismatch``
  rather than being papered over.
* A thread that raises stores the exception; the next poll reports
  ``running: false`` with a non-null ``error``.  A crashed run can never leave
  the UI spinning.
* A run that overruns ``max_wall_clock_s`` is reported with ``timed_out: true``
  and an error string, while ``running`` stays ``true`` for as long as the
  thread is genuinely alive — the runner will not claim a thread is dead when
  it is not.
* ``graph``/``session`` served during a run come from a snapshot captured
  *before* the run started (or after the previous completed step), and are
  labelled with ``graph_source``.  Nothing is synthesised.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

__all__ = [
    "ExecutionRunner",
    "RunnerBusy",
    "select_next_node_id",
]

# Mirrors ExecutorStore._select_next_node / _is_satisfied.
_RUNNABLE_STATES = {"planned", "ready", "patched"}
_MODES = ("next", "until_done")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunnerBusy(RuntimeError):
    """Raised by :meth:`ExecutionRunner.start` when a run is already in flight."""

    def __init__(self, status: Dict[str, Any]) -> None:
        super().__init__(
            f"execution already in flight (run_token={status.get('run_token')})"
        )
        self.status = status


def select_next_node_id(graph: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the node id ``ExecutorStore.execute_next()`` would pick next.

    Mirror of ``ExecutorStore._select_next_node`` + ``_is_satisfied`` in
    ``packages/executor/store.py``, evaluated over a serialised graph so it can
    run without holding the store lock.  ``tests/test_execution_runner.py``
    pins this against the real executor for a full pipeline; if the executor's
    rule ever changes, that test fails rather than the UI quietly lying.
    """
    if not isinstance(graph, dict):
        return None
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return None

    status_by_id: Dict[str, str] = {}
    for node in nodes:
        if isinstance(node, dict):
            status_by_id[str(node.get("node_id"))] = str(node.get("status") or "")

    for node in nodes:
        if isinstance(node, dict) and str(node.get("status") or "") == "running":
            return str(node.get("node_id"))

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("status") or "") not in _RUNNABLE_STATES:
            continue
        depends_on = node.get("depends_on") or []
        if not isinstance(depends_on, list):
            depends_on = []
        satisfied = all(status_by_id.get(str(dep)) == "succeeded" for dep in depends_on)
        if satisfied:
            return str(node.get("node_id"))
    return None


class ExecutionRunner:
    """Runs ``store.execute_next()`` on a background thread; reports progress.

    The store object is used through its public API only
    (``execute_next``/``snapshot_graph``/``snapshot_session``).  The one
    private touch is a *timed, read-only* acquire of ``store._lock`` when
    serving an idle live snapshot, so that even a poll that races a starting
    run is bounded (see :meth:`_live_snapshot`); it degrades to the cached
    snapshot instead of blocking.
    """

    def __init__(
        self,
        store: Any,
        *,
        max_wall_clock_s: float = 900.0,
        live_snapshot_timeout_s: float = 0.25,
        start_snapshot_timeout_s: float = 10.0,
        default_max_steps: int = 20,
    ) -> None:
        self._store = store
        self._max_wall_clock_s = float(max_wall_clock_s)
        self._live_snapshot_timeout_s = float(live_snapshot_timeout_s)
        self._start_snapshot_timeout_s = float(start_snapshot_timeout_s)
        self._default_max_steps = int(default_max_steps)

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        self._running: bool = False
        self._phase: str = "idle"  # idle | starting | executing | finished
        self._run_token: Optional[str] = None
        self._node_id: Optional[str] = None
        self._node_id_confirmed: bool = False
        self._node_id_mismatch: Optional[Dict[str, Optional[str]]] = None
        self._mode: Optional[str] = None
        self._max_steps: int = self._default_max_steps
        self._started_at_iso: Optional[str] = None
        self._started_at_mono: Optional[float] = None
        self._node_started_at_iso: Optional[str] = None
        self._node_started_at_mono: Optional[float] = None
        self._node_elapsed_s: float = 0.0
        self._finished_at_iso: Optional[str] = None
        self._elapsed_s: float = 0.0
        self._error: Optional[str] = None
        self._error_type: Optional[str] = None
        self._traceback: Optional[str] = None
        self._timed_out: bool = False
        self._steps: List[Dict[str, Any]] = []
        self._result: Optional[Dict[str, Any]] = None
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_source: str = "none"

    # ── public API ────────────────────────────────────────────────────────

    @property
    def max_wall_clock_s(self) -> float:
        return self._max_wall_clock_s

    def is_running(self) -> bool:
        with self._lock:
            self._reconcile_locked()
            return self._running

    def start(self, mode: str = "next", *, max_steps: Optional[int] = None) -> Dict[str, Any]:
        """Reserve the runner, snapshot the graph, launch the worker thread.

        Returns the accepted-start payload.  Raises :class:`RunnerBusy` when a
        run is already in flight — starts are never silently queued.
        """
        if mode not in _MODES:
            raise ValueError(f"unknown execution mode: {mode!r} (expected one of {_MODES})")
        steps_limit = self._default_max_steps if max_steps is None else int(max_steps)
        token = f"run-{uuid.uuid4().hex[:12]}"

        # Phase 1 — reserve.  Held briefly; no store access inside the lock.
        with self._lock:
            self._reconcile_locked()
            if self._running:
                raise RunnerBusy(self._status_locked())
            self._running = True
            self._phase = "starting"
            self._run_token = token
            self._mode = mode
            self._max_steps = steps_limit
            self._node_id = None
            self._node_id_confirmed = False
            self._node_id_mismatch = None
            self._started_at_iso = _utc_now_iso()
            self._started_at_mono = time.monotonic()
            self._node_started_at_iso = self._started_at_iso
            self._node_started_at_mono = self._started_at_mono
            self._node_elapsed_s = 0.0
            self._finished_at_iso = None
            self._elapsed_s = 0.0
            self._error = None
            self._error_type = None
            self._traceback = None
            self._timed_out = False
            self._steps = []
            self._result = None
            self._thread = None

        # Phase 2 — read the node that is about to run from a real snapshot.
        # Nothing of ours is in flight, so this is a fast, uncontended read;
        # the timeout only guards an unrelated writer holding the store lock.
        try:
            snapshot = self._live_snapshot(self._start_snapshot_timeout_s)
        except BaseException as exc:  # noqa: BLE001 — must not leave us reserved
            with self._lock:
                self._finish_locked(
                    error=f"{type(exc).__name__}: {exc}".strip() or type(exc).__name__,
                    error_type=type(exc).__name__,
                    tb="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
                )
                return self._status_locked()
        if snapshot is None:
            message = (
                "could not snapshot the graph before starting: the store lock was held "
                f"for more than {self._start_snapshot_timeout_s:.1f}s by another writer"
            )
            with self._lock:
                self._finish_locked(error=message, error_type="StoreBusy")
                return self._status_locked()

        node_id = select_next_node_id(snapshot.get("graph"))

        # Phase 3 — publish and launch.
        with self._lock:
            self._snapshot = snapshot
            self._snapshot_source = "pre_run_snapshot"
            self._node_id = node_id
            self._phase = "executing"
            thread = threading.Thread(
                target=self._run,
                args=(token, mode, steps_limit),
                name=f"execution-runner-{token}",
                daemon=True,
            )
            self._thread = thread
            payload = self._status_locked()
        thread.start()

        payload["started"] = True
        return payload

    def status(self, *, include_graph: bool = True) -> Dict[str, Any]:
        """Current runner state.  Never blocks on the store lock while running."""
        with self._lock:
            self._reconcile_locked()
            payload = self._status_locked()
            running = bool(payload["running"])
            cached = self._snapshot
            cached_source = self._snapshot_source

        if not include_graph:
            return payload

        if running:
            # Deliberate: serving the pre-run / last-completed-step snapshot is
            # what keeps this endpoint instant.  Labelled, never synthesised.
            payload["graph"] = (cached or {}).get("graph")
            payload["session"] = (cached or {}).get("session")
            payload["graph_source"] = cached_source if cached else "none"
            payload["graph_captured_at"] = (cached or {}).get("captured_at")
            return payload

        live = self._live_snapshot(self._live_snapshot_timeout_s)
        if live is not None:
            payload["graph"] = live.get("graph")
            payload["session"] = live.get("session")
            payload["graph_source"] = "live"
            payload["graph_captured_at"] = live.get("captured_at")
        else:
            payload["graph"] = (cached or {}).get("graph")
            payload["session"] = (cached or {}).get("session")
            payload["graph_source"] = "cached_store_busy" if cached else "none"
            payload["graph_captured_at"] = (cached or {}).get("captured_at")
        return payload

    def forget_finished_run(self) -> bool:
        """Drop the record of a completed run.

        After the graph is reset or rebuilt, a finished run's ``result``,
        ``steps`` and ``node_id`` describe nodes that no longer exist, so any
        client reading them is being told about a discarded graph. Refuses to
        touch a run that is still in flight. Returns True if state was cleared.
        """
        with self._lock:
            self._reconcile_locked()
            if self._running:
                return False
            self._run_token = None
            self._node_id = None
            self._node_id_confirmed = False
            self._node_id_mismatch = None
            self._mode = None
            self._phase = "idle"
            self._started_at_iso = None
            self._started_at_mono = None
            self._node_started_at_iso = None
            self._node_started_at_mono = None
            self._finished_at_iso = None
            self._elapsed_s = 0.0
            self._node_elapsed_s = 0.0
            self._error = None
            self._error_type = None
            self._traceback = None
            self._timed_out = False
            self._steps = []
            self._result = None
            self._thread = None
            self._snapshot = None
            self._snapshot_source = "none"
            return True

    def busy_payload(self) -> Dict[str, Any]:
        """Status of the in-flight run, without any graph payload."""
        return self.status(include_graph=False)

    # ── worker ────────────────────────────────────────────────────────────

    def _run(self, token: str, mode: str, max_steps: int) -> None:
        try:
            if mode == "next":
                result = self._store.execute_next()
                self._record_step(token, result, index=0)
                final: Dict[str, Any] = dict(result)
            else:
                steps: List[Dict[str, Any]] = []
                result = None
                for index in range(max(1, int(max_steps))):
                    result = self._store.execute_next()
                    steps.append(result)
                    self._record_step(token, result, index=index)
                    if not result.get("executed"):
                        break
                    graph = result.get("graph")
                    graph_status = str((graph or {}).get("status") or "")
                    if graph_status in {"completed", "failed"}:
                        break
                final = {
                    "steps": steps,
                    "graph": (result or {}).get("graph"),
                    "session": (result or {}).get("session"),
                }
            with self._lock:
                if self._run_token == token:
                    self._result = _summarise_result(mode, final)
                    self._finish_locked()
        except BaseException as exc:  # noqa: BLE001 — a crash must surface, not vanish
            detail = f"{type(exc).__name__}: {exc}".strip()
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with self._lock:
                if self._run_token == token:
                    self._finish_locked(
                        error=detail or type(exc).__name__,
                        error_type=type(exc).__name__,
                        tb=tb[-4000:],
                    )

    def _record_step(self, token: str, result: Dict[str, Any], *, index: int) -> None:
        """Publish one completed step.  Uses the store's own returned payload,
        so no additional store lock acquisition is needed."""
        graph = result.get("graph") if isinstance(result, dict) else None
        session = result.get("session") if isinstance(result, dict) else None
        actual_node_id = result.get("node_id") if isinstance(result, dict) else None
        with self._lock:
            if self._run_token != token:
                return
            predicted = self._node_id
            if actual_node_id is not None:
                if predicted is not None and str(predicted) != str(actual_node_id):
                    self._node_id_mismatch = {
                        "predicted": str(predicted),
                        "actual": str(actual_node_id),
                    }
                self._node_id = str(actual_node_id)
                self._node_id_confirmed = True
            self._steps.append(
                {
                    "index": index,
                    "executed": bool(result.get("executed")) if isinstance(result, dict) else False,
                    "node_id": None if actual_node_id is None else str(actual_node_id),
                    "status": result.get("status") if isinstance(result, dict) else None,
                    "message": result.get("message") if isinstance(result, dict) else None,
                    "reason": result.get("reason") if isinstance(result, dict) else None,
                    "started_at": self._node_started_at_iso,
                    "finished_at": _utc_now_iso(),
                    "seconds": self._node_elapsed_locked(),
                    "elapsed_s": self._elapsed_locked(),
                }
            )
            if isinstance(graph, dict):
                self._snapshot = {
                    "graph": graph,
                    "session": session,
                    "captured_at": _utc_now_iso(),
                }
                self._snapshot_source = "step_snapshot"
                # For until_done the loop continues, so publish the node that is
                # about to run next — read from the graph the store just
                # returned, again never guessed.
                if self._mode == "until_done":
                    next_id = select_next_node_id(graph)
                    if next_id is not None and str(graph.get("status") or "") not in {"completed", "failed"}:
                        self._node_id = next_id
                        self._node_id_confirmed = False
                        # That node starts now: restart the per-node clock so the
                        # counter on screen describes it, not the whole loop.
                        self._node_started_at_iso = _utc_now_iso()
                        self._node_started_at_mono = time.monotonic()

    # ── internals (call with self._lock held unless noted) ────────────────

    def _elapsed_locked(self) -> float:
        if self._started_at_mono is None:
            return 0.0
        if not self._running:
            return round(self._elapsed_s, 3)
        return round(time.monotonic() - self._started_at_mono, 3)

    def _node_elapsed_locked(self) -> float:
        """Seconds the *current* node has been executing.

        For mode "next" this equals ``elapsed_s``; for "until_done" it restarts
        at each node so the UI counter describes the node on screen rather than
        the whole loop.
        """
        if self._node_started_at_mono is None:
            return 0.0
        if not self._running:
            return round(self._node_elapsed_s, 3)
        return round(time.monotonic() - self._node_started_at_mono, 3)

    def _finish_locked(
        self,
        *,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        tb: Optional[str] = None,
    ) -> None:
        self._elapsed_s = (
            0.0 if self._started_at_mono is None else round(time.monotonic() - self._started_at_mono, 3)
        )
        self._node_elapsed_s = (
            0.0
            if self._node_started_at_mono is None
            else round(time.monotonic() - self._node_started_at_mono, 3)
        )
        self._running = False
        self._phase = "finished"
        self._finished_at_iso = _utc_now_iso()
        # Once the run is over, node_id must describe what actually ran, not
        # whatever would have run next.
        if self._steps:
            last_node_id = self._steps[-1].get("node_id")
            if last_node_id:
                self._node_id = str(last_node_id)
                self._node_id_confirmed = True
        if error is not None:
            self._error = error
            self._error_type = error_type
            self._traceback = tb

    def _reconcile_locked(self) -> None:
        """Keep reported state honest against the real thread and the clock."""
        if not self._running:
            return
        thread = self._thread
        # `ident` is None until start() has actually run, and stays set once the
        # thread has run. Without this guard there is a window -- the thread is
        # published under the lock but started after it is released -- where a
        # concurrent poll sees a not-yet-started thread as "not alive" and
        # fabricates a RunnerThreadLost failure for a run that is about to
        # proceed normally, which also drops `running` and lets a second start
        # slip through. Only a thread that genuinely ran and vanished counts.
        if thread is not None and thread.ident is not None and not thread.is_alive():
            # The worker always reports through _finish_locked; if the thread is
            # gone and it did not, something killed it. Say so.
            self._finish_locked(
                error="runner thread exited without reporting a result",
                error_type="RunnerThreadLost",
            )
            return
        if self._started_at_mono is not None:
            elapsed = time.monotonic() - self._started_at_mono
            if elapsed > self._max_wall_clock_s and not self._timed_out:
                self._timed_out = True
                self._error = (
                    f"run exceeded the {self._max_wall_clock_s:.0f}s wall-clock guard "
                    f"after {elapsed:.1f}s; the worker thread is still alive"
                )
                self._error_type = "RunnerTimeout"

    def _status_locked(self) -> Dict[str, Any]:
        thread = self._thread
        return {
            "running": self._running,
            "phase": self._phase,
            "run_token": self._run_token,
            "node_id": self._node_id,
            "node_id_confirmed": self._node_id_confirmed,
            "node_id_mismatch": self._node_id_mismatch,
            "mode": self._mode,
            "started_at": self._started_at_iso,
            "finished_at": self._finished_at_iso,
            "elapsed_s": self._elapsed_locked(),
            "node_started_at": self._node_started_at_iso,
            "node_elapsed_s": self._node_elapsed_locked(),
            "error": self._error,
            "error_type": self._error_type,
            "traceback": self._traceback,
            "timed_out": self._timed_out,
            "thread_alive": bool(thread.is_alive()) if thread is not None else False,
            "max_wall_clock_s": self._max_wall_clock_s,
            "steps": list(self._steps),
            "steps_completed": len(self._steps),
            "result": self._result,
        }

    def _live_snapshot(self, timeout: float) -> Optional[Dict[str, Any]]:
        """Snapshot the store, bounded by ``timeout``.

        The store guards everything with a re-entrant lock; acquiring it here
        with a timeout (and holding it across both snapshots) is what makes the
        bound real.  ``None`` means "the store was busy" — the caller must fall
        back to the cached snapshot rather than block.
        """
        lock = getattr(self._store, "_lock", None)
        acquire = getattr(lock, "acquire", None)
        if not callable(acquire):
            return self._capture_snapshot()
        try:
            acquired = lock.acquire(timeout=timeout)
        except TypeError:  # pragma: no cover — non-standard lock object
            acquired = lock.acquire(False)
        if not acquired:
            return None
        try:
            return self._capture_snapshot()
        finally:
            lock.release()

    def _capture_snapshot(self) -> Dict[str, Any]:
        graph = self._store.snapshot_graph()
        session = self._store.snapshot_session()
        return {
            "graph": graph.model_dump(mode="json") if hasattr(graph, "model_dump") else graph,
            "session": session.model_dump(mode="json") if hasattr(session, "model_dump") else session,
            "captured_at": _utc_now_iso(),
        }


def _summarise_result(mode: str, final: Dict[str, Any]) -> Dict[str, Any]:
    """Compact, graph-free summary of a finished run (the graph is served
    separately so status payloads do not carry it twice)."""
    graph = final.get("graph") if isinstance(final, dict) else None
    summary: Dict[str, Any] = {
        "mode": mode,
        "graph_status": (graph or {}).get("status"),
    }
    if mode == "next":
        summary.update(
            {
                "executed": bool(final.get("executed")),
                "node_id": final.get("node_id"),
                "status": final.get("status"),
                "message": final.get("message"),
                "reason": final.get("reason"),
                "artifact_ids": final.get("artifact_ids") or [],
            }
        )
    else:
        steps = final.get("steps") or []
        summary.update(
            {
                "executed": any(bool(step.get("executed")) for step in steps),
                "step_count": len(steps),
                "node_ids": [step.get("node_id") for step in steps if step.get("node_id")],
                "statuses": [step.get("status") for step in steps if step.get("status")],
            }
        )
    return summary
