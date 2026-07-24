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
    """A node the executor cannot run must not report succeeded.

    Regression guard: these nodes used to fall through to the generic placeholder
    handler, which wrote a JSON/TXT/SVG bundle and returned "succeeded" without any
    tool call and without contract validation.
    """
    store = MockExecutorStore(root_dir=tmp_path)
    node = _replace_graph_with_single_tool_node(store, "brats_mri_segmentation")

    def boom(*args, **kwargs):
        raise AssertionError("run_v3_tool must not be reached for an unhandled tool")

    monkeypatch.setattr(store_module, "run_v3_tool", boom)

    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert node.status == "failed"
    assert "no executor handler for tool brats_mri_segmentation" in outcome.message
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
    record = payload["stage_outputs"]["segment"]["brats_mri_segmentation"][0]
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


def test_report_contradiction_check_is_not_tied_to_the_node_id(tmp_path: Path, monkeypatch) -> None:
    """The segmentation-contradiction guard is keyed on the tool, not on node id.

    The node below runs ``segment_prostate`` -- the tool whose mask
    ``lesion_assessment_meta`` is actually about -- under a node id the compiler
    would never emit, so a guard that grepped for the literal id ``segment_prostate``
    would miss it.
    """
    store = MockExecutorStore(root_dir=tmp_path)
    gland_seg = ActionNode(
        node_id="gland_seg_step",  # deliberately NOT "segment_prostate"
        kind="tool",
        title="Segment Prostate",
        action_type="segment_prostate",
        tool_name="segment_prostate",
        status="succeeded",
        depends_on=[],
        owner="executor",
    )
    report_node = _node(store, "generate_report")
    report_node.depends_on = ["gland_seg_step"]
    store._session.graph.nodes = [gland_seg, report_node]
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
    assert "gland_seg_step succeeded but report says segmentation_usable=false" in outcome.message


def test_report_contradiction_check_ignores_cardiac_segmentation(tmp_path: Path, monkeypatch) -> None:
    """A cardiac graph must not be failed by a prostate-lesion metadata field.

    ``lesion_assessment_meta`` is prostate-lesion specific.  A real cardiac run of
    the engine's generate_report emits::

        {"segmentation_usable": false,
         "lesion_geometry_note": "missing_lesion_or_prostate_mask",
         "adc_available": false}

    which is correct -- there is no prostate mask in a cardiac case -- and says
    nothing about whether ``segment_cardiac_cine`` worked.  Cross-checking it
    against a succeeded cardiac segmentation node used to fail the report node and
    left the whole compiled cardiac graph red.
    """
    store = MockExecutorStore(root_dir=tmp_path)
    cardiac_seg = ActionNode(
        node_id="segment_cardiac_cine",
        kind="tool",
        title="Segment Cardiac Cine",
        action_type="segment_cardiac_cine",
        tool_name="segment_cardiac_cine",
        status="succeeded",
        depends_on=[],
        owner="executor",
    )
    report_node = _node(store, "generate_report")
    report_node.depends_on = ["segment_cardiac_cine"]
    store._session.graph.nodes = [cardiac_seg, report_node]
    store._session.graph.edges = []

    report_dir = tmp_path / "artifacts_out"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "report.json"
    report_json_path.write_text(
        json.dumps(
            {
                "lesion_assessment_meta": {
                    "segmentation_usable": False,
                    "lesion_geometry_note": "missing_lesion_or_prostate_mask",
                    "adc_available": False,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    clinical_report_path = report_dir / "clinical_report.md"
    clinical_report_path.write_text("# cardiac report\n", encoding="utf-8")

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

    assert outcome.status == "succeeded", outcome.message
    assert report_node.status == "succeeded"


# ---------------------------------------------------------------------------
# Cardiac cine: segment_cardiac_cine / classify_cardiac_cine_disease
# ---------------------------------------------------------------------------


def _touch(path: Path, payload: bytes = b"\x1f\x8b nifti-ish") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _cardiac_seg_fixture(tmp_path: Path) -> dict:
    """Files a real segment_cardiac_cine run would leave behind."""
    case_dir = tmp_path / "case"
    cine = _touch(case_dir / "patient061_frame01_0000.nii.gz")
    _touch(case_dir / "patient061_frame01_gt.nii.gz")  # label, must never be an input

    out = tmp_path / "seg_out"
    staged_dir = out / "nnunet_io" / "input"
    pred_dir = out / "nnunet_io" / "pred"
    staged = _touch(staged_dir / "patient061_frame01_0000_0000.nii.gz")
    seg = _touch(pred_dir / "patient061_frame01_0000.nii.gz")
    masks = {
        key: _touch(out / "masks" / f"patient061_frame01_0000_{key}.nii.gz")
        for key in ("rv_mask", "myo_mask", "lv_mask")
    }
    png = _touch(out / "qa" / "qa_snapshot.png", b"\x89PNG\r\n\x1a\n")
    return {
        "case_dir": case_dir,
        "cine": cine,
        "staged": staged,
        "pred_dir": pred_dir,
        "seg": seg,
        "masks": masks,
        "png": png,
    }


def _cardiac_seg_data(fixture: dict) -> dict:
    return {
        "seg_path": str(fixture["seg"]),
        "rv_mask_path": str(fixture["masks"]["rv_mask"]),
        "myo_mask_path": str(fixture["masks"]["myo_mask"]),
        "lv_mask_path": str(fixture["masks"]["lv_mask"]),
        "input_dir": str(fixture["staged"].parent),
        "pred_dir": str(fixture["pred_dir"]),
        "case_results": [
            {
                "case_id": "patient061_frame01_0000",
                "seg_path": str(fixture["seg"]),
                "rv_mask_path": str(fixture["masks"]["rv_mask"]),
                "myo_mask_path": str(fixture["masks"]["myo_mask"]),
                "lv_mask_path": str(fixture["masks"]["lv_mask"]),
            }
        ],
        "note": "ACDC convention: 1=RV, 2=MYO, 3=LV.",
    }


def _install_cardiac_fake(monkeypatch, fixture: dict, calls: list, *, snapshot_ok: bool = True):
    """Monkeypatch run_v3_tool with a per-tool fake, recording every call."""

    def fake_run_v3_tool(tool_name, args, **kwargs):
        calls.append((tool_name, dict(args), dict(kwargs)))
        if tool_name == "segment_cardiac_cine":
            data = _cardiac_seg_data(fixture)
            generated = [
                {"path": str(fixture["seg"]), "kind": "nifti", "description": "Cardiac segmentation", "media_type": None},
                {"path": str(fixture["masks"]["rv_mask"]), "kind": "nifti", "description": "RV mask", "media_type": None},
                {"path": str(fixture["masks"]["myo_mask"]), "kind": "nifti", "description": "Myocardium mask", "media_type": None},
                {"path": str(fixture["masks"]["lv_mask"]), "kind": "nifti", "description": "LV mask", "media_type": None},
            ]
        elif tool_name == "generate_qa_snapshot":
            if not snapshot_ok:
                raise RuntimeError("matplotlib is unavailable in this runtime")
            data = {
                "output_png": str(fixture["png"]),
                "input_nifti": args.get("input_nifti", ""),
                "mask_nifti": args.get("mask_nifti", ""),
                "selected_frame": -1,
                "selected_slice": 4,
            }
            generated = [
                {"path": str(fixture["png"]), "kind": "png", "description": "QA snapshot with mask overlay", "media_type": "image/png"},
            ]
        elif tool_name == "classify_cardiac_cine_disease":
            classification = _touch(fixture["case_dir"].parent / "cls" / "cardiac_cine_classification.json", b"{}")
            data = {
                "classification_path": str(classification),
                "predicted_group": "MINF",
                "ground_truth_group": "MINF",
                "ground_truth_match": True,
                "needs_vlm_review": False,
                "metrics": {"lv_ef_percent": 31.2},
                "phase_indices": {"ed_index_0based": 0, "es_index_0based": 1},
            }
            generated = [
                {"path": str(classification), "kind": "json", "description": "Cardiac classification", "media_type": "application/json"},
            ]
        else:  # pragma: no cover - guards against an unexpected dispatch
            raise AssertionError(f"unexpected tool dispatched: {tool_name}")
        return V3ToolRunResult(
            tool_name=tool_name,
            data=data,
            warnings=[],
            source_artifacts=[],
            generated_artifacts=generated,
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)
    return fake_run_v3_tool


def _cardiac_seg_node(store: MockExecutorStore, fixture: dict) -> ActionNode:
    node = _replace_graph_with_single_tool_node(store, "segment_cardiac_cine")
    node.inputs = {"cine_ref": "@case.input"}
    store._session.case_state.input_root = str(fixture["case_dir"])
    store._session.case_state.domain = "cardiac"
    return node


def test_segment_cardiac_cine_node_reaches_run_v3_tool(tmp_path: Path, monkeypatch) -> None:
    """The node that used to raise MissingExecutorHandlerError now really dispatches."""
    fixture = _cardiac_seg_fixture(tmp_path)
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls)

    outcome = store._run_node(node)

    assert outcome.status == "succeeded", outcome.message
    assert node.status == "succeeded"
    seg_calls = [call for call in calls if call[0] == "segment_cardiac_cine"]
    assert len(seg_calls) == 1
    tool_name, args, kwargs = seg_calls[0]
    assert args["cine_path"] == str(fixture["cine"])
    assert args["output_subdir"]
    assert kwargs["run_id"] == store._session.graph.graph_id
    assert node.outputs["seg_path"] == str(fixture["seg"])
    assert node.outputs["rv_mask_path"] == str(fixture["masks"]["rv_mask"])
    assert node.outputs["execution_mode"] == "v3_tool"

    case_state_path = tmp_path / "root" / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["segment"]["segment_cardiac_cine"][0]
    assert record["ok"] is True
    assert record["consumable"] is True
    assert sorted(record["validation"]["resolved_output_paths"]) == [
        "lv_mask_path",
        "myo_mask_path",
        "rv_mask_path",
        "seg_path",
    ]


def test_segment_cardiac_cine_never_feeds_the_ground_truth_label_to_nnunet(tmp_path: Path, monkeypatch) -> None:
    """The demo case ships a *_gt volume next to the cine; it must not be an input."""
    fixture = _cardiac_seg_fixture(tmp_path)
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls)

    store._run_node(node)

    args = next(call[1] for call in calls if call[0] == "segment_cardiac_cine")
    # A file, not the directory -- passing the directory would segment the label too.
    assert args["cine_path"] == str(fixture["cine"])
    assert "_gt" not in args["cine_path"]
    assert args["cine_path"] != str(fixture["case_dir"])


def test_segment_cardiac_cine_rejects_ambiguous_directory_input(tmp_path: Path, monkeypatch) -> None:
    """Two candidate volumes plus a label: fail loudly rather than guess."""
    fixture = _cardiac_seg_fixture(tmp_path)
    _touch(fixture["case_dir"] / "patient061_frame12_0000.nii.gz")
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)

    def boom(*args, **kwargs):
        raise AssertionError("run_v3_tool must not be reached with an ambiguous input")

    monkeypatch.setattr(store_module, "run_v3_tool", boom)

    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert "ambiguous" in outcome.message
    assert "patient061_frame01_gt.nii.gz" in outcome.message


def test_segment_cardiac_cine_emits_an_overlay_png_artifact(tmp_path: Path, monkeypatch) -> None:
    """Cardiac tools emit NIfTI only; the node must also surface a viewable PNG."""
    fixture = _cardiac_seg_fixture(tmp_path)
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls)

    outcome = store._run_node(node)
    assert outcome.status == "succeeded"

    snapshot_calls = [call for call in calls if call[0] == "generate_qa_snapshot"]
    assert len(snapshot_calls) == 1
    _, args, _ = snapshot_calls[0]
    # Overlay is rendered on the volume nnUNet actually predicted on.
    assert args["input_nifti"] == str(fixture["staged"])
    assert args["mask_nifti"] == str(fixture["seg"])

    node_artifacts = [a for a in store._session.graph.artifacts if a.node_id == node.node_id]
    pngs = [a for a in node_artifacts if a.kind == "png"]
    assert len(pngs) == 1
    assert pngs[0].role == "preview"
    assert pngs[0].uri.endswith("qa_snapshot.png")
    assert node.outputs["qa_snapshot_path"] == str(fixture["png"])
    assert node.outputs["qa_snapshot_error"] == ""


def test_segment_cardiac_cine_records_snapshot_failure_without_faking_it(tmp_path: Path, monkeypatch) -> None:
    """A broken QA renderer must not invent a preview, nor hide that it broke."""
    fixture = _cardiac_seg_fixture(tmp_path)
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls, snapshot_ok=False)

    outcome = store._run_node(node)

    # The segmentation itself really ran and passed its contract, so the node stands.
    assert outcome.status == "succeeded"
    assert node.outputs["qa_snapshot_path"] == ""
    assert "matplotlib is unavailable" in node.outputs["qa_snapshot_error"]
    assert "QA snapshot unavailable" in str(node.notes)
    assert [a for a in store._session.graph.artifacts if a.kind == "png"] == []

    case_state_path = tmp_path / "root" / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["qa"]["generate_qa_snapshot"][0]
    assert record["ok"] is False
    assert record["consumable"] is False
    assert "matplotlib is unavailable" in record["data"]["error"]


def test_segment_cardiac_cine_missing_class_mask_marks_node_failed(tmp_path: Path, monkeypatch) -> None:
    fixture = _cardiac_seg_fixture(tmp_path)
    fixture["masks"]["lv_mask"].unlink()
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _cardiac_seg_node(store, fixture)
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls)

    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert "segment_cardiac_cine missing required output paths" in outcome.message
    assert "lv_mask_path" in outcome.message
    # The QA snapshot is never attempted for a segmentation that failed its contract.
    assert [call for call in calls if call[0] == "generate_qa_snapshot"] == []


def test_classify_cardiac_cine_disease_consumes_the_upstream_segmentation(tmp_path: Path, monkeypatch) -> None:
    fixture = _cardiac_seg_fixture(tmp_path)
    store = MockExecutorStore(root_dir=tmp_path / "root")
    seg_node = _cardiac_seg_node(store, fixture)
    classify_node = ActionNode(
        node_id="classify_cardiac_cine_disease",
        kind="tool",
        title="Classify Cardiac Cine Disease",
        action_type="classify_cardiac_cine_disease",
        tool_name="classify_cardiac_cine_disease",
        status="planned",
        depends_on=["segment_cardiac_cine"],
        inputs={"case_state_path": "@runtime.case_state_path"},
        owner="executor",
    )
    store._session.graph.nodes = [seg_node, classify_node]
    calls: list = []
    _install_cardiac_fake(monkeypatch, fixture, calls)

    assert store._run_node(seg_node).status == "succeeded"
    outcome = store._run_node(classify_node)

    assert outcome.status == "succeeded", outcome.message
    tool_name, args, _ = next(call for call in calls if call[0] == "classify_cardiac_cine_disease")
    assert args["seg_path"] == str(fixture["seg"])
    assert args["cine_path"] == str(fixture["cine"])
    assert classify_node.outputs["predicted_group"] == "MINF"
    assert "MINF" in outcome.message

    case_state_path = tmp_path / "root" / "runtime" / store._session.graph.graph_id / "case_state.json"
    payload = json.loads(case_state_path.read_text(encoding="utf-8"))
    record = payload["stage_outputs"]["classify"]["classify_cardiac_cine_disease"][0]
    assert record["ok"] is True
    assert record["consumable"] is True


def test_classify_cardiac_cine_disease_without_a_segmentation_fails_loudly(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path / "root")
    node = _replace_graph_with_single_tool_node(store, "classify_cardiac_cine_disease")

    def boom(*args, **kwargs):
        raise AssertionError("run_v3_tool must not be reached without a seg_path")

    monkeypatch.setattr(store_module, "run_v3_tool", boom)

    outcome = store._run_node(node)

    assert outcome.status == "failed"
    assert "could not resolve seg_path" in outcome.message


def test_cardiac_tools_are_no_longer_in_the_unhandled_set(tmp_path: Path) -> None:
    """Guard the intent of this change: these must stay wired to real handlers."""
    store = MockExecutorStore(root_dir=tmp_path)
    for tool_name in ("segment_cardiac_cine", "classify_cardiac_cine_disease", "generate_qa_snapshot"):
        assert tool_name not in UNHANDLED_TOOLS
        assert callable(getattr(store, f"_exec_{tool_name}", None))
        assert tool_name in store_module._TOOL_EXECUTION_CONTRACTS
        assert store_module._TOOL_REQUIRED_OUTPUT_PATHS[tool_name]
