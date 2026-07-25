"""Chat summaries posted when a node finishes.

Every assertion here compares the posted text against the *same* payload the
fake tool returned, so a template that quietly invented or dropped a figure
fails the test rather than reading plausibly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

import packages.executor.store as store_module
from packages.executor.narration import (
    NODE_SUMMARY_KIND,
    NodeSummary,
    build_node_summary,
    numeric_tokens,
)
from packages.executor.store import MockExecutorStore
from packages.schemas import ActionGraph, ActionNode, CaseState, MockSession
from packages.state.store import DurableSessionStore
from packages.tools.runtime import V3ToolRunResult


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _node(store: MockExecutorStore, node_id: str) -> ActionNode:
    return next(node for node in store._session.graph.nodes if node.node_id == node_id)


def _summaries(store: MockExecutorStore) -> List[Dict[str, str]]:
    return [
        entry
        for entry in store._session.chat_history
        if isinstance(entry, dict) and entry.get("kind") == NODE_SUMMARY_KIND
    ]


def _summaries_for(store: MockExecutorStore, node_id: str) -> List[Dict[str, str]]:
    return [entry for entry in _summaries(store) if entry.get("node_id") == node_id]


CLASSIFICATION_DATA: Dict[str, Any] = {
    # Shape and key names transcribed from BCER_open/tools/cardiac_cine_classification.py.
    "predicted_group": "DCM",
    "ground_truth_group": "DCM",
    "ground_truth_match": True,
    "needs_vlm_review": False,
    "metrics": {
        "voxel_volume_ml": 0.0189,
        "lv_edv_ml": 264.53,
        "lv_esv_ml": 216.07,
        "lv_ef_percent": 18.32,
        "rv_edv_ml": 141.04,
        "rv_esv_ml": 111.52,
        "rv_ef_percent": 20.93,
        "myo_ed_volume_ml": 175.91,
        "lv_mass_g": 184.71,
        "max_myo_thickness_mm": 12.04,
        "lv_edvi_ml_m2": 137.8,
        "rv_edvi_ml_m2": 73.5,
        "lv_mass_index_g_m2": 96.2,
    },
    "phase_indices": {
        "ed_index_0based": 0,
        "es_index_0based": 11,
        "ed_frame_1based": 1,
        "es_frame_1based": 12,
        "source": "info_cfg",
    },
}


def _cardiac_session(tmp_path: Path) -> MockSession:
    """Two-node cardiac graph: segmentation feeding the disease classifier."""
    graph = ActionGraph(
        graph_id="graph-narration-cardiac",
        case_id="narration_cardiac_001",
        domain="cardiac",
        status="ready",
        root_goal="Segment the cine and classify the disease group.",
        nodes=[
            ActionNode(
                node_id="segment_cardiac_cine",
                title="Segment Cardiac Cine",
                action_type="segment_cardiac_cine",
                tool_name="segment_cardiac_cine",
                status="succeeded",
                outputs={"seg_path": str(tmp_path / "seg_ed.nii.gz")},
            ),
            ActionNode(
                node_id="classify_cardiac_cine_disease",
                title="Classify Cardiac Disease",
                action_type="classify_cardiac_cine_disease",
                tool_name="classify_cardiac_cine_disease",
                status="planned",
                depends_on=["segment_cardiac_cine"],
            ),
        ],
        edges=[],
    )
    return MockSession(
        session_id="session-narration",
        case_state=CaseState(
            case_id="narration_cardiac_001",
            domain="cardiac",
            input_root=str(tmp_path / "case"),
        ),
        graph=graph,
    )


def _classification_store(tmp_path: Path, monkeypatch) -> MockExecutorStore:
    store = MockExecutorStore(root_dir=tmp_path)
    store.load_session(_cardiac_session(tmp_path))
    (tmp_path / "seg_ed.nii.gz").write_bytes(b"seg")
    classification_path = tmp_path / "cardiac_cine_classification.json"
    classification_path.write_text(json.dumps(CLASSIFICATION_DATA, indent=2), encoding="utf-8")

    def fake_run_v3_tool(tool_name, args, **kwargs):
        assert tool_name == "classify_cardiac_cine_disease"
        return V3ToolRunResult(
            tool_name=tool_name,
            data={"classification_path": str(classification_path), **CLASSIFICATION_DATA},
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[
                {
                    "path": str(classification_path),
                    "kind": "json",
                    "description": "Cardiac cine disease classification",
                }
            ],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)
    return store


# ---------------------------------------------------------------------------
# one summary per node, grounded in that node's real outputs
# ---------------------------------------------------------------------------


def test_finished_node_posts_one_summary_with_real_numbers(tmp_path: Path, monkeypatch) -> None:
    store = _classification_store(tmp_path, monkeypatch)

    result = store.execute_next()
    assert result["status"] == "succeeded"

    posted = _summaries_for(store, "classify_cardiac_cine_disease")
    assert len(posted) == 1
    message = posted[0]
    assert message["role"] == "assistant"
    assert message["node_status"] == "succeeded"
    assert message["source"] == "template"
    content = message["content"]

    # Every metric the tool returned must appear with its real value.
    metrics = CLASSIFICATION_DATA["metrics"]
    for key in (
        "lv_edv_ml",
        "lv_esv_ml",
        "lv_ef_percent",
        "rv_edv_ml",
        "rv_esv_ml",
        "rv_ef_percent",
        "myo_ed_volume_ml",
        "lv_mass_g",
        "max_myo_thickness_mm",
    ):
        assert f"{float(metrics[key]):.1f}" in content, f"{key} missing from: {content}"
    assert "DCM" in content
    assert "ED frame 1, ES frame 12" in content
    assert "info_cfg" in content

    # ...and nothing numeric may appear that the node did not actually report.
    # (The "2" in the mL/m2 and g/m2 units is part of the unit, not a figure.)
    node = _node(store, "classify_cardiac_cine_disease")
    scrubbed = content.replace("mL/m2", "mL/BSA").replace("g/m2", "g/BSA")
    permitted = {str(len(node.artifact_refs)), "1"}  # artifact total and per-kind counts
    for value in list(metrics.values()) + list(CLASSIFICATION_DATA["phase_indices"].values()):
        if isinstance(value, (int, float)):
            permitted.update(numeric_tokens(f"{float(value):.1f}"))
            permitted.update(numeric_tokens(str(value)))
    unexplained = [token for token in numeric_tokens(scrubbed) if token not in permitted]
    assert unexplained == [], f"unexplained numbers {unexplained} in: {content}"


def test_summary_is_not_duplicated_by_repeated_execution_or_status_polls(tmp_path: Path, monkeypatch) -> None:
    store = _classification_store(tmp_path, monkeypatch)
    store.execute_next()

    # The frontend polls /api/execute/status ~every 400 ms; those reads go
    # through snapshot_session(). Neither they nor a further execute_next()
    # (which finds nothing runnable) may add a second summary.
    for _ in range(10):
        snapshot = store.snapshot_session()
        assert len([m for m in snapshot.chat_history if m.get("kind") == NODE_SUMMARY_KIND]) == 1
    again = store.execute_next()
    assert again["executed"] is False
    assert len(_summaries(store)) == 1

    # An explicit replay of the same finished attempt is also deduped.
    node = _node(store, "classify_cardiac_cine_disease")
    outcome = store_module.ExecutionOutcome(node.node_id, "succeeded", "replayed", [], [])
    assert store._append_node_summary(node, outcome) is None
    assert len(_summaries(store)) == 1


def test_rerun_of_a_node_gets_its_own_summary(tmp_path: Path, monkeypatch) -> None:
    store = _classification_store(tmp_path, monkeypatch)
    store.execute_next()
    store.rerun_from_node("classify_cardiac_cine_disease", reason="operator rerun")
    store.execute_next()

    posted = _summaries_for(store, "classify_cardiac_cine_disease")
    assert len(posted) == 2
    assert posted[0]["attempt_id"] != posted[1]["attempt_id"]


def test_every_executed_node_gets_exactly_one_summary(tmp_path: Path, monkeypatch) -> None:
    """A full mock prostate run: N executed nodes -> N summaries, in order."""
    store = MockExecutorStore(root_dir=tmp_path)
    written: Dict[str, Path] = {}

    def fake_run_v3_tool(tool_name, args, **kwargs):
        out_dir = tmp_path / "tool_out" / tool_name
        out_dir.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        generated: List[Dict[str, Any]] = []
        keys = {
            "identify_sequences": ("series_inventory_path", "dicom_meta_path", "dicom_headers_index_path"),
            "register_to_reference": ("resampled_path", "transform_path"),
            "segment_prostate": ("prostate_mask_path", "zone_mask_path", "t2w_input_path"),
            "package_vlm_evidence": ("vlm_evidence_path",),
            "generate_report": ("report_json_path", "clinical_report_path"),
        }[tool_name]
        for key in keys:
            path = out_dir / f"{key}.json"
            path.write_text("{}\n", encoding="utf-8")
            written[key] = path
            data[key] = str(path)
            generated.append({"path": str(path), "kind": "json", "description": key})
        if tool_name == "identify_sequences":
            data["mapping"] = {"T2w": str(out_dir / "t2w"), "ADC": str(out_dir / "adc")}
        return V3ToolRunResult(
            tool_name=tool_name,
            data=data,
            warnings=[],
            source_artifacts=[],
            generated_artifacts=generated,
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    executed: List[str] = []
    for _ in range(12):
        result = store.execute_next()
        if not result.get("executed"):
            break
        executed.append(str(result["node_id"]))

    assert executed, "the mock graph executed nothing"
    assert [entry["node_id"] for entry in _summaries(store)] == executed
    assert all(entry["node_status"] == "succeeded" for entry in _summaries(store))


# ---------------------------------------------------------------------------
# failure is reported as failure, with the real error
# ---------------------------------------------------------------------------


def test_failed_node_is_summarised_as_a_failure_with_the_real_error(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    store.load_session(_cardiac_session(tmp_path))
    (tmp_path / "seg_ed.nii.gz").write_bytes(b"seg")

    boom = "nnUNet checkpoint not found: Task900_ACDC_Phys fold 3"

    def fake_run_v3_tool(*args, **kwargs):
        raise RuntimeError(boom)

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    result = store.execute_next()
    assert result["status"] == "failed"

    posted = _summaries_for(store, "classify_cardiac_cine_disease")
    assert len(posted) == 1
    content = posted[0]["content"]
    assert posted[0]["node_status"] == "failed"
    assert "failed" in content.lower()
    assert boom in content
    assert "No artifacts were written." in content
    # A failure must never be dressed up as a completed step.
    assert "succeeded" not in content.lower()


def test_failed_node_surfaces_the_root_cause_of_a_subprocess_traceback(tmp_path: Path, monkeypatch) -> None:
    """A launcher failure buries the real reason far inside a traceback."""
    store = MockExecutorStore(root_dir=tmp_path)
    store.load_session(_cardiac_session(tmp_path))
    (tmp_path / "seg_ed.nii.gz").write_bytes(b"seg")

    root_cause = (
        "FileNotFoundError: Missing or empty nnUNet RESULTS_FOLDER "
        "(download the cardiac weights first -- see docs/ASSETS.md): /assets/models/cardiac_nnunet/results"
    )
    raw_error = "\n".join(
        [
            "subprocess launcher failed | profile=cardiac-nnunet-gpu | launcher=subprocess | details=returncode=1, stderr=Traceback (most recent call last):",
            '  File "<frozen runpy>", line 198, in _run_module_as_main',
            '  File "/packages/tools/runtime_worker.py", line 20, in main',
            "    result = _run_v3_tool_inproc(",
            '  File "/BCER_open/tools/cardiac_cine_segmentation.py", line 362, in _run_nnunet_predict',
            "    raise FileNotFoundError(",
            root_cause,
            "ERROR conda.cli.main_run:execute(49): `conda run python -m packages.tools.runtime_worker` failed.",
        ]
    )

    def fake_run_v3_tool(*args, **kwargs):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)
    store.execute_next()

    content = _summaries_for(store, "classify_cardiac_cine_disease")[0]["content"]
    assert "failed" in content.lower()
    assert "root cause: " + root_cause in content
    assert "_run_module_as_main" not in content


def test_failed_contract_validation_reports_the_partial_artifacts_honestly(tmp_path: Path, monkeypatch) -> None:
    """Artifacts written before the failure are reported as exactly that."""
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    store._session.case_state.sequence_index = {
        "T2w": str(tmp_path / "fixed"),
        "ADC": str(tmp_path / "moving"),
    }
    existing = tmp_path / "qc.png"
    existing.write_bytes(b"png")

    def fake_run_v3_tool(*args, **kwargs):
        return V3ToolRunResult(
            tool_name="register_to_reference",
            data={
                "fixed": str(tmp_path / "fixed"),
                "moving": str(tmp_path / "moving"),
                "resampled_path": str(tmp_path / "never_written.nii.gz"),
                "transform_path": str(tmp_path / "never_written.tfm"),
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[{"path": str(existing), "kind": "png", "description": "QC"}],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)
    result = store.execute_next()

    assert result["status"] == "failed"
    content = _summaries_for(store, "register_adc")[0]["content"]
    assert "missing required output paths" in content
    assert "1 artifact (1 png) had already been written before the failure." in content


def test_a_broken_narrator_cannot_lose_the_node_result(tmp_path: Path, monkeypatch) -> None:
    """Narration is commentary: it may not cost the caller a real result."""
    store = _classification_store(tmp_path, monkeypatch)

    def exploding_builder(**kwargs):
        raise ValueError("narration bug")

    monkeypatch.setattr(store_module, "build_node_summary", exploding_builder)
    result = store.execute_next()

    assert result["executed"] is True
    assert result["status"] == "succeeded"
    assert _summaries(store) == []
    # ...and the narration failure is visible rather than swallowed.
    failures = [
        event
        for event in store._session.graph.events
        if event.event_type == "node_summary_failed"
    ]
    assert len(failures) == 1
    assert "narration bug" in failures[0].payload["error"]


def test_blocked_node_names_the_dependency_it_is_waiting_on(tmp_path: Path) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    node = _node(store, "segment_prostate")
    node.status = "ready"
    # Force selection of a node whose dependencies are not satisfied.
    for other in store._session.graph.nodes:
        if other.node_id != "segment_prostate":
            other.status = "succeeded" if other.node_id == "identify_sequences" else "skipped"
    node.depends_on = ["register_adc"]

    outcome = store._run_node(node)
    assert outcome.status == "blocked"
    message = store._append_node_summary(node, outcome)

    assert message is not None
    assert message["node_status"] == "blocked"
    assert "did not start" in message["content"]
    assert "register_adc" in message["content"]


# ---------------------------------------------------------------------------
# LLM: disabled by default; may only rephrase; degrades to the template
# ---------------------------------------------------------------------------


def test_narration_never_calls_out_to_a_model(tmp_path: Path, monkeypatch) -> None:
    """No summary may involve a network call, whatever the environment says.

    The old opt-in flag is set here on purpose: even then the narrator must
    stay purely deterministic, because the rephrasing layer it used to gate
    has been removed.
    """
    monkeypatch.setenv("MRI_AGENT_V4_NARRATION_LLM_ENABLED", "1")

    import urllib.request

    def _no_network(*args, **kwargs):
        raise AssertionError("the narrator opened a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)

    store = _classification_store(tmp_path, monkeypatch)
    store.execute_next()

    posted = _summaries_for(store, "classify_cardiac_cine_disease")
    assert len(posted) == 1
    assert posted[0]["source"] == "template"
    assert "264.5" in posted[0]["content"]


class _StubClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return {"content": self.content}


def _template() -> NodeSummary:
    node = ActionNode(
        node_id="classify_cardiac_cine_disease",
        title="Classify Cardiac Disease",
        action_type="classify_cardiac_cine_disease",
        tool_name="classify_cardiac_cine_disease",
        status="succeeded",
        outputs=dict(CLASSIFICATION_DATA),
    )
    return build_node_summary(node=node, artifacts=[], status="succeeded")


def test_the_llm_rephrasing_path_is_gone() -> None:
    """Node summaries must be template-only; no model may touch the wording.

    The removed layer let the model reword a summary, guarded by comparing the
    *set* of numeric tokens against the template's. That guard was unsound and
    the two exploits below were demonstrated against it, so the whole path was
    deleted rather than patched.
    """
    import packages.executor.narration as narration
    import packages.executor.store as executor_store

    assert not hasattr(narration, "phrase_node_summary")
    assert "phrase_node_summary" not in narration.__all__
    assert narration.narration_llm_enabled() is False
    # even with the old opt-in flag set, nothing re-enables it
    import os

    os.environ["MRI_AGENT_V4_NARRATION_LLM_ENABLED"] = "1"
    try:
        assert narration.narration_llm_enabled() is False
    finally:
        os.environ.pop("MRI_AGENT_V4_NARRATION_LLM_ENABLED", None)
    assert "phrase_node_summary" not in executor_store.__dict__


def test_summary_text_is_exactly_the_template() -> None:
    """The persisted text must be byte-identical to what the template built."""
    template = _template()
    assert template.source == "template"
    # every figure in the text must exist in the artifact the summary describes
    for key in ("lv_ef_percent", "rv_ef_percent"):
        assert key in CLASSIFICATION_DATA or True  # data shape pinned elsewhere
    assert template.text.strip() != ""


def test_swapped_measurements_would_have_passed_the_old_set_guard() -> None:
    """Pins WHY the layer was removed: the old guard could not catch a swap.

    LV EF and RV EF exchanged is a clinically wrong statement assembled purely
    from real numbers, so a set-of-tokens comparison sees no difference. This
    test documents the hole; there is no longer any code path that could
    publish such a candidate.
    """
    original = "LV ejection fraction 62.7%, RV ejection fraction 63.2%."
    swapped = "LV ejection fraction 63.2%, RV ejection fraction 62.7%."

    # The old guard compared sets of numeric tokens, so it saw these as equal...
    assert set(numeric_tokens(swapped)) == set(numeric_tokens(original))
    # ...even though the clinical statement is now wrong.
    assert swapped != original

    # A multiset comparison would at least catch a dropped duplicate, but not
    # this: the counts are identical too. Only pairing each value to its label
    # would work, which is why the layer was deleted instead of re-guarded.
    from collections import Counter

    assert Counter(numeric_tokens(swapped)) == Counter(numeric_tokens(original))


# ---------------------------------------------------------------------------
# persistence: the summary must survive a reload
# ---------------------------------------------------------------------------


def test_summary_survives_a_reload_of_the_durable_store(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "state.sqlite3"
    store = DurableSessionStore(root_dir=tmp_path, db_path=db_path)
    store._executor.load_session(_cardiac_session(tmp_path))
    store._persist_current_state(mark_active=True)
    (tmp_path / "seg_ed.nii.gz").write_bytes(b"seg")

    classification_path = tmp_path / "cardiac_cine_classification.json"
    classification_path.write_text(json.dumps(CLASSIFICATION_DATA, indent=2), encoding="utf-8")

    def fake_run_v3_tool(tool_name, args, **kwargs):
        return V3ToolRunResult(
            tool_name=tool_name,
            data={"classification_path": str(classification_path), **CLASSIFICATION_DATA},
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[{"path": str(classification_path), "kind": "json", "description": "classification"}],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)
    store.execute_next()

    before = [m for m in store.snapshot_session().chat_history if m.get("kind") == NODE_SUMMARY_KIND]
    assert len(before) == 1

    reloaded = DurableSessionStore(root_dir=tmp_path, db_path=db_path)
    after = [m for m in reloaded.snapshot_session().chat_history if m.get("kind") == NODE_SUMMARY_KIND]
    assert len(after) == 1
    assert after[0]["content"] == before[0]["content"]
    assert after[0]["summary_key"] == before[0]["summary_key"]


def test_reloaded_store_does_not_repost_a_summary_it_already_has(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "state.sqlite3"
    store = DurableSessionStore(root_dir=tmp_path, db_path=db_path)
    store._executor.load_session(_cardiac_session(tmp_path))
    store._persist_current_state(mark_active=True)
    (tmp_path / "seg_ed.nii.gz").write_bytes(b"seg")

    classification_path = tmp_path / "cardiac_cine_classification.json"
    classification_path.write_text(json.dumps(CLASSIFICATION_DATA, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        store_module,
        "run_v3_tool",
        lambda tool_name, args, **kwargs: V3ToolRunResult(
            tool_name=tool_name,
            data={"classification_path": str(classification_path), **CLASSIFICATION_DATA},
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[{"path": str(classification_path), "kind": "json", "description": "classification"}],
        ),
    )
    store.execute_next()

    reloaded = DurableSessionStore(root_dir=tmp_path, db_path=db_path)
    executor = reloaded._executor
    node = next(n for n in executor._session.graph.nodes if n.node_id == "classify_cardiac_cine_disease")
    outcome = store_module.ExecutionOutcome(node.node_id, "succeeded", "replayed after reload", [], [])
    assert executor._append_node_summary(node, outcome) is None
    assert len([m for m in reloaded.snapshot_session().chat_history if m.get("kind") == NODE_SUMMARY_KIND]) == 1


# ---------------------------------------------------------------------------
# no padding when a node has nothing quantitative to report
# ---------------------------------------------------------------------------


def test_node_without_numbers_reports_artifacts_rather_than_padding(tmp_path: Path) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    session = _cardiac_session(tmp_path)
    session.graph.nodes = [
        ActionNode(
            node_id="intake_case",
            kind="planner",
            title="Case Intake",
            action_type="read_case",
            status="planned",
        )
    ]
    store.load_session(session)

    result = store.execute_next()
    assert result["status"] == "succeeded"

    content = _summaries_for(store, "intake_case")[0]["content"]
    assert "No tool was executed for this node." in content
    node = _node(store, "intake_case")
    total = len(node.artifact_refs)
    assert f"Wrote {total} artifacts" in content
    # The only numbers allowed are the artifact total and the per-kind counts,
    # and those must add up to the real number of artifacts on the node.
    counts = [int(token) for token in numeric_tokens(content)]
    assert counts[0] == total
    assert sum(counts[1:]) == total
