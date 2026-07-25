"""Turn a finished executor node into one honest chat sentence block.

Why this module exists
----------------------
The chat panel used to restate the plan after every step ("I will now segment
the myocardium") because nothing posted when a node *finished*:
``MockExecutorStore.post_chat`` only appends when a human sends a message.  This
module builds the assistant message that goes into ``chat_history`` after a node
reaches a terminal state, describing what the node actually did.

Honesty rules baked in (this codebase has shipped fabricated state before):

* Every figure in the text is read out of ``node.outputs`` -- which the executor
  handlers fill from the real v3 tool ``result.data`` -- or counted from the
  ``ArtifactRef`` objects the node actually produced (those refs are only created
  for files that exist on disk).  Nothing is estimated or padded: a value that is
  absent is simply not mentioned.
* A failed node is narrated as a failure carrying the real error string.
* A node that exposes nothing quantitative gets artifact kinds and counts, not
  invented detail.
* Summaries are generated deterministically from each node's real outputs.
  There is no model in this path: see ``narration_llm_enabled`` for why the
  optional rephrasing layer was removed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "NODE_SUMMARY_KIND",
    "NodeSummary",
    "build_node_summary",
    "narration_llm_enabled",
    "numeric_tokens",
]

NODE_SUMMARY_KIND = "node_summary"

# Set to 1/true/on to let the LLM rephrase the deterministic text.  Unset means
# the template is used verbatim and no HTTP call is made.
NARRATION_LLM_ENV_FLAG = "MRI_AGENT_V4_NARRATION_LLM_ENABLED"

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_FINAL_RE = re.compile(r"<final>(.*?)</final>", flags=re.IGNORECASE | re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.IGNORECASE | re.DOTALL)

# An LLM rephrase longer than this is not a rephrase.


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _basename(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    return Path(raw).name or raw


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = _text(value)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fmt(value: Any, *, digits: int = 1) -> Optional[str]:
    """Render a real number, or ``None`` when there is no real number to render."""
    number = _number(value)
    if number is None:
        return None
    return f"{number:.{digits}f}"


def _measure(label: str, value: Any, unit: str, *, digits: int = 1) -> Optional[str]:
    rendered = _fmt(value, digits=digits)
    if rendered is None:
        return None
    return f"{label} {rendered}{unit}" if unit.startswith("%") else f"{label} {rendered} {unit}".strip()


def _join_clauses(items: Sequence[Optional[str]], *, sep: str = ", ") -> str:
    return sep.join(item for item in items if item)


def _truncate(value: str, limit: int) -> str:
    raw = " ".join(_text(value).split())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


_EXCEPTION_LINE_RE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Failure)\b\s*:\s*\S")


def _condense_error(raw: Any, *, limit: int = 480) -> str:
    """Keep the operator-useful part of a multi-line tool failure.

    A runtime failure arrives as ``subprocess launcher failed | ... stderr=Traceback
    ...`` with the actual cause tens of lines further down, so a plain head
    truncation would show frame addresses and hide the reason.  This keeps the
    first line *and* the last exception line -- both literal, neither invented.
    """
    lines = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0]
    cause = ""
    for line in lines:
        if _EXCEPTION_LINE_RE.match(line):
            cause = line
    if cause and cause not in head:
        half = max(160, limit // 2)
        return f"{_truncate(head, half)} | root cause: {_truncate(cause, half)}"
    return _truncate(" ".join(lines), limit)


def _sentence(value: str) -> str:
    """Close a clause with a full stop unless it already carries punctuation."""
    raw = _text(value)
    if not raw or raw.endswith((".", "!", "?", ":", "…")):
        return raw
    return raw + "."


def numeric_tokens(text: str) -> List[str]:
    """Every numeric literal in ``text`` (used to police LLM rephrasing)."""
    return _NUMBER_RE.findall(str(text or ""))


def _artifact_attr(artifact: Any, name: str) -> str:
    if isinstance(artifact, dict):
        return _text(artifact.get(name))
    return _text(getattr(artifact, name, ""))


def _artifact_kind_counts(artifacts: Sequence[Any]) -> List[str]:
    counts: Dict[str, int] = {}
    for artifact in artifacts:
        kind = _artifact_attr(artifact, "kind") or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [f"{count} {kind}" for kind, count in ordered]


def _artifact_sentence(artifacts: Sequence[Any], *, failed: bool = False) -> str:
    total = len(artifacts)
    if total == 0:
        return "No artifacts were recorded for this node." if not failed else "No artifacts were written."
    breakdown = ", ".join(_artifact_kind_counts(artifacts))
    noun = _plural(total, "artifact")
    if failed:
        return f"{total} {noun} ({breakdown}) had already been written before the failure."
    return f"Wrote {total} {noun}: {breakdown}."


def _warning_sentence(outputs: Dict[str, Any]) -> Optional[str]:
    warnings = [w for w in (outputs.get("warnings") or []) if _text(w)]
    if not warnings:
        return None
    first = _truncate(str(warnings[0]), 200)
    if len(warnings) == 1:
        return f"1 warning: {first}"
    return f"{len(warnings)} warnings, first: {first}"


def _profile_sentence(outputs: Dict[str, Any]) -> Optional[str]:
    profile = _text(outputs.get("runtime_profile"))
    if not profile:
        return None
    return f"Runtime profile: {profile}."


# ---------------------------------------------------------------------------
# per-tool detail builders -- every value below is read from node.outputs, which
# the executor handlers populate from the real v3 tool result payload.
# ---------------------------------------------------------------------------


def _detail_identify_sequences(outputs: Dict[str, Any]) -> List[str]:
    mapping = _ensure_dict(outputs.get("mapping"))
    if not mapping:
        return ["The tool returned an empty sequence mapping."]
    pairs = ", ".join(f"{key} -> {_basename(value)}" for key, value in sorted(mapping.items()))
    return [f"Identified {len(mapping)} {_plural(len(mapping), 'sequence')}: {pairs}."]


def _detail_register(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    fixed = _basename(outputs.get("fixed"))
    moving = _basename(outputs.get("moving"))
    if fixed and moving:
        sentences.append(f"Registered {moving} onto {fixed}.")
    written = _join_clauses(
        [
            f"resampled volume {_basename(outputs.get('resampled_path'))}" if _text(outputs.get("resampled_path")) else None,
            f"transform {_basename(outputs.get('transform_path'))}" if _text(outputs.get("transform_path")) else None,
        ]
    )
    if written:
        sentences.append(f"Wrote {written}.")
    qc = _ensure_dict(outputs.get("qc_pngs"))
    if qc:
        sentences.append(f"Rendered {len(qc)} QC {_plural(len(qc), 'PNG')}: {', '.join(sorted(_basename(v) for v in qc.values()))}.")
    return sentences


def _detail_segment_prostate(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    source = _basename(outputs.get("t2w_input_path"))
    if source:
        sentences.append(f"Segmented the prostate on {source}.")
    written = _join_clauses(
        [
            f"gland mask {_basename(outputs.get('prostate_mask_path'))}" if _text(outputs.get("prostate_mask_path")) else None,
            f"zone mask {_basename(outputs.get('zone_mask_path'))}" if _text(outputs.get("zone_mask_path")) else None,
        ]
    )
    if written:
        sentences.append(f"Wrote {written}.")
    if outputs.get("degraded_mode") is True:
        sentences.append("The tool reported degraded_mode=true for this segmentation.")
    return sentences


def _detail_reconstruct_grappa(outputs: Dict[str, Any]) -> List[str]:
    """Narrate the k-space reconstruction from the numbers the tool really returned.

    Every figure here comes from ``node.outputs`` (which the executor copies straight
    out of the tool result), including the *uncomfortable* ones: a run where the input
    turned out to be fully sampled says the GRAPPA kernel was applied to 0 frames
    rather than letting the node title imply otherwise.
    """
    sentences: List[str] = []
    source = _basename(outputs.get("h5_path"))
    shape = [int(v) for v in (outputs.get("kspace_shape") or []) if isinstance(v, (int, float))]
    coils = outputs.get("n_coils")
    if source:
        detail = f" ({'x'.join(str(v) for v in shape)})" if shape else ""
        coil_clause = f" with {int(coils)} coils" if isinstance(coils, int) and coils else ""
        sentences.append(f"Read raw k-space from {source}{detail}{coil_clause}.")

    undersample = _ensure_dict(outputs.get("undersample"))
    if undersample.get("applied"):
        sentences.append(
            f"Retrospectively undersampled k-space by R={undersample.get('factor')} "
            f"keeping {undersample.get('ky_lines_kept')} of {undersample.get('ky_lines_total')} ky lines "
            f"({undersample.get('acs_lines')} of them a fully sampled ACS block)."
        )

    total = outputs.get("frames_total")
    applied = outputs.get("grappa_applied_frames")
    skipped = outputs.get("grappa_skipped_frames")
    failed = outputs.get("grappa_failed_frames")
    if isinstance(total, int) and isinstance(applied, int):
        clause = f"Applied the GRAPPA kernel to {applied} of {total} frames"
        if isinstance(skipped, int) and skipped:
            clause += f"; {skipped} frames were already fully sampled and were only coil-combined"
        sentences.append(_sentence(clause))
    if isinstance(failed, int) and failed:
        sentences.append(f"{failed} frames fell back to zero-filled reconstruction because pygrappa failed.")

    reconstructed = _text(outputs.get("reconstructed_nifti"))
    out_shape = [int(v) for v in (outputs.get("output_shape") or []) if isinstance(v, (int, float))]
    if reconstructed:
        shape_clause = f" ({'x'.join(str(v) for v in out_shape)})" if out_shape else ""
        sentences.append(f"Wrote {_basename(reconstructed)}{shape_clause}.")

    previews = _join_clauses(
        [
            f"snapshot {_basename(outputs.get('qa_snapshot_path'))}" if _text(outputs.get("qa_snapshot_path")) else None,
            f"before/after {_basename(outputs.get('comparison_png_path'))}" if _text(outputs.get("comparison_png_path")) else None,
        ]
    )
    if previews:
        sentences.append(f"Rendered {previews}.")
    for error in [_text(item) for item in (outputs.get("preview_errors") or []) if _text(item)]:
        sentences.append(_sentence(f"No preview image was produced: {_truncate(error, 200)}"))
    return sentences


def _detail_segment_cardiac_cine(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    case_results = [item for item in (outputs.get("case_results") or []) if isinstance(item, dict)]
    cine = _basename(outputs.get("cine_path"))
    phases = [_text(item.get("case_id")) for item in case_results if _text(item.get("case_id"))]
    if phases:
        sentences.append(
            f"Segmented {cine or 'the cine'} in {len(phases)} {_plural(len(phases), 'phase')}: {', '.join(phases)}."
        )
    elif cine:
        sentences.append(f"Segmented {cine}.")

    mask_keys = ("rv_mask_path", "myo_mask_path", "lv_mask_path")
    if case_results:
        label_volumes = sum(1 for item in case_results if _text(item.get("seg_path")))
        class_masks = sum(1 for item in case_results for key in mask_keys if _text(item.get(key)))
    else:
        label_volumes = 1 if _text(outputs.get("seg_path")) else 0
        class_masks = sum(1 for key in mask_keys if _text(outputs.get(key)))
    if label_volumes or class_masks:
        sentences.append(
            f"Wrote {label_volumes} label {_plural(label_volumes, 'volume')} "
            f"and {class_masks} class {_plural(class_masks, 'mask')}."
        )
    note = _text(outputs.get("note"))
    if note:
        sentences.append(_sentence(_truncate(note, 200)))

    snapshot = _text(outputs.get("qa_snapshot_path"))
    snapshot_error = _text(outputs.get("qa_snapshot_error"))
    if snapshot:
        sentences.append(f"QC overlay: {_basename(snapshot)}.")
    elif snapshot_error:
        sentences.append(_sentence(f"No QC overlay was produced: {_truncate(snapshot_error, 200)}"))
    return sentences


def _detail_classify_cardiac(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    predicted = _text(outputs.get("predicted_group"))
    truth = _text(outputs.get("ground_truth_group"))
    verdict = f"Classified the case as {predicted or 'UNKNOWN'}"
    if truth:
        verdict += f" (ground truth {truth}, {'match' if outputs.get('ground_truth_match') else 'mismatch'})"
    sentences.append(verdict + ".")

    metrics = _ensure_dict(outputs.get("metrics"))
    lv = _join_clauses(
        [
            _measure("EDV", metrics.get("lv_edv_ml"), "mL"),
            _measure("ESV", metrics.get("lv_esv_ml"), "mL"),
            _measure("EF", metrics.get("lv_ef_percent"), "%"),
        ]
    )
    rv = _join_clauses(
        [
            _measure("EDV", metrics.get("rv_edv_ml"), "mL"),
            _measure("ESV", metrics.get("rv_esv_ml"), "mL"),
            _measure("EF", metrics.get("rv_ef_percent"), "%"),
        ]
    )
    if lv:
        sentences.append(f"LV {lv}.")
    if rv:
        sentences.append(f"RV {rv}.")
    myo = _join_clauses(
        [
            _measure("myocardium at ED", metrics.get("myo_ed_volume_ml"), "mL"),
            _measure("LV mass", metrics.get("lv_mass_g"), "g"),
            _measure("max wall thickness", metrics.get("max_myo_thickness_mm"), "mm"),
        ]
    )
    if myo:
        sentences.append(myo[0].upper() + myo[1:] + ".")
    indexed = _join_clauses(
        [
            _measure("LV EDVi", metrics.get("lv_edvi_ml_m2"), "mL/m2"),
            _measure("RV EDVi", metrics.get("rv_edvi_ml_m2"), "mL/m2"),
            _measure("LV mass index", metrics.get("lv_mass_index_g_m2"), "g/m2"),
        ]
    )
    if indexed:
        sentences.append(indexed[0].upper() + indexed[1:] + ".")

    phases = _ensure_dict(outputs.get("phase_indices"))
    ed_frame = phases.get("ed_frame_1based")
    es_frame = phases.get("es_frame_1based")
    if _number(ed_frame) is not None and _number(es_frame) is not None:
        clause = f"ED frame {int(_number(ed_frame))}, ES frame {int(_number(es_frame))}"
        source = _text(phases.get("source"))
        if source:
            clause += f" (source: {source})"
        sentences.append(clause + ".")
    if outputs.get("needs_vlm_review"):
        sentences.append("The tool flagged this case for VLM-assisted review.")
    return sentences


def _detail_qa_snapshot(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    png = _basename(outputs.get("output_png"))
    source = _basename(outputs.get("input_nifti"))
    mask = _basename(outputs.get("mask_nifti"))
    if png:
        clause = f"Rendered QC snapshot {png}"
        if source:
            clause += f" from {source}"
        if mask:
            clause += f" with mask {mask}"
        sentences.append(clause + ".")
    frame = _number(outputs.get("selected_frame"))
    slice_index = _number(outputs.get("selected_slice"))
    picked = _join_clauses(
        [
            f"frame {int(frame)}" if frame is not None else None,
            f"slice {int(slice_index)}" if slice_index is not None else None,
        ]
    )
    if picked:
        sentences.append(f"Showing {picked}.")
    return sentences


def _detail_package_vlm_evidence(outputs: Dict[str, Any]) -> List[str]:
    sentences: List[str] = []
    bundle = _basename(outputs.get("vlm_evidence_path"))
    status = _text(outputs.get("status"))
    if bundle:
        clause = f"Packaged the VLM evidence bundle {bundle}"
        if status:
            clause += f" (status {status})"
        sentences.append(clause + ".")
    summary = _text(outputs.get("summary"))
    if summary:
        sentences.append(_sentence(_truncate(summary, 240)))
    return sentences


def _report_facts(report_json_path: str) -> List[str]:
    """Read back the report the node just wrote.  Silent when unreadable."""
    path = Path(_text(report_json_path))
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    sentences: List[str] = []
    sequences = [str(item) for item in (payload.get("sequences_present") or []) if str(item).strip()]
    if sequences:
        sentences.append(f"The report covers {len(sequences)} {_plural(len(sequences), 'sequence')}: {', '.join(sequences)}.")
    stage_status = _ensure_dict(payload.get("stage_status"))
    if stage_status:
        done = sorted(key for key, value in stage_status.items() if value is True)
        if done:
            sentences.append(f"Stages recorded as complete: {', '.join(done)}.")
    lesion_meta = _ensure_dict(payload.get("lesion_assessment_meta"))
    tier = _text(lesion_meta.get("evidence_tier"))
    if tier:
        sentences.append(f"Evidence tier: {tier}.")
    return sentences


def _detail_generate_report(outputs: Dict[str, Any]) -> List[str]:
    written = [
        _basename(outputs.get(key))
        for key in ("report_json_path", "clinical_report_path", "report_txt_path", "vlm_evidence_bundle_path")
        if _text(outputs.get(key))
    ]
    deduped: List[str] = []
    for name in written:
        if name and name not in deduped:
            deduped.append(name)
    sentences: List[str] = []
    if deduped:
        sentences.append(f"Wrote the report bundle: {', '.join(deduped)}.")
    sentences.extend(_report_facts(outputs.get("report_json_path")))
    return sentences


def _detail_generic_tool(outputs: Dict[str, Any]) -> List[str]:
    """Fallback for a tool with no bespoke narration: report real output paths."""
    paths: List[str] = []
    for key, value in outputs.items():
        if not str(key).endswith(("_path", "_png", "_dir")):
            continue
        raw = _text(value)
        if raw and Path(raw).exists():
            paths.append(_basename(raw))
    if not paths:
        return []
    return [f"Recorded {len(paths)} output {_plural(len(paths), 'path')}: {', '.join(paths)}."]


_DETAIL_BUILDERS = {
    "identify_sequences": _detail_identify_sequences,
    "register_to_reference": _detail_register,
    "segment_prostate": _detail_segment_prostate,
    "reconstruct_grappa": _detail_reconstruct_grappa,
    "segment_cardiac_cine": _detail_segment_cardiac_cine,
    "classify_cardiac_cine_disease": _detail_classify_cardiac,
    "generate_qa_snapshot": _detail_qa_snapshot,
    "package_vlm_evidence": _detail_package_vlm_evidence,
    "generate_report": _detail_generate_report,
}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeSummary:
    node_id: str
    status: str
    text: str
    source: str = "template"
    facts: Dict[str, Any] = field(default_factory=dict)


def build_node_summary(
    *,
    node: Any,
    artifacts: Sequence[Any] = (),
    message: str = "",
    unsatisfied_deps: Sequence[str] = (),
    status: str = "",
) -> NodeSummary:
    """Deterministic, artifact-grounded summary of one finished node."""
    node_id = _text(getattr(node, "node_id", "")) or "node"
    title = _text(getattr(node, "title", "")) or node_id
    node_status = _text(status) or _text(getattr(node, "status", ""))
    outputs = _ensure_dict(getattr(node, "outputs", None))
    tool = _text(getattr(node, "tool_name", "")) or _text(getattr(node, "action_type", ""))
    notes = _text(getattr(node, "notes", ""))
    label = f"{title} ({tool})" if tool and tool != title else title

    facts: Dict[str, Any] = {
        "node_id": node_id,
        "status": node_status,
        "tool": tool,
        "artifact_count": len(artifacts),
    }

    if node_status == "failed":
        error = _text(message) or notes or "no error message was recorded"
        sentences = [
            _sentence(f"{label} failed: {_condense_error(error)}"),
            _artifact_sentence(artifacts, failed=True),
        ]
        facts["error"] = error
        return NodeSummary(node_id=node_id, status=node_status, text=" ".join(sentences), facts=facts)

    if node_status == "blocked":
        blocked = [str(dep) for dep in unsatisfied_deps if _text(dep)]
        reason = _text(message) or "dependencies not satisfied"
        clause = _sentence(f"{label} did not start: {reason}")
        if blocked:
            clause += f" Waiting on {len(blocked)} upstream {_plural(len(blocked), 'node')}: {', '.join(blocked)}."
        facts["unsatisfied_deps"] = blocked
        return NodeSummary(node_id=node_id, status=node_status, text=clause, facts=facts)

    sentences: List[str] = []
    builder = _DETAIL_BUILDERS.get(tool)
    if builder is not None:
        sentences.extend(builder(outputs))
    elif outputs.get("tool_executed") is False:
        sentences.append(f"{title} completed. No tool was executed for this node.")
    else:
        sentences.extend(_detail_generic_tool(outputs))

    if not sentences:
        # Nothing quantitative to say: name what ran instead of padding.
        sentences.append(f"{label} completed and reported no quantitative output.")
    elif builder is not None:
        sentences.insert(0, f"{label}:")

    sentences.append(_artifact_sentence(artifacts))
    warning = _warning_sentence(outputs)
    if warning:
        sentences.append(_sentence(warning))
    profile = _profile_sentence(outputs)
    if profile:
        sentences.append(profile)

    text = " ".join(part for part in sentences if _text(part))
    facts["outputs"] = {key: value for key, value in outputs.items() if key != "warnings"}
    return NodeSummary(node_id=node_id, status=node_status, text=text, facts=facts)


# ---------------------------------------------------------------------------
# optional LLM rephrasing -- may reword, may never change a number
# ---------------------------------------------------------------------------


def narration_llm_enabled() -> bool:
    """Always False.  The LLM rephrasing path was removed deliberately.

    Node summaries are the only place the UI states clinical measurements, so
    they are generated from the node's real outputs and nothing else.

    An optional "let the model reword it" layer used to sit here, guarded by
    comparing the *set* of numeric tokens in the candidate against the
    template's.  That guard was unsound in two demonstrated ways: a set is
    blind to multiplicity and to which figure attaches to which label, so a
    candidate with LV EF and RV EF swapped (62.7 <-> 63.2) was accepted and
    would have published a clinically wrong measurement built entirely from
    real numbers; and a candidate that merely *added* an unsupported reading
    ("a normal study with no evidence of infarction") passed untouched because
    it contained no digits.

    Closing those holes properly needs semantic entailment, not token
    matching, and the deterministic template already reads well, so the
    rephrasing layer was deleted rather than patched.  Kept as a function so
    callers and tests fail loudly rather than silently re-enabling anything.
    """
    return False


