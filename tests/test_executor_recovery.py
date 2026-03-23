from __future__ import annotations

from pathlib import Path

import packages.executor.store as store_module
from packages.executor.store import MockExecutorStore
from packages.schemas import ExecutionPatch, PatchOperation
from packages.tools.runtime import V3ToolRunResult


def _node(store: MockExecutorStore, node_id: str):
    return next(node for node in store._session.graph.nodes if node.node_id == node_id)


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")
    return str(path)


def test_failure_patch_continue_records_attempt_history(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    store._session.case_state.sequence_index = {
        "T2w": str(tmp_path / "fixed_series"),
        "ADC": str(tmp_path / "moving_series"),
    }

    calls = {"register": 0}

    def fake_run_v3_tool(tool_name, args, **kwargs):
        if tool_name != "register_to_reference":
            raise AssertionError(f"unexpected tool: {tool_name}")
        calls["register"] += 1
        if calls["register"] == 1:
            return V3ToolRunResult(
                tool_name=tool_name,
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

        resampled_path = _touch(tmp_path / "attempt2" / "resampled.nii.gz")
        transform_path = _touch(tmp_path / "attempt2" / "transform.tfm")
        return V3ToolRunResult(
            tool_name=tool_name,
            data={
                "fixed": str(tmp_path / "fixed_series"),
                "moving": str(tmp_path / "moving_series"),
                "resampled_path": resampled_path,
                "transform_path": transform_path,
            },
            warnings=[],
            source_artifacts=[],
            generated_artifacts=[
                {
                    "path": resampled_path,
                    "kind": "nifti",
                    "description": "resampled.nii.gz",
                    "media_type": "application/octet-stream",
                },
                {
                    "path": transform_path,
                    "kind": "log",
                    "description": "transform.tfm",
                    "media_type": "text/plain",
                },
            ],
        )

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    failed = store._run_node(_node(store, "register_adc"))
    assert failed.status == "failed"
    register_node = _node(store, "register_adc")
    assert register_node.attempt_count == 1
    assert register_node.attempt_history[0]["status"] == "failed"

    patch = ExecutionPatch(
        patch_id="patch-recover-001",
        graph_id=store._session.graph.graph_id,
        author_type="human",
        author_id="operator",
        reason="fix registration inputs and continue",
        applies_to_version=store._session.graph.version,
        operations=[
            PatchOperation(
                op="update_node",
                target="register_adc",
                value={
                    "inputs": {"fixed": "@seq.T2w", "moving": "@seq.ADC"},
                    "notes": "Manual repair before rerun",
                },
            )
        ],
    )
    applied = store.apply_patch(patch)
    assert applied["applied"] is True
    assert "register_adc" in applied["affected_nodes"]
    assert _node(store, "register_adc").status == "patched"
    assert store._session.graph.status == "paused"

    rerun = store.execute_next()
    register_node = _node(store, "register_adc")
    assert rerun["status"] == "succeeded"
    assert register_node.attempt_count == 2
    assert [item["status"] for item in register_node.attempt_history] == ["failed", "succeeded"]
    assert register_node.attempt_history[1]["supersedes"] == register_node.attempt_history[0]["attempt_id"]


def test_rerun_from_node_preserves_old_attempt_artifacts(tmp_path: Path, monkeypatch) -> None:
    store = MockExecutorStore(root_dir=tmp_path)
    _node(store, "identify_sequences").status = "succeeded"
    store._session.case_state.sequence_index = {
        "T2w": str(tmp_path / "fixed_series"),
        "ADC": str(tmp_path / "moving_series"),
    }

    counters = {"register": 0, "segment": 0}

    def fake_run_v3_tool(tool_name, args, **kwargs):
        if tool_name == "register_to_reference":
            counters["register"] += 1
            attempt = counters["register"]
            resampled_path = _touch(tmp_path / f"register_attempt_{attempt}" / "resampled.nii.gz")
            transform_path = _touch(tmp_path / f"register_attempt_{attempt}" / "transform.tfm")
            return V3ToolRunResult(
                tool_name=tool_name,
                data={
                    "fixed": str(tmp_path / "fixed_series"),
                    "moving": str(tmp_path / "moving_series"),
                    "resampled_path": resampled_path,
                    "transform_path": transform_path,
                },
                warnings=[],
                source_artifacts=[],
                generated_artifacts=[
                    {
                        "path": resampled_path,
                        "kind": "nifti",
                        "description": f"register_resampled_{attempt}.nii.gz",
                        "media_type": "application/octet-stream",
                    },
                    {
                        "path": transform_path,
                        "kind": "log",
                        "description": f"register_transform_{attempt}.tfm",
                        "media_type": "text/plain",
                    },
                ],
            )
        if tool_name == "segment_prostate":
            counters["segment"] += 1
            attempt = counters["segment"]
            prostate_mask = _touch(tmp_path / f"segment_attempt_{attempt}" / "prostate_mask.nii.gz")
            zone_mask = _touch(tmp_path / f"segment_attempt_{attempt}" / "zone_mask.nii.gz")
            t2w_input = _touch(tmp_path / f"segment_attempt_{attempt}" / "t2w_input.nii.gz")
            return V3ToolRunResult(
                tool_name=tool_name,
                data={
                    "prostate_mask_path": prostate_mask,
                    "zone_mask_path": zone_mask,
                    "t2w_input_path": t2w_input,
                },
                warnings=[],
                source_artifacts=[],
                generated_artifacts=[
                    {
                        "path": prostate_mask,
                        "kind": "mask",
                        "description": f"prostate_mask_{attempt}.nii.gz",
                        "media_type": "application/octet-stream",
                    },
                    {
                        "path": zone_mask,
                        "kind": "mask",
                        "description": f"zone_mask_{attempt}.nii.gz",
                        "media_type": "application/octet-stream",
                    },
                    {
                        "path": t2w_input,
                        "kind": "nifti",
                        "description": f"t2w_input_{attempt}.nii.gz",
                        "media_type": "application/octet-stream",
                    },
                ],
            )
        raise AssertionError(f"unexpected tool: {tool_name}")

    monkeypatch.setattr(store_module, "run_v3_tool", fake_run_v3_tool)

    register_outcome = store._run_node(_node(store, "register_adc"))
    segment_outcome = store._run_node(_node(store, "segment_prostate"))
    assert register_outcome.status == "succeeded"
    assert segment_outcome.status == "succeeded"

    register_node = _node(store, "register_adc")
    segment_node = _node(store, "segment_prostate")
    first_attempt_id = register_node.current_attempt_id
    old_artifact_count = len(store._session.graph.artifacts)

    response = store.rerun_from_node("register_adc", reason="operator rerun after review")
    assert response["rerun"] is True
    assert _node(store, "register_adc").status == "ready"
    assert _node(store, "segment_prostate").status == "planned"

    rerun_outcome = store.execute_next()
    register_node = _node(store, "register_adc")
    assert rerun_outcome["status"] == "succeeded"
    assert register_node.attempt_count == 2
    assert len(store._session.graph.artifacts) > old_artifact_count

    first_attempt_artifacts = [
        artifact for artifact in store._session.graph.artifacts if artifact.metadata.get("attempt_id") == first_attempt_id
    ]
    second_attempt_artifacts = [
        artifact for artifact in store._session.graph.artifacts if artifact.metadata.get("attempt_id") == register_node.current_attempt_id
    ]
    assert first_attempt_artifacts
    assert second_attempt_artifacts
    assert register_node.attempt_history[1]["supersedes"] == first_attempt_id
    assert segment_node.attempt_history[0]["attempt_id"].startswith("segment_prostate-attempt-")
