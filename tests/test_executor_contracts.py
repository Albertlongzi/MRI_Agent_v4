from __future__ import annotations

import json
from pathlib import Path

import packages.executor.store as store_module
from packages.executor.store import MockExecutorStore
from packages.tools.runtime import V3ToolRunResult


def _node(store: MockExecutorStore, node_id: str):
    return next(node for node in store._session.graph.nodes if node.node_id == node_id)


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
