from __future__ import annotations

import json
from pathlib import Path

import pytest

import packages.executor.store as store_module
from packages.executor.store import MissingExecutorHandlerError, MockExecutorStore
from packages.schemas import ActionNode
from packages.tools.runtime import V3ToolRunResult


def _node(store: MockExecutorStore, node_id: str):
    return next(node for node in store._session.graph.nodes if node.node_id == node_id)


UNHANDLED_TOOLS = (
    "detect_lesion_candidates",
    "extract_roi_features",
    "brats_mri_segmentation",
    "classify_brain_glioma_grade",
    "segment_cardiac_cine",
    "classify_cardiac_cine_disease",
)


def test_register_missing_required_outputs_marks_node_failed(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    store._session.case_state.sequence_index = {
        "T2w": str(tmp_path / "fixed_series"),
        "ADC": str(tmp_path / "moving_series"),
    }

    def fake_run_v3_tool(*args, **kwargs):
        return V3ToolRunResult(
            tool_name="register_to_reference",
            data={
                "fixed": str(tmp_path / "fixed_series"),
                "moving": str(tmp_path / "moving_series"),
                "resampled_path": str(tmp_path / "missing_resampled.nii.gz"),
                "transform_path": str(tmp_path / "missing_transform.tfm"),
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    register_node = _node(store, "register_adc")
    outcome = store._run_node(register_node)

    assert outcome.status == "failed"
    assert register_node.status == "failed"

    case_state_path = tmp_path / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["register"]["register_to_reference"][0]
    assert record["ok"] is False
    assert record["consumable"] is False
    assert "missing required output paths" in record["data"]["error"]


def test_generate_report_contradiction_marks_node_failed(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    _node(store, "register_adc").status = "succeeded"
    _node(store, "segment_prostate").status = "succeeded"
    _node(store, "package_vlm_evidence").status = "succeeded"

    report_dir = tmp_path / "artifacts_out"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "report.json"
    report_json_path.write_text(
        json.dumps(
            {
                "lesion_assessment_meta": {
                    "segmentation_usable": False,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    clinical_report_path = report_dir / "clinical_report.md"
    clinical_report_path.write_text("# report\n", encoding="utf-8")

    def fake_run_v3_tool(*args, **kwargs):
        return V3ToolRunResult(
            tool_name="generate_report",
            data={
                "report_json_path": str(report_json_path),
                "clinical_report_path": str(clinical_report_path),
                "report_txt_path": str(clinical_report_path),
                "vlm_evidence_bundle_path": str(tmp_path / "vlm.json"),
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[
                {
                    "path": str(report_json_path),
                    "kind": "json",
                    "description": "report.json",
                    "media_type": "application/json",
                },
                {
                    "path": str(clinical_report_path),
                    "kind": "report",
                    "description": "clinical_report.md",
                    "media_type": "text/markdown",
                },
            ],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    report_node = _node(store, "generate_report")
    outcome = store._run_node(report_node)

    assert outcome.status == "failed"
    assert report_node.status == "failed"

    case_state_path = tmp_path / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["report"]["generate_report"][0]
    assert record["ok"] is False
    assert record["consumable"] is False
    assert "segmentation_usable=false" in record["data"]["error"]


def test_generate_report_normalizes_clinical_markdown_when_segmentation_is_usable(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    _node(store, "register_adc").status = "succeeded"
    _node(store, "segment_prostate").status = "succeeded"
    _node(store, "package_vlm_evidence").status = "succeeded"

    report_dir = tmp_path / "artifacts_out"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "report.json"
    report_json_path.write_text(
        json.dumps(
            {
                "lesion_assessment_meta": {
                    "segmentation_usable": True,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    clinical_report_path = report_dir / "clinical_report.md"
    clinical_report_path.write_text(
        "Pipeline could not reliably assess lesions (missing ADC and/or segmentation issues).\n",
        encoding="utf-8",
    )

    def fake_run_v3_tool(*args, **kwargs):
        return V3ToolRunResult(
            tool_name="generate_report",
            data={
                "report_json_path": str(report_json_path),
                "clinical_report_path": str(clinical_report_path),
                "report_txt_path": str(clinical_report_path),
                "vlm_evidence_bundle_path": str(tmp_path / "vlm.json"),
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[
                {
                    "path": str(report_json_path),
                    "kind": "json",
                    "description": "report.json",
                    "media_type": "application/json",
                },
                {
                    "path": str(clinical_report_path),
                    "kind": "report",
                    "description": "clinical_report.md",
                    "media_type": "text/markdown",
                },
            ],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    report_node = _node(store, "generate_report")
    outcome = store._run_node(report_node)

    assert outcome.status == "succeeded"
    assert report_node.status == "succeeded"
    rewritten = clinical_report_path.read_text(encoding="utf-8")
    assert "could not reliably assess lesions" not in rewritten.lower()
    assert "could reliably assess lesions" in rewritten.lower()


def _replace_graph_with_single_tool_node(store: MockExecutorStore, tool_name: str) -> ActionNode:
    """Swap the mock graph for a one-node graph running ``tool_name``."""
    node = ActionNode(
        node_id=tool_name,
        kind="tool",
        title=tool_name.replace("_", " ").title(),
        action_type=tool_name,
        tool_name=tool_name,
        status="planned",
        depends_on=[],
        inputs={},
        outputs={},
        checks=[],
        owner="executor",
        editable=True,
        notes="compiler materialized",
    )
    graph = store._session.graph
    graph.nodes = [node]
    graph.edges = []
    return node


def test_unhandled_tool_node_fails_instead_of_faking_success(tmp_path: Path, monkeypatch) -> None:
    """A cardiac node the executor cannot run must not report succeeded.

    Regression guard: these nodes used to fall through to the generic placeholder
    handler, which wrote a JSON/TXT/SVG bundle and returned "succeeded" without any
    tool call and without contract validation.
    """
    store = MockExecutorStore(root_dir=tmp_path)
    node = _replace_graph_with_single_tool_node(store, "segment_cardiac_cine")

    def boom(*args, **kwargs):
        raise AssertionError("run_v3_tool must not be reached for an unhandled tool")

    monkeypatch.setattr(store_module, "run_v3_tool", boom)

    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert node.status == "failed"
    assert "no executor handler for tool segment_cardiac_cine" in outcome.message
    # No placeholder artifact was produced for the node.
    assert node.artifact_refs == []
    assert [a for a in store._session.graph.artifacts if a.node_id == node.node_id] == []
    assert store._session.case_state.last_error is not None
    assert "no executor handler" in store._session.case_state.last_error
    # The reason is on the node so the UI inspector can render it.
    assert "no executor handler" in str(node.notes)

    # The failure is recorded in the runtime case_state under the right stage.
    case_state_path = tmp_path / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["segment"]["segment_cardiac_cine"][0]
    assert record["ok"] is False
    assert record["consumable"] is False
    assert "no executor handler" in record["data"]["error"]


@pytest.mark.parametrize("tool_name", UNHANDLED_TOOLS)
def test_every_unhandled_compiler_tool_raises(tmp_path: Path, tool_name: str) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    node = _replace_graph_with_single_tool_node(store, tool_name)
    with pytest.raises(MissingExecutorHandlerError) as excinfo:
        store._simulate_node_execution(node)
    assert f"no executor handler for tool {tool_name}" in str(excinfo.value)


def test_unhandled_tool_node_fails_the_whole_graph_run(tmp_path: Path) -> None:
    """execute_until_done must stop and report failed, not "N/N done, all green"."""
    store = MockExecutorStore(root_dir=tmp_path)
    _replace_graph_with_single_tool_node(store, "brats_mri_segmentation")

    result = store.execute_until_done(max_steps=5)

    assert result["graph"]["status"] == "failed"
    assert result["graph"]["nodes"][0]["status"] == "failed"
    assert result["steps"][0]["status"] == "failed"
    assert result["graph"]["artifacts"] == []


def test_non_tool_node_still_gets_placeholder_bundle(tmp_path: Path) -> None:
    """Presentational nodes are not tool calls and keep their placeholder behaviour."""
    store = MockExecutorStore(root_dir=tmp_path)
    checkpoint = ActionNode(
        node_id="checkpoint_review",
        kind="human",
        title="Review Checkpoint",
        action_type="review_checkpoint",
        tool_name=None,
        status="planned",
        depends_on=[],
        owner="human",
        editable=True,
    )
    store._session.graph.nodes = [checkpoint]
    store._session.graph.edges = []

    outcome = store._run_node(checkpoint)

    assert outcome.status == "succeeded"
    assert outcome.message == "Non-tool node completed (no tool executed)"
    assert checkpoint.outputs["tool_executed"] is False


def test_identify_sequences_does_not_fall_back_to_a_mock(tmp_path: Path, monkeypatch) -> None:
    """Step 1 must fail loudly rather than fabricate a sequence index."""
    store = MockExecutorStore(root_dir=tmp_path)
    store._session.case_state.input_root = str(tmp_path / "does_not_exist")
    monkeypatch.setattr(store_module, "resolve_demo_case", lambda domain: None)

    def boom(*args, **kwargs):
        raise AssertionError("run_v3_tool must not be reached without a case root")

    monkeypatch.setattr(store_module, "run_v3_tool", boom)

    assert not hasattr(store, "_exec_identify_sequences_mock")

    node = _node(store, "identify_sequences")
    node.status = "planned"
    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert node.status == "failed"
    assert "identify_sequences could not run" in outcome.message
    assert store._session.case_state.sequence_index == {}


def test_report_contradiction_check_covers_non_prostate_segmentation(tmp_path: Path, monkeypatch) -> None:
    """The segmentation-contradiction guard is no longer tied to node id segment_prostate."""
    store = MockExecutorStore(root_dir=tmp_path)
    cardiac_seg = ActionNode(
        node_id="cardiac_seg_step",  # deliberately NOT "segment_prostate"
        kind="tool",
        title="Segment Cardiac Cine",
        action_type="segment_cardiac_cine",
        tool_name="segment_cardiac_cine",
        status="succeeded",
        depends_on=[],
        owner="executor",
    )
    report_node = _node(store, "generate_report")
    report_node.depends_on = ["cardiac_seg_step"]
    store._session.graph.nodes = [cardiac_seg, report_node]
    store._session.graph.edges = []

    report_dir = tmp_path / "artifacts_out"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "report.json"
    report_json_path.write_text(
        json.dumps({"lesion_assessment_meta": {"segmentation_usable": False}}) + "\n",
        encoding="utf-8",
    )
    clinical_report_path = report_dir / "clinical_report.md"
    clinical_report_path.write_text("# report\n", encoding="utf-8")

    def fake_run_v3_tool(*args, **kwargs):
        return V3ToolRunResult(
            tool_name="generate_report",
            data={
                "report_json_path": str(report_json_path),
                "clinical_report_path": str(clinical_report_path),
                "report_txt_path": str(clinical_report_path),
                "vlm_evidence_bundle_path": str(tmp_path / "vlm.json"),
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    outcome = store._run_node(report_node)

    assert outcome.status == "failed"
    assert "cardiac_seg_step succeeded but report says segmentation_usable=false" in outcome.message
