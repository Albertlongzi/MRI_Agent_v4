from __future__ import annotations

from scripts.qa_audit import detect_report_contradictions, evaluate_runtime_case_state, validate_planner_mode


def test_detect_report_contradictions_flags_markdown_and_json_conflicts() -> None:
    report_json = {
        "stage_status": {
            "identify_sequences": True,
            "register_to_reference": True,
            "segment_prostate": True,
        },
        "lesion_assessment_meta": {
            "segmentation_usable": True,
        },
        "limitations": [],
    }
    clinical_report = "Pipeline could not reliably assess lesions (missing ADC and/or segmentation issues)."

    contradictions = detect_report_contradictions(
        graph_status="completed",
        report_json=report_json,
        clinical_report_text=clinical_report,
    )

    assert contradictions
    assert any("missing adc and/or segmentation issues" in item for item in contradictions)


def test_evaluate_runtime_case_state_flags_missing_outputs() -> None:
    runtime_case_state = {
        "stage_outputs": {
            "identify": {
                "identify_sequences": [
                    {
                        "ok": True,
                        "consumable": True,
                        "validation": {
                            "resolved_output_paths": {},
                            "missing_output_paths": [],
                        },
                    }
                ]
            },
            "register": {
                "register_to_reference": [
                    {
                        "ok": False,
                        "consumable": False,
                        "validation": {
                            "resolved_output_paths": {},
                            "missing_output_paths": [{"key": "resampled_path", "path": "<missing>"}],
                        },
                    }
                ]
            },
        }
    }

    checks = evaluate_runtime_case_state(runtime_case_state)
    by_name = {check.name: check for check in checks}

    assert by_name["runtime.identify_sequences"].status == "pass"
    assert by_name["runtime.register_to_reference"].status == "fail"
    assert by_name["runtime.segment_prostate"].status == "fail"


def test_validate_planner_mode_rejects_error_mode() -> None:
    assert validate_planner_mode({"mode": "error"}) == "error"
    assert validate_planner_mode({"mode": "graph"}) is None
