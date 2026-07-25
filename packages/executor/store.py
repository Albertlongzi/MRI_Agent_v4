from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from packages.schemas import (
    ActionEdge,
    ActionGraph,
    ActionNode,
    ArtifactRef,
    CaseState,
    ExecutionPatch,
    GraphEvent,
    MockSession,
    PatchOperation,
    create_chat_reply,
    create_mock_session,
)
from packages.tools import resolve_demo_case, resolve_tool_runtime_profile, run_v3_tool
from packages.tools.compiler_metadata import DEFAULT_TOOL_CONTRACTS

from .artifacts import ArtifactWriter, ArtifactWriteResult, make_node_artifact_dir
from .narration import NODE_SUMMARY_KIND, build_node_summary


def _utc_now():
    return datetime.now(timezone.utc)


def _model_copy(obj: Any) -> Any:
    copier = getattr(obj, "model_copy", None)
    if callable(copier):
        return copier(deep=True)
    return deepcopy(obj)


def _field_names(model_cls: Any) -> set[str]:
    fields = getattr(model_cls, "model_fields", None)
    if isinstance(fields, dict):
        return set(fields.keys())
    fields = getattr(model_cls, "__fields__", None)
    if isinstance(fields, dict):
        return set(fields.keys())
    return set()


def _build_model(model_cls: Any, **kwargs: Any) -> Any:
    names = _field_names(model_cls)
    if names:
        kwargs = {k: v for k, v in kwargs.items() if k in names}
    return model_cls(**kwargs)


def _node_map(graph: ActionGraph) -> Dict[str, ActionNode]:
    return {str(node.node_id): node for node in graph.nodes}


def _edge_map(graph: ActionGraph) -> Dict[str, ActionEdge]:
    return {str(edge.edge_id): edge for edge in graph.edges}


def _artifact_map(graph: ActionGraph) -> Dict[str, ArtifactRef]:
    return {str(artifact.artifact_id): artifact for artifact in graph.artifacts}


def _is_satisfied(graph: ActionGraph, node: ActionNode) -> bool:
    node_by_id = _node_map(graph)
    for dep in node.depends_on or []:
        dep_node = node_by_id.get(str(dep))
        if dep_node is None or str(dep_node.status) != "succeeded":
            return False
    return True


def _artifact_rel_path(*parts: str) -> str:
    clean = [str(part or "").strip().strip("/") for part in parts if str(part or "").strip()]
    return "/".join(clean)


def _join_lines(*items: str) -> str:
    return "\n".join(str(item).rstrip() for item in items if str(item).strip())


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_clinical_report_text(report_json: Dict[str, Any], clinical_text: str) -> str:
    lesion_meta = _ensure_dict(report_json.get("lesion_assessment_meta"))
    if lesion_meta.get("segmentation_usable") is not True:
        return clinical_text
    normalized = str(clinical_text or "")
    replacements = (
        (
            "Pipeline could not reliably assess lesions (missing ADC and/or segmentation issues).",
            "Pipeline could reliably assess lesions using the available ADC and segmentation evidence.",
        ),
        (
            "pipeline could not reliably assess lesions (missing adc and/or segmentation issues).",
            "pipeline could reliably assess lesions using the available adc and segmentation evidence.",
        ),
    )
    for old, new in replacements:
        normalized = normalized.replace(old, new)
    return normalized


def _clinical_report_has_contradiction(report_json: Dict[str, Any], clinical_text: str) -> bool:
    lesion_meta = _ensure_dict(report_json.get("lesion_assessment_meta"))
    if lesion_meta.get("segmentation_usable") is not True:
        return False
    lowered = str(clinical_text or "").lower()
    return any(
        pattern in lowered
        for pattern in (
            "pipeline could not reliably assess lesions",
            "missing adc and/or segmentation issues",
            "prostate mask unavailable/invalid",
            "segmentation unusable",
        )
    )


def _downstream_node_ids(graph: ActionGraph, start_node_id: str) -> List[str]:
    seen = {str(start_node_id)}
    ordered: List[str] = []
    queue = [str(start_node_id)]
    while queue:
        current = queue.pop(0)
        for node in graph.nodes:
            if str(node.node_id) in seen:
                continue
            if current not in [str(dep) for dep in node.depends_on or []]:
                continue
            seen.add(str(node.node_id))
            ordered.append(str(node.node_id))
            queue.append(str(node.node_id))
    return ordered


# ---------------------------------------------------------------------------
# Executor tool contract table.
#
# This is deliberately a STATIC table rather than something derived from the v3
# tool registry at import time: importing the BCER/v3 stack here would pull the
# heavy imaging dependencies into every process that touches the executor (and
# into the unit tests, which monkeypatch ``run_v3_tool`` precisely so they never
# need it).  Keep this table as the single obvious place where per-tool executor
# expectations live.
#
# ``required_output_paths`` is transcribed from the v3 ``ToolSpec.output_schema``
# ``required`` lists in the BCER repo (``tools/*.py``), restricted to the entries
# that are filesystem paths the executor can stat.  Where the v4 executor is
# deliberately stricter than the v3 schema (identify_sequences, segment_prostate,
# generate_report) the stricter v4 list is kept and flagged below.
#
# NOTE: ``packages/tools/compiler_metadata.py`` also carries ``produced_outputs``
# for all 11 tools, but two of its entries do not match the real v3 tools
# (``detect_lesion_candidates`` -> ``lesion_candidates_path`` and
# ``brats_mri_segmentation`` -> ``segmentation_path``); the real tools emit
# ``candidates_path`` and ``seg_path``/``tumor_subregions_path``.  compiler_metadata
# is therefore used only for tool identity / capability lookups here, not for
# contract validation.
# ---------------------------------------------------------------------------
_TOOL_EXECUTION_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "identify_sequences": {
        # v3 requires only ``mapping``; v4 additionally insists on the index files.
        "stage": "identify",
        "required_output_paths": ("series_inventory_path", "dicom_meta_path", "dicom_headers_index_path"),
    },
    "register_to_reference": {
        "stage": "register",
        "required_output_paths": ("resampled_path", "transform_path"),
    },
    "segment_prostate": {
        # v3 requires only ``prostate_mask_path``; v4 additionally insists on zone/input.
        "stage": "segment",
        "required_output_paths": ("prostate_mask_path", "zone_mask_path", "t2w_input_path"),
    },
    "detect_lesion_candidates": {
        # v3 ToolSpec.output_schema is empty for this tool; ``candidates_path`` is the
        # one path emitted by both the primary and the fallback code paths.
        "stage": "lesion",
        "required_output_paths": ("candidates_path",),
    },
    "extract_roi_features": {
        "stage": "extract",
        "required_output_paths": ("feature_table_path", "slice_summary_path"),
    },
    "package_vlm_evidence": {
        "stage": "vlm",
        "required_output_paths": ("vlm_evidence_path",),
    },
    "generate_report": {
        # v3 requires report_txt_path/report_json_path/vlm_evidence_bundle_path; v4
        # validates the two artifacts it actually renders and cross-checks.
        "stage": "report",
        "required_output_paths": ("report_json_path", "clinical_report_path"),
    },
    "brats_mri_segmentation": {
        "stage": "segment",
        "required_output_paths": ("seg_path", "tumor_subregions_path"),
    },
    "classify_brain_glioma_grade": {
        "stage": "classify",
        "required_output_paths": ("classification_path",),
    },
    "reconstruct_grappa": {
        # v3 ToolSpec.output_schema lists several keys but marks none required; the
        # reconstruction NIfTI is the only one the rest of the cardiac chain consumes,
        # and it is the only one the executor can stat.  ``zerofilled_nifti`` is
        # deliberately NOT required: it exists only when the caller asked for
        # retrospective undersampling.
        "stage": "reconstruct",
        "required_output_paths": ("reconstructed_nifti",),
    },
    "segment_cardiac_cine": {
        # v3 requires only ``seg_path``; v4 additionally insists on the three class
        # masks, because ``classify_cardiac_cine_disease`` and the cardiac report
        # path both consume RV/MYO/LV separately.  ``_split_cardiac_labels`` in
        # BCER_open/tools/cardiac_cine_segmentation.py always writes all three.
        "stage": "segment",
        "required_output_paths": ("seg_path", "rv_mask_path", "myo_mask_path", "lv_mask_path"),
    },
    "classify_cardiac_cine_disease": {
        "stage": "classify",
        "required_output_paths": ("classification_path",),
    },
    "generate_qa_snapshot": {
        # Not a compiler blueprint tool: the executor calls it itself so the
        # NIfTI-only cardiac path still puts a viewable PNG in the artifact rail.
        "stage": "qa",
        "required_output_paths": ("output_png",),
    },
    "compare_nifti_slices": {
        # Same deal as generate_qa_snapshot: called by the executor, not compiled.
        # The reconstruction node uses it for the zero-filled vs GRAPPA before/after.
        "stage": "qa",
        "required_output_paths": ("output_png",),
    },
}

_TOOL_STAGE_MAP: Dict[str, str] = {
    tool_name: str(contract.get("stage") or "misc") for tool_name, contract in _TOOL_EXECUTION_CONTRACTS.items()
}

_TOOL_REQUIRED_OUTPUT_PATHS: Dict[str, Tuple[str, ...]] = {
    tool_name: tuple(contract.get("required_output_paths") or ())
    for tool_name, contract in _TOOL_EXECUTION_CONTRACTS.items()
}

# Every name the planner/compiler can legitimately put on a node and call a "tool".
# Derived from the compiler metadata so a new blueprint cannot silently bypass the
# "did the executor actually run something?" guard.
_KNOWN_TOOL_NAMES: frozenset = frozenset(_TOOL_EXECUTION_CONTRACTS) | frozenset(DEFAULT_TOOL_CONTRACTS)

# Tools that produce a segmentation, derived from compiler metadata capabilities
# rather than hard-coding ``segment_prostate``.
_SEGMENTATION_TOOL_NAMES: frozenset = frozenset(
    tool_name
    for tool_name, contract in DEFAULT_TOOL_CONTRACTS.items()
    if "segment" in [str(cap) for cap in (contract.get("capabilities") or [])]
)

# Segmentation tools whose mask the report's ``lesion_assessment_meta`` block is
# actually *about*.  That block is prostate-lesion specific -- a real cardiac run
# emits ``segmentation_usable: false`` alongside
# ``lesion_geometry_note: "missing_lesion_or_prostate_mask"`` -- so cross-checking it
# against *any* succeeded segmentation node (brain tumour, cardiac cine) turns a
# perfectly correct cardiac report into a failed node.  Still derived from compiler
# metadata, and still keyed on the tool rather than the literal node id.
_LESION_SEGMENTATION_TOOL_NAMES: frozenset = frozenset(
    tool_name
    for tool_name, contract in DEFAULT_TOOL_CONTRACTS.items()
    if "prostate_segmentation" in [str(cap) for cap in (contract.get("capabilities") or [])]
)


def _node_tool_identity(node: ActionNode) -> str:
    """Return the tool this node claims to run, or "" if it is not a tool node.

    A node counts as a tool node when it carries an explicit ``tool_name`` or when
    its free-form ``action_type`` matches a name the compiler/registry knows about.
    Presentational nodes (``read_case``, ``review_checkpoint``, ...) return "".
    """
    tool_name = str(getattr(node, "tool_name", "") or "").strip()
    if tool_name:
        return tool_name
    action_type = str(getattr(node, "action_type", "") or "").strip()
    if action_type in _KNOWN_TOOL_NAMES:
        return action_type
    return ""


def _runtime_profile_label(tool_name: str) -> str:
    try:
        return str(resolve_tool_runtime_profile(tool_name).get("profile_id") or "control-plane")
    except Exception:
        return "control-plane"


# ---------------------------------------------------------------------------
# generate_qa_snapshot runtime profile.
#
# ``configs/tool_runtime_profiles.json`` carries no ``tool_profiles`` entry for
# generate_qa_snapshot, so ``resolve_tool_runtime_profile`` falls back to
# ``control-plane`` -- the API virtualenv, which deliberately has no nibabel /
# matplotlib.  Rather than silently skipping the only human-viewable artifact the
# cardiac path produces, the executor pins a profile that does have the imaging
# stack.  Precedence:
#   1. an explicit ``tool_profiles`` entry in configs (config owner wins);
#   2. ``MRI_AGENT_V4_QA_SNAPSHOT_PROFILE``;
#   3. ``_QA_SNAPSHOT_DEFAULT_PROFILE`` below.
# Delete this whole shim once configs maps the tool.
# ---------------------------------------------------------------------------
_QA_SNAPSHOT_PROFILE_ENV = "MRI_AGENT_V4_QA_SNAPSHOT_PROFILE"
_QA_SNAPSHOT_DEFAULT_PROFILE = "legacy-qwen-vllm"


def _qa_snapshot_profile_override() -> Optional[str]:
    try:
        from packages.tools.runtime_profiles import load_runtime_profile_catalog

        if "generate_qa_snapshot" in (load_runtime_profile_catalog().get("tool_profiles") or {}):
            return None
    except Exception:
        pass
    return str(os.environ.get(_QA_SNAPSHOT_PROFILE_ENV) or "").strip() or _QA_SNAPSHOT_DEFAULT_PROFILE


# Filename markers that identify a NIfTI as a *label* volume rather than an image
# volume.  The shipped cardiac demo case ships ``patient061_frame01_gt.nii.gz``
# next to the cine, and ``segment_cardiac_cine`` happily segments every NIfTI in a
# directory it is handed -- which would push the ground truth through nnUNet as if
# it were anatomy.
_LABEL_FILENAME_MARKERS: Tuple[str, ...] = (
    "_gt",
    "_seg",
    "_label",
    "_labels",
    "_mask",
    "_masks",
    "_pred",
    "_groundtruth",
)


def _nifti_stem(path: Path) -> str:
    name = path.name
    for suffix in (".nii.gz", ".nii"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _is_label_nifti(path: Path) -> bool:
    stem = _nifti_stem(path).lower()
    return any(stem.endswith(marker) or f"{marker}_" in stem for marker in _LABEL_FILENAME_MARKERS)


_KSPACE_SUFFIXES: Tuple[str, ...] = (".h5", ".hdf5")

# Optional sidecar next to a raw k-space file (or in the case root) carrying the
# acquisition geometry the HDF5 itself does not: CMRxRecon's processed H5 files have
# no pixel-spacing attribute, and reconstructing them as 1x1x1 mm would hand nnUNet a
# volume whose physical heart is ~half its real size.  Only the keys listed in
# ``_RECON_FORWARDED_ARG_KEYS`` are read, and every value that is used is echoed back
# in ``node.outputs["recon_param_sources"]`` so nothing arrives at the tool unattributed.
_RECON_PARAMS_FILENAME = "recon_params.json"

_RECON_FORWARDED_ARG_KEYS: Tuple[str, ...] = (
    "kspace_key",
    "image_key",
    "calib_key",
    "acs_lines",
    "kernel_size",
    "coil_axis",
    "nonspatial_order",
    "pixel_spacing",
    "undersample_factor",
    # Readout-oversampling crop. Omitting these silently dropped them, so the
    # reconstruction kept its uncropped ~800 mm field of view while the tool
    # reported success -- the heart then occupied a small fraction of the frame
    # and the segmentation under-covered the ventricle.
    "readout_fov_mm",
    "readout_oversampling",
    "readout_crop_samples",
    "readout_axis",
)


def _kspace_files_in(directory: Path) -> List[Path]:
    return sorted(
        item
        for item in directory.iterdir()
        if item.is_file() and item.suffix.lower() in _KSPACE_SUFFIXES
    )


def _nifti_files_in(directory: Path) -> List[Path]:
    return sorted(
        item
        for item in directory.iterdir()
        if item.is_file() and (item.name.lower().endswith(".nii.gz") or item.name.lower().endswith(".nii"))
    )


@dataclass(frozen=True)
class ExecutionOutcome:
    node_id: str
    status: str
    message: str
    artifact_ids: List[str]
    event_ids: List[str]


class ContractValidationError(RuntimeError):
    pass


class MissingExecutorHandlerError(RuntimeError):
    """Raised when a node names a real tool that the executor cannot actually run.

    Previously such nodes fell through to ``_exec_generic_tool``, which wrote a
    placeholder JSON/TXT/SVG bundle and reported ``succeeded`` without calling any
    tool and without contract validation.  A brain or cardiac graph therefore
    rendered "N/N done, all green" having executed nothing.  Failing loudly here is
    the only honest answer until the missing handlers exist.
    """


class MockExecutorStore:
    def __init__(
        self,
        *,
        session_factory=create_mock_session,
        root_dir: Optional[Path] = None,
        initial_session: Optional[MockSession] = None,
    ) -> None:
        self._lock = False
        self._session_factory = session_factory
        self._root_dir = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[2]
        self._artifact_root = self._root_dir / "artifacts"
        self._runtime_root = self._root_dir / "runtime"
        self._writer = ArtifactWriter(self._artifact_root)
        self._initial_session = _model_copy(initial_session) if initial_session is not None else _model_copy(self._session_factory())
        self._session = _model_copy(self._initial_session)
        self._step_count = 0
        self._proposal_count = 0
        self._artifact_count = 0
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._recount_internal_state()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def artifact_root(self) -> Path:
        return self._artifact_root

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root

    def load_session(
        self,
        session: MockSession,
        *,
        initial_session: Optional[MockSession] = None,
    ) -> MockSession:
        self._initial_session = _model_copy(initial_session) if initial_session is not None else _model_copy(session)
        self._session = _model_copy(session)
        self._recount_internal_state()
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        return self.snapshot_session()

    def _runtime_run_dir(self) -> Path:
        return self._runtime_root / self._session.graph.graph_id

    def _runtime_case_state_path(self) -> Path:
        return self._runtime_run_dir() / "case_state.json"

    def _runtime_artifacts_dir(self) -> Path:
        return self._runtime_run_dir() / "artifacts"

    def _ensure_runtime_workspace(self) -> tuple[Path, Path, Path]:
        run_dir = self._runtime_run_dir()
        case_state_path = self._runtime_case_state_path()
        artifacts_dir = self._runtime_artifacts_dir()
        target = (self._artifact_root / self._session.graph.graph_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        if artifacts_dir.exists() or artifacts_dir.is_symlink():
            if artifacts_dir.is_symlink():
                current = artifacts_dir.resolve()
                if current != target:
                    artifacts_dir.unlink()
                    artifacts_dir.symlink_to(target, target_is_directory=True)
            elif artifacts_dir.is_dir():
                # Keep existing directory content, but do not replace it destructively.
                pass
        else:
            artifacts_dir.symlink_to(target, target_is_directory=True)
        if not case_state_path.exists():
            case_state_path.write_text(
                json.dumps(
                    {
                        "case_id": self._session.case_state.case_id,
                        "run_id": self._session.graph.graph_id,
                        "created_at": _utc_now().isoformat(),
                        "metadata": {
                            "domain": self._session.case_state.domain,
                            "input_root": self._session.case_state.input_root,
                        },
                        "stage_outputs": {},
                        "stage_meta": {},
                        "artifacts_index": [],
                        "summary": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return run_dir, artifacts_dir, case_state_path

    def _load_runtime_case_state(self) -> Dict[str, Any]:
        _, _, case_state_path = self._ensure_runtime_workspace()
        try:
            return json.loads(case_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "case_id": self._session.case_state.case_id,
                "run_id": self._session.graph.graph_id,
                "created_at": _utc_now().isoformat(),
                "metadata": {
                    "domain": self._session.case_state.domain,
                    "input_root": self._session.case_state.input_root,
                },
                "stage_outputs": {},
                "stage_meta": {},
                "artifacts_index": [],
                "summary": {},
            }

    def _save_runtime_case_state(self, payload: Dict[str, Any]) -> None:
        _, _, case_state_path = self._ensure_runtime_workspace()
        case_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _artifact_dicts_from_v4_refs(self, artifacts: Sequence[ArtifactRef]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for artifact in artifacts:
            abs_path = self._root_dir / str(artifact.uri).lstrip("/")
            out.append(
                {
                    "path": str(abs_path.resolve()),
                    "kind": str(artifact.kind),
                    "description": str(artifact.name or ""),
                    "media_type": artifact.mime_type,
                }
            )
        return out

    def _resolve_output_path(self, raw_path: Any) -> Optional[Path]:
        text = str(raw_path or "").strip()
        if not text or text.startswith("http://") or text.startswith("https://"):
            return None
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            return candidate
        rel = text.lstrip("/")
        candidates = [
            (self._root_dir / rel).resolve(),
            (self._runtime_run_dir() / rel).resolve(),
            (self._artifact_root / rel).resolve(),
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _resolve_sequence_reference(self, raw_value: Any, *, fallback_modality: Optional[str] = None) -> str:
        text = str(raw_value or "").strip()
        mapping = self._session.case_state.sequence_index or {}
        if text.startswith("@seq."):
            resolved = mapping.get(text.split(".", 1)[1])
            if resolved:
                return str(resolved)
        if text in mapping:
            return str(mapping[text])
        if fallback_modality and mapping.get(fallback_modality):
            return str(mapping[fallback_modality])
        return text

    def _artifact_kind_for_path(self, raw_kind: Any, artifact_path: Path) -> str:
        kind = str(raw_kind or "").strip().lower()
        full_name = artifact_path.name.lower()
        if kind == "nifti" or full_name.endswith(".nii") or full_name.endswith(".nii.gz"):
            return "nifti"
        if kind == "json" or full_name.endswith(".json"):
            return "json"
        if kind == "csv" or full_name.endswith(".csv"):
            return "csv"
        if kind in {"png", "figure"} or full_name.endswith(".png"):
            return "png"
        if kind == "svg" or full_name.endswith(".svg"):
            return "svg"
        if kind in {"md", "markdown", "report"} or full_name.endswith(".md"):
            return "report"
        if kind in {"mask", "overlay", "table", "text", "txt", "log"}:
            return "text" if kind in {"text", "txt"} else kind
        if full_name.endswith(".tfm") or full_name.endswith(".log") or full_name.endswith(".txt"):
            return "log"
        return "text"

    def _artifact_role_for_path(self, artifact_kind: str) -> str:
        if artifact_kind in {"png", "svg", "overlay"}:
            return "preview"
        if artifact_kind in {"log", "text"}:
            return "evidence"
        return "output"

    def _artifact_refs_from_generated(
        self,
        *,
        node: ActionNode,
        tool_name: str,
        generated_artifacts: Sequence[Dict[str, Any]],
    ) -> List[ArtifactRef]:
        artifacts: List[ArtifactRef] = []
        for generated in generated_artifacts:
            artifact_path = Path(str(generated.get("path") or "")).expanduser().resolve()
            if not artifact_path.exists():
                continue
            try:
                uri = artifact_path.relative_to(self._root_dir).as_posix()
            except Exception:
                uri = str(artifact_path)
            kind = self._artifact_kind_for_path(generated.get("kind"), artifact_path)
            role = self._artifact_role_for_path(kind)
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=str(generated.get("description") or artifact_path.name or tool_name),
                    kind=kind,  # type: ignore[arg-type]
                    role=role,  # type: ignore[arg-type]
                    uri=uri,
                    mime_type=generated.get("media_type"),
                    metadata={"source": "v3_tool", "tool_name": tool_name},
                )
            )
        return artifacts

    def _validate_tool_result_contract(self, tool_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        required_keys = list(_TOOL_REQUIRED_OUTPUT_PATHS.get(str(tool_name), ()))
        resolved_paths: Dict[str, str] = {}
        missing_paths: List[Dict[str, str]] = []
        for key in required_keys:
            raw_path = data.get(key)
            resolved = self._resolve_output_path(raw_path)
            if resolved is None or not resolved.exists():
                missing_paths.append({"key": key, "path": "" if resolved is None else str(resolved)})
                continue
            resolved_paths[key] = str(resolved)

        validation: Dict[str, Any] = {
            "tool_name": str(tool_name),
            "required_output_paths": required_keys,
            "resolved_output_paths": resolved_paths,
            "missing_output_paths": missing_paths,
            "consumable": not missing_paths,
        }

        if str(tool_name) == "generate_report" and validation["consumable"]:
            report_path = Path(resolved_paths["report_json_path"])
            try:
                report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ContractValidationError(f"report_json_path is unreadable: {report_path} ({exc})") from exc
            lesion_meta = _ensure_dict(report_payload.get("lesion_assessment_meta"))
            succeeded_segmentation_nodes = self._succeeded_segmentation_node_ids(_LESION_SEGMENTATION_TOOL_NAMES)
            if succeeded_segmentation_nodes:
                seg_usable = lesion_meta.get("segmentation_usable")
                validation["report_segmentation_usable"] = seg_usable
                validation["succeeded_segmentation_nodes"] = succeeded_segmentation_nodes
                if seg_usable is False:
                    joined = ", ".join(succeeded_segmentation_nodes)
                    raise ContractValidationError(
                        "generate_report produced downstream contradiction: "
                        f"{joined} succeeded but report says segmentation_usable=false"
                    )

        if missing_paths:
            details = ", ".join(f"{item['key']} -> {item['path'] or '<missing>'}" for item in missing_paths)
            raise ContractValidationError(f"{tool_name} missing required output paths: {details}")
        return validation

    def _succeeded_segmentation_node_ids(self, tool_names: Optional[Iterable[str]] = None) -> List[str]:
        """Node ids of succeeded segmentation nodes, keyed on tool rather than node id.

        Replaces a hard-coded lookup of the literal node id ``segment_prostate``; the
        tool set is derived from compiler metadata capabilities, so a graph is matched
        however the compiler happened to name its nodes.  ``tool_names`` narrows the
        set -- callers that only care about the mask a particular report section
        describes pass e.g. ``_LESION_SEGMENTATION_TOOL_NAMES``.
        """
        wanted = frozenset(str(name) for name in tool_names) if tool_names is not None else _SEGMENTATION_TOOL_NAMES
        out: List[str] = []
        for node in self._session.graph.nodes:
            if str(node.status) != "succeeded":
                continue
            if _node_tool_identity(node) in wanted:
                out.append(str(node.node_id))
        return out

    def _record_runtime_tool_result(
        self,
        *,
        tool_name: str,
        ok: bool,
        data: Dict[str, Any],
        generated_artifacts: Sequence[Dict[str, Any]],
        consumable: Optional[bool] = None,
        validation: Optional[Dict[str, Any]] = None,
        attempt_id: Optional[str] = None,
        rerun_from: Optional[str] = None,
        supersedes: Optional[str] = None,
    ) -> None:
        state = self._load_runtime_case_state()
        stage = _TOOL_STAGE_MAP.get(str(tool_name), "misc")
        stage_outputs = state.setdefault("stage_outputs", {})
        stage_meta = state.setdefault("stage_meta", {})
        stage_tools = stage_outputs.setdefault(stage, {})
        records = stage_tools.setdefault(str(tool_name), [])
        record = {
            "call_id": f"{tool_name}-{len(records) + 1:03d}",
            "ok": bool(ok),
            "consumable": bool(ok) if consumable is None else bool(consumable),
            "attempt_id": attempt_id,
            "rerun_from": rerun_from,
            "supersedes": supersedes,
            "data": dict(data or {}),
            "validation": dict(validation or {}),
            "stage_order": len(stage_outputs),
        }
        records.append(record)
        stage_meta.setdefault(stage, len(stage_outputs))

        artifacts_index = state.setdefault("artifacts_index", [])
        known = {str(item.get("path") or "") for item in artifacts_index if isinstance(item, dict)}
        for artifact in generated_artifacts:
            path = str(artifact.get("path") or "").strip()
            if not path or path in known:
                continue
            artifacts_index.append(
                {
                    "path": path,
                    "kind": str(artifact.get("kind") or "unknown"),
                    "description": str(artifact.get("description") or tool_name),
                    "media_type": artifact.get("media_type"),
                }
            )
            known.add(path)
        self._save_runtime_case_state(state)

    def _recount_internal_state(self) -> None:
        graph = self._session.graph
        executed_statuses = {"running", "succeeded", "failed", "skipped", "patched"}
        self._step_count = sum(1 for node in graph.nodes if str(node.status) in executed_statuses)
        self._proposal_count = len(graph.proposals) + len(graph.patch_history)
        self._artifact_count = len(graph.artifacts)

    def _latest_attempt_id(self, node: ActionNode) -> Optional[str]:
        if node.attempt_history:
            latest = node.attempt_history[-1]
            if isinstance(latest, dict):
                value = latest.get("attempt_id")
                if value:
                    return str(value)
        return str(node.current_attempt_id) if node.current_attempt_id else None

    def _begin_attempt(self, node: ActionNode) -> str:
        node.attempt_count = int(node.attempt_count or 0) + 1
        attempt_id = f"{node.node_id}-attempt-{node.attempt_count:03d}"
        node.current_attempt_id = attempt_id
        record = {
            "attempt_id": attempt_id,
            "status": "running",
            "started_at": _utc_now().isoformat(),
            "finished_at": None,
            "rerun_from": node.rerun_from,
            "supersedes": node.supersedes,
            "artifact_ids": [],
            "event_ids": [],
            "message": None,
            "error": None,
            "output_snapshot": {},
        }
        node.attempt_history.append(record)
        return attempt_id

    def _update_attempt(
        self,
        node: ActionNode,
        *,
        status: str,
        event_ids: Optional[Sequence[str]] = None,
        artifact_ids: Optional[Sequence[str]] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        if not node.attempt_history:
            return
        record = node.attempt_history[-1]
        record["status"] = str(status)
        if event_ids is not None:
            record["event_ids"] = [str(item) for item in event_ids]
        if artifact_ids is not None:
            record["artifact_ids"] = [str(item) for item in artifact_ids]
        if message is not None:
            record["message"] = str(message)
        if error is not None:
            record["error"] = str(error)
        record["output_snapshot"] = _ensure_dict(_model_copy(node.outputs))
        if str(status) != "running":
            record["finished_at"] = _utc_now().isoformat()

    def _invalidate_nodes(
        self,
        graph: ActionGraph,
        *,
        start_node_id: str,
        cause: str,
        reason: str,
        mark_target_patched: bool,
    ) -> List[str]:
        affected = [str(start_node_id), *_downstream_node_ids(graph, str(start_node_id))]
        affected_set = set(affected)
        stale_artifact_ids: set[str] = set()
        for node in graph.nodes:
            node_id = str(node.node_id)
            if node_id not in affected_set:
                continue
            stale_artifact_ids.update(str(item) for item in node.artifact_refs)
            node.outputs = {}
            node.artifact_refs = []
            node.rerun_from = str(start_node_id)
            node.supersedes = self._latest_attempt_id(node)
            node.notes = f"{reason} ({cause})" if not node.notes else f"{node.notes} | {reason} ({cause})"
            if node_id == str(start_node_id):
                node.status = "patched" if mark_target_patched else ("ready" if _is_satisfied(graph, node) else "planned")
            else:
                node.status = "planned"
        self._session.case_state.selected_artifacts = [
            artifact_id
            for artifact_id in self._session.case_state.selected_artifacts
            if str(artifact_id) not in stale_artifact_ids
        ]
        self._session.case_state.last_error = None
        return affected

    def _patch_impacted_nodes(self, operations: Sequence[PatchOperation]) -> List[str]:
        impacted: List[str] = []
        for operation in operations:
            op = str(operation.op or "").strip()
            value = dict(operation.value or {})
            if op in {"update_node", "reroute_dependency"}:
                node_id = str(operation.target or value.get("node_id") or "").strip()
                if node_id:
                    impacted.append(node_id)
            elif op == "insert_checkpoint":
                node_id = str(operation.target or value.get("target_node") or "").strip()
                if node_id:
                    impacted.append(node_id)
        return impacted

    def snapshot_session(self) -> MockSession:
        return _model_copy(self._session)

    def snapshot_graph(self) -> ActionGraph:
        return _model_copy(self._session.graph)

    def snapshot_events(self) -> List[GraphEvent]:
        return [_model_copy(event) for event in self._session.graph.events]

    def post_chat(
        self,
        message: str,
        *,
        assistant_reply: Optional[Dict[str, str]] = None,
        reply_source: str = "mock",
        reply_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            raise ValueError("message must not be empty")
        self._session.chat_history.append({"role": "user", "content": text})
        reply = assistant_reply if assistant_reply is not None else create_chat_reply(text, self._session.graph)
        self._session.chat_history.append(reply)
        event = self._append_event(
            actor_type="human",
            actor_id="demo-user",
            event_type="chat_message",
            target_id=self._session.graph.graph_id,
            payload={
                "message": text,
                "reply": reply.get("content", ""),
                "reply_source": reply_source,
                "reply_metadata": dict(reply_metadata or {}),
            },
        )
        return {
            "reply": reply,
            "chat_history": list(self._session.chat_history),
            "event": event.model_dump(mode="json"),
            "graph": self.snapshot_graph().model_dump(mode="json"),
            "session": self.snapshot_session().model_dump(mode="json"),
        }

    # ------------------------------------------------------------------
    # Node narration
    #
    # After a node reaches a terminal state the chat panel used to say nothing
    # -- it only ever restated the plan -- so this posts one assistant message
    # per finished node describing what that node actually did, built from the
    # node's real outputs and artifacts (see ``packages/executor/narration.py``).
    # ------------------------------------------------------------------

    def _node_summary_key(self, node: ActionNode, outcome: ExecutionOutcome) -> str:
        """Stable identity for "this node, this attempt, this outcome".

        Written into the chat message itself so the guard survives a reload:
        ``chat_history`` is persisted, the store object is not.
        """
        attempt = str(node.current_attempt_id or "").strip()
        if not attempt:
            # ``blocked`` never opens an attempt; the blocking event id is unique.
            attempt = str(outcome.event_ids[-1]) if outcome.event_ids else "no-attempt"
        return f"{node.node_id}#{attempt}#{outcome.status}"

    def _unsatisfied_dependencies(self, node: ActionNode) -> List[str]:
        node_by_id = _node_map(self._session.graph)
        out: List[str] = []
        for dep in node.depends_on or []:
            dep_node = node_by_id.get(str(dep))
            if dep_node is None or str(dep_node.status) != "succeeded":
                out.append(str(dep))
        return out

    def _node_artifacts(self, node: ActionNode) -> List[ArtifactRef]:
        wanted = {str(item) for item in node.artifact_refs}
        return [artifact for artifact in self._session.graph.artifacts if str(artifact.artifact_id) in wanted]

    def _append_node_summary(self, node: ActionNode, outcome: ExecutionOutcome) -> Optional[Dict[str, str]]:
        """Append one assistant chat message for a node that just finished.

        Returns the message, or ``None`` when the node did not reach a terminal
        state or a summary for this exact attempt is already in ``chat_history``
        (so repeated status polls, replays and reloads cannot duplicate it).
        """
        status = str(outcome.status or node.status or "")
        if status not in {"succeeded", "failed", "blocked"}:
            return None
        summary_key = self._node_summary_key(node, outcome)
        for entry in self._session.chat_history:
            if isinstance(entry, dict) and str(entry.get("summary_key") or "") == summary_key:
                return None

        summary = build_node_summary(
            node=node,
            artifacts=self._node_artifacts(node),
            message=str(outcome.message or ""),
            unsatisfied_deps=self._unsatisfied_dependencies(node) if status == "blocked" else (),
            status=status,
        )

        message: Dict[str, str] = {
            "role": "assistant",
            "content": summary.text,
            "kind": NODE_SUMMARY_KIND,
            "node_id": str(node.node_id),
            "node_status": status,
            "attempt_id": str(node.current_attempt_id or ""),
            "summary_key": summary_key,
            "source": summary.source,
        }
        self._session.chat_history.append(message)
        self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="node_summary_posted",
            target_id=str(node.node_id),
            payload={
                "node_id": str(node.node_id),
                "status": status,
                "attempt_id": str(node.current_attempt_id or ""),
                "source": summary.source,
                "content": summary.text,
            },
        )
        return message

    def reset(self, *, purge_artifacts: bool = True) -> MockSession:
        if purge_artifacts and self._artifact_root.exists():
            shutil.rmtree(self._artifact_root, ignore_errors=True)
        if purge_artifacts and self._runtime_root.exists():
            shutil.rmtree(self._runtime_root, ignore_errors=True)
        self._session = _model_copy(self._initial_session)
        self._step_count = 0
        self._proposal_count = 0
        self._artifact_count = 0
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._append_event(
            actor_type="system",
            actor_id="executor",
            event_type="graph_reset",
            target_id=self._session.graph.graph_id,
            payload={"purge_artifacts": bool(purge_artifacts)},
        )
        return self.snapshot_session()

    def preview_patch(self, *, reason: str, author_id: str = "supervisor") -> ExecutionPatch:
        graph = self._session.graph
        patch = ExecutionPatch(
            patch_id=f"patch-{self._proposal_count + 1:03d}",
            graph_id=graph.graph_id,
            author_type="supervisor",
            author_id=author_id,
            reason=reason,
            applies_to_version=graph.version,
            result="preview",
            operations=[
                PatchOperation(
                    op="insert_checkpoint",
                    target="segment_prostate",
                    value={
                        "after_node": "register_adc",
                        "title": "Review Registration Output",
                        "kind": "review",
                    },
                )
            ],
        )
        self._proposal_count += 1
        graph.proposals.append(patch)
        self._append_event(
            actor_type="supervisor",
            actor_id=author_id,
            event_type="patch_previewed",
            target_id=patch.patch_id,
            payload={"reason": reason, "operations": len(patch.operations)},
        )
        return patch

    def apply_latest_proposal(self) -> Dict[str, Any]:
        graph = self._session.graph
        if not graph.proposals:
            return {"applied": False, "reason": "no proposals available", "graph": self.snapshot_graph().model_dump(mode="json")}
        patch = graph.proposals[-1]
        return self.apply_patch(patch)

    def apply_patch(self, patch: ExecutionPatch | Dict[str, Any]) -> Dict[str, Any]:
        graph = self._session.graph
        patch_model = self._coerce_patch(patch)
        if int(patch_model.applies_to_version or 0) != int(graph.version or 0):
            raise ValueError(f"patch version mismatch: {patch_model.applies_to_version} != {graph.version}")
        self._apply_patch_operations(graph, patch_model.operations)
        impacted_nodes = self._patch_impacted_nodes(patch_model.operations)
        affected_nodes: List[str] = []
        for node_id in impacted_nodes:
            for affected in self._invalidate_nodes(
                graph,
                start_node_id=node_id,
                cause="patch",
                reason=patch_model.reason,
                mark_target_patched=True,
            ):
                if affected not in affected_nodes:
                    affected_nodes.append(affected)
        patch_model.result = "applied"
        graph.patch_history.append(patch_model)
        graph.proposals = [proposal for proposal in graph.proposals if proposal.patch_id != patch_model.patch_id]
        graph.version += 1
        self._sync_graph_status()
        self._recompute_session_state()
        self._append_event(
            actor_type=patch_model.author_type,
            actor_id=patch_model.author_id,
            event_type="patch_applied",
            target_id=patch_model.patch_id,
            payload={"reason": patch_model.reason, "result": patch_model.result, "affected_nodes": affected_nodes},
        )
        return {
            "applied": True,
            "patch": patch_model.model_dump(mode="json"),
            "affected_nodes": affected_nodes,
            "graph": self.snapshot_graph().model_dump(mode="json"),
            "session": self.snapshot_session().model_dump(mode="json"),
        }

    def rerun_from_node(self, node_id: str, *, reason: str = "operator rerun", actor_id: str = "operator") -> Dict[str, Any]:
        graph = self._session.graph
        target = self._find_node(graph, node_id)
        affected_nodes = self._invalidate_nodes(
            graph,
            start_node_id=target.node_id,
            cause="rerun",
            reason=reason,
            mark_target_patched=False,
        )
        graph.version += 1
        self._recompute_session_state()
        event = self._append_event(
            actor_type="human",
            actor_id=actor_id,
            event_type="rerun_requested",
            target_id=target.node_id,
            payload={"reason": reason, "affected_nodes": affected_nodes},
        )
        self._sync_graph_status()
        return {
            "rerun": True,
            "node_id": target.node_id,
            "reason": reason,
            "affected_nodes": affected_nodes,
            "event": event.model_dump(mode="json"),
            "graph": self.snapshot_graph().model_dump(mode="json"),
            "session": self.snapshot_session().model_dump(mode="json"),
        }

    def execute_next(self) -> Dict[str, Any]:
        graph = self._session.graph
        node = self._select_next_node()
        if node is None:
            self._sync_graph_status()
            return {
                "executed": False,
                "reason": "no runnable node",
                "graph": self.snapshot_graph().model_dump(mode="json"),
                "session": self.snapshot_session().model_dump(mode="json"),
            }
        if str(node.status) == "running":
            outcome = self._finish_running_node(node)
        else:
            outcome = self._run_node(node)
        self._sync_graph_status()
        self._recompute_session_state()
        # One chat summary per finished node, from that node's real outputs.
        # Narration is commentary: it must never cost the caller a real result
        # (this method's return value is what gets persisted), but a narration
        # bug must not vanish either -- hence the event rather than a swallow.
        try:
            self._append_node_summary(node, outcome)
        except Exception as exc:  # noqa: BLE001
            self._append_event(
                actor_type="executor",
                actor_id="cerebellum",
                event_type="node_summary_failed",
                target_id=str(node.node_id),
                payload={
                    "node_id": str(node.node_id),
                    "status": str(outcome.status),
                    "error": f"{type(exc).__name__}: {exc}".strip(),
                },
            )
        return {
            "executed": True,
            "node_id": outcome.node_id,
            "status": outcome.status,
            "message": outcome.message,
            "artifact_ids": outcome.artifact_ids,
            "event_ids": outcome.event_ids,
            "graph": self.snapshot_graph().model_dump(mode="json"),
            "session": self.snapshot_session().model_dump(mode="json"),
        }

    def execute_until_done(self, max_steps: int = 20) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        for _ in range(max(1, int(max_steps))):
            result = self.execute_next()
            steps.append(result)
            if not result.get("executed"):
                break
            if str(self._session.graph.status) in {"completed", "failed"}:
                break
        return {
            "steps": steps,
            "graph": self.snapshot_graph().model_dump(mode="json"),
            "session": self.snapshot_session().model_dump(mode="json"),
        }

    def _coerce_patch(self, patch: ExecutionPatch | Dict[str, Any]) -> ExecutionPatch:
        if isinstance(patch, ExecutionPatch):
            return _model_copy(patch)
        return _build_model(ExecutionPatch, **patch)

    def _append_event(
        self,
        *,
        actor_type: str,
        actor_id: str,
        event_type: str,
        target_id: str,
        payload: Dict[str, Any],
        parent_event_id: Optional[str] = None,
    ) -> GraphEvent:
        graph = self._session.graph
        event = GraphEvent(
            event_id=f"event-{len(graph.events) + 1:04d}",
            graph_id=graph.graph_id,
            actor_type=actor_type,  # type: ignore[arg-type]
            actor_id=actor_id,
            event_type=event_type,
            target_id=target_id,
            payload=dict(payload or {}),
            parent_event_id=parent_event_id,
        )
        graph.events.append(event)
        self._session.case_state.last_event_id = event.event_id
        return event

    def _sync_graph_status(self) -> None:
        graph = self._session.graph
        nodes = graph.nodes
        if any(str(node.status) == "failed" for node in nodes):
            graph.status = "failed"
            return
        if all(str(node.status) == "succeeded" for node in nodes):
            graph.status = "completed"
            return
        if any(str(node.status) == "running" for node in nodes):
            graph.status = "running"
            return
        if any(str(node.status) in {"blocked", "patched"} for node in nodes):
            graph.status = "paused"
            return
        graph.status = "ready" if nodes else "draft"

    def _recompute_session_state(self) -> None:
        graph = self._session.graph
        self._session.case_state.active_graph_id = graph.graph_id
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in graph.artifacts[-3:]]
        running = next((node for node in graph.nodes if str(node.status) == "running"), None)
        if running is not None:
            self._session.case_state.active_node_id = running.node_id
            self._session.case_state.ui_focus = {"panel": "graph", "selected_node": running.node_id}
            return
        next_node = self._select_next_node()
        self._session.case_state.active_node_id = None if next_node is None else next_node.node_id
        self._session.case_state.ui_focus = {"panel": "graph", "selected_node": "" if next_node is None else next_node.node_id}

    def _select_next_node(self) -> Optional[ActionNode]:
        graph = self._session.graph
        for node in graph.nodes:
            if str(node.status) == "running":
                return node
        for node in graph.nodes:
            if str(node.status) in {"planned", "ready", "patched"} and _is_satisfied(graph, node):
                return node
        return None

    def _run_node(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        if not _is_satisfied(graph, node):
            node.status = "blocked"
            self._session.case_state.last_error = "dependencies not satisfied"
            event = self._append_event(
                actor_type="executor",
                actor_id="cerebellum",
                event_type="node_blocked",
                target_id=node.node_id,
                payload={"reason": "dependencies not satisfied"},
            )
            return ExecutionOutcome(node.node_id, "blocked", "dependencies not satisfied", [], [event.event_id])

        attempt_id = self._begin_attempt(node)
        node.status = "running"
        start_event = self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="node_started",
            target_id=node.node_id,
            payload={"title": node.title, "action_type": node.action_type, "attempt_id": attempt_id},
        )
        self._update_attempt(node, status="running", event_ids=[start_event.event_id])
        try:
            outcome = self._simulate_node_execution(node)
            node.status = outcome.status  # type: ignore[assignment]
            self._session.case_state.last_error = None
        except Exception as exc:
            message = str(exc).strip() or f"{node.action_type} failed"
            node.status = "failed"
            # Surface the reason on the node itself so the inspector can render it.
            node.notes = message
            self._session.case_state.last_error = message
            if node.tool_name:
                self._record_runtime_tool_result(
                    tool_name=str(node.tool_name),
                    ok=False,
                    consumable=False,
                    data={"error": message},
                    generated_artifacts=[],
                    validation={"error": message},
                    attempt_id=node.current_attempt_id,
                    rerun_from=node.rerun_from,
                    supersedes=node.supersedes,
                )
            fail_event = self._append_event(
                actor_type="executor",
                actor_id="cerebellum",
                event_type="node_failed",
                target_id=node.node_id,
                payload={"title": node.title, "action_type": node.action_type, "error": message, "attempt_id": attempt_id},
            )
            self._update_attempt(
                node,
                status="failed",
                event_ids=[start_event.event_id, fail_event.event_id],
                artifact_ids=[],
                message=message,
                error=message,
            )
            graph.version += 1
            return ExecutionOutcome(node.node_id, "failed", message, [], [start_event.event_id, fail_event.event_id])
        finish_event = self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="node_finished" if str(node.status) == "succeeded" else "node_failed",
            target_id=node.node_id,
            payload={
                "title": node.title,
                "action_type": node.action_type,
                "artifacts": outcome.artifact_ids,
                "status": node.status,
                "attempt_id": attempt_id,
            },
        )
        self._update_attempt(
            node,
            status=str(node.status),
            event_ids=[start_event.event_id, finish_event.event_id],
            artifact_ids=outcome.artifact_ids,
            message=outcome.message,
        )
        graph.version += 1
        return ExecutionOutcome(node.node_id, str(node.status), outcome.message, outcome.artifact_ids, [start_event.event_id, finish_event.event_id])

    def _finish_running_node(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        attempt_id = node.current_attempt_id or self._begin_attempt(node)
        start_event = self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="node_resumed",
            target_id=node.node_id,
            payload={"title": node.title, "action_type": node.action_type, "attempt_id": attempt_id},
        )
        self._update_attempt(node, status="running", event_ids=[start_event.event_id])
        try:
            outcome = self._simulate_node_execution(node)
            node.status = outcome.status  # type: ignore[assignment]
            self._session.case_state.last_error = None
        except Exception as exc:
            message = str(exc).strip() or f"{node.action_type} failed"
            node.status = "failed"
            # Surface the reason on the node itself so the inspector can render it.
            node.notes = message
            self._session.case_state.last_error = message
            if node.tool_name:
                self._record_runtime_tool_result(
                    tool_name=str(node.tool_name),
                    ok=False,
                    consumable=False,
                    data={"error": message},
                    generated_artifacts=[],
                    validation={"error": message},
                    attempt_id=node.current_attempt_id,
                    rerun_from=node.rerun_from,
                    supersedes=node.supersedes,
                )
            fail_event = self._append_event(
                actor_type="executor",
                actor_id="cerebellum",
                event_type="node_failed",
                target_id=node.node_id,
                payload={"title": node.title, "action_type": node.action_type, "error": message, "attempt_id": attempt_id},
            )
            self._update_attempt(
                node,
                status="failed",
                event_ids=[start_event.event_id, fail_event.event_id],
                artifact_ids=[],
                message=message,
                error=message,
            )
            graph.version += 1
            return ExecutionOutcome(node.node_id, "failed", message, [], [start_event.event_id, fail_event.event_id])
        finish_event = self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="node_finished" if str(node.status) == "succeeded" else "node_failed",
            target_id=node.node_id,
            payload={
                "title": node.title,
                "action_type": node.action_type,
                "artifacts": outcome.artifact_ids,
                "status": node.status,
                "attempt_id": attempt_id,
            },
        )
        self._update_attempt(
            node,
            status=str(node.status),
            event_ids=[start_event.event_id, finish_event.event_id],
            artifact_ids=outcome.artifact_ids,
            message=outcome.message,
        )
        graph.version += 1
        return ExecutionOutcome(node.node_id, str(node.status), outcome.message, outcome.artifact_ids, [start_event.event_id, finish_event.event_id])

    def _simulate_node_execution(self, node: ActionNode) -> ExecutionOutcome:
        handlers = {
            "identify_sequences": self._exec_identify_sequences,
            "register_to_reference": self._exec_register_to_reference,
            "segment_prostate": self._exec_segment_prostate,
            "reconstruct_grappa": self._exec_reconstruct_grappa,
            "segment_cardiac_cine": self._exec_segment_cardiac_cine,
            "classify_cardiac_cine_disease": self._exec_classify_cardiac_cine_disease,
            "generate_qa_snapshot": self._exec_generate_qa_snapshot,
            "package_vlm_evidence": self._exec_package_vlm_evidence,
            "generate_report": self._exec_generate_report,
        }
        handler = handlers.get(str(node.action_type)) or handlers.get(str(node.tool_name or ""))
        if handler is not None:
            return handler(node)

        tool_name = _node_tool_identity(node)
        if tool_name:
            raise MissingExecutorHandlerError(
                f"no executor handler for tool {tool_name} (node {node.node_id}); "
                "the planner can compile this tool but the v4 executor cannot run it yet, "
                "so no placeholder result will be reported as success"
            )
        if str(node.kind) == "tool":
            raise MissingExecutorHandlerError(
                f"no executor handler for tool node {node.node_id} (action_type={node.action_type!r}); "
                "tool nodes must resolve to a real tool call"
            )
        # Non-tool / presentational nodes (read_case, review_checkpoint, merge, ...)
        # legitimately have no tool to run; they still get a placeholder bundle.
        return self._exec_non_tool_node(node)

    def _artifact_ref(
        self,
        *,
        node: ActionNode,
        name: str,
        kind: str,
        role: str,
        uri: str,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        self._artifact_count += 1
        artifact_metadata = dict(metadata or {})
        if node.current_attempt_id:
            artifact_metadata.setdefault("attempt_id", str(node.current_attempt_id))
        if node.rerun_from:
            artifact_metadata.setdefault("rerun_from", str(node.rerun_from))
        if node.supersedes:
            artifact_metadata.setdefault("supersedes", str(node.supersedes))
        artifact = ArtifactRef(
            artifact_id=f"artifact-{self._artifact_count:04d}",
            node_id=node.node_id,
            name=name,
            kind=kind,  # type: ignore[arg-type]
            uri=uri,
            role=role,  # type: ignore[arg-type]
            mime_type=mime_type,
            visible=True,
            metadata=artifact_metadata,
        )
        graph = self._session.graph
        graph.artifacts.append(artifact)
        if artifact.artifact_id not in node.artifact_refs:
            node.artifact_refs.append(artifact.artifact_id)
        self._append_event(
            actor_type="executor",
            actor_id="cerebellum",
            event_type="artifact_added",
            target_id=artifact.artifact_id,
            payload={
                "node_id": node.node_id,
                "uri": artifact.uri,
                "kind": artifact.kind,
                "role": artifact.role,
                "attempt_id": node.current_attempt_id,
            },
        )
        return artifact

    def _write_step_bundle(
        self,
        *,
        node: ActionNode,
        title: str,
        node_dir: str,
        json_name: Optional[str] = None,
        txt_name: Optional[str] = None,
        svg_name: Optional[str] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        txt_payload: Optional[str] = None,
        svg_lines: Optional[Sequence[str]] = None,
        svg_accent: str = "#9c4f2f",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ArtifactRef]:
        artifacts: List[ArtifactRef] = []
        if json_name and json_payload is not None:
            result = self._writer.write_json(_artifact_rel_path(node_dir, json_name), json_payload)
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=title if len(artifacts) == 0 else f"{title} JSON",
                    kind="json",
                    role="output",
                    uri=result.uri,
                    mime_type=result.mime_type,
                    metadata=metadata,
                )
            )
        if txt_name and txt_payload is not None:
            result = self._writer.write_text(_artifact_rel_path(node_dir, txt_name), txt_payload)
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=f"{title} Notes",
                    kind="text",
                    role="evidence",
                    uri=result.uri,
                    mime_type=result.mime_type,
                    metadata=metadata,
                )
            )
        if svg_name and svg_lines is not None:
            result = self._writer.write_svg(
                _artifact_rel_path(node_dir, svg_name),
                title=title,
                lines=svg_lines,
                accent=svg_accent,
            )
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=f"{title} Preview",
                    kind="svg",
                    role="preview",
                    uri=result.uri,
                    mime_type=result.mime_type,
                    metadata=metadata,
                )
            )
        return artifacts

    def _exec_identify_sequences(self, node: ActionNode) -> ExecutionOutcome:
        # There is no configured "mock mode" anywhere in this codebase, so a failure to
        # reach the real v3 tool must surface as a failure -- never as a fabricated
        # sequence index. Mirrors _exec_generate_report.
        real_outcome = self._exec_identify_sequences_v3(node)
        if real_outcome is None:
            raise RuntimeError(
                "identify_sequences could not run: no readable case input_root and no demo case "
                f"for domain {self._session.case_state.domain!r}"
            )
        return real_outcome

    def _exec_identify_sequences_v3(self, node: ActionNode) -> Optional[ExecutionOutcome]:
        case_state = self._session.case_state
        graph = self._session.graph
        input_root = Path(str(case_state.input_root or "")).expanduser()
        if not input_root.exists():
            fallback_case = resolve_demo_case(case_state.domain)
            if fallback_case is None:
                return None
            input_root = fallback_case
            case_state.input_root = str(fallback_case)

        node_dir = make_node_artifact_dir(graph_id=self._session.graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        runtime_state = self._load_runtime_case_state()
        runtime_state["metadata"] = {"domain": case_state.domain, "input_root": str(input_root)}
        self._save_runtime_case_state(runtime_state)
        result = run_v3_tool(
            "identify_sequences",
            {
                "dicom_case_dir": str(input_root.resolve()),
                "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
                "convert_to_nifti": False,
                "deep_dump": False,
                "require_pydicom": False,
            },
            case_id=case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )

        artifacts: List[ArtifactRef] = []
        for generated in result.generated_artifacts:
            artifact_path = Path(str(generated.get("path") or "")).resolve()
            try:
                uri = artifact_path.relative_to(self._root_dir).as_posix()
            except Exception:
                uri = str(artifact_path)
            metadata = {
                "source": "v3_tool",
                "description": generated.get("description") or "",
                "tool_name": "identify_sequences",
            }
            kind = str(generated.get("kind") or "json").lower()
            role = "evidence"
            if kind in {"json", "text"}:
                role = "output"
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=str(generated.get("description") or artifact_path.name or "identify artifact"),
                    kind=("text" if kind in {"txt", "text"} else "json" if kind == "json" else "nifti"),  # type: ignore[arg-type]
                    role=role,  # type: ignore[arg-type]
                    uri=uri,
                    mime_type=generated.get("media_type"),
                    metadata=metadata,
                )
            )

        mapping = _ensure_dict(result.data.get("mapping"))
        case_state.sequence_index = {str(key): str(value) for key, value in mapping.items()}
        case_state.available_modalities = sorted(case_state.sequence_index.keys())
        if case_state.available_modalities:
            first_modality = case_state.available_modalities[0]
            self._session.case_state.ui_focus = {"panel": "viewer", "selected_node": node.node_id, "selected_modality": first_modality}
        node.outputs = {
            "mapping": case_state.sequence_index,
            "series_inventory_path": str(result.data.get("series_inventory_path") or ""),
            "dicom_meta_path": str(result.data.get("dicom_meta_path") or ""),
            "dicom_headers_index_path": str(result.data.get("dicom_headers_index_path") or ""),
            "note": str(result.data.get("note") or "Executed via v3 identify_sequences"),
            "warnings": list(result.warnings),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("identify_sequences"),
        }
        node.notes = f"Executed via v3 identify_sequences on {input_root.name}"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        validation = self._validate_tool_result_contract("identify_sequences", result.data)
        self._record_runtime_tool_result(
            tool_name="identify_sequences",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Sequence inventory generated via v3 identify_sequences",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_register_to_reference(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        fixed = self._resolve_sequence_reference(node.inputs.get("fixed"), fallback_modality="T2w")
        moving = self._resolve_sequence_reference(node.inputs.get("moving"), fallback_modality="ADC")
        if not fixed or not moving:
            raise ContractValidationError("register_to_reference missing resolved fixed/moving inputs")

        result = run_v3_tool(
            "register_to_reference",
            {
                "fixed": fixed,
                "moving": moving,
                "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
            },
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="register_to_reference",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("register_to_reference", result.data)
        node.outputs = {
            "fixed": str(result.data.get("fixed") or fixed),
            "moving": str(result.data.get("moving") or moving),
            "resampled_path": str(result.data.get("resampled_path") or ""),
            "transform_path": str(result.data.get("transform_path") or ""),
            "resampled_paths": _ensure_dict(result.data.get("resampled_paths")),
            "qc_pngs": _ensure_dict(result.data.get("qc_pngs")),
            "note": str(result.data.get("note") or ""),
            "warnings": list(result.warnings),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("register_to_reference"),
        }
        node.notes = "Registration completed via v3 register_to_reference"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        self._record_runtime_tool_result(
            tool_name="register_to_reference",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Registration completed via v3 register_to_reference",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_segment_prostate(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        t2w_ref = self._resolve_sequence_reference(node.inputs.get("t2w_ref"), fallback_modality="T2w")
        if not t2w_ref:
            raise ContractValidationError("segment_prostate missing resolved T2w input")

        result = run_v3_tool(
            "segment_prostate",
            {
                "t2w_ref": t2w_ref,
                "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
            },
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="segment_prostate",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("segment_prostate", result.data)
        node.outputs = {
            "prostate_mask_path": str(result.data.get("prostate_mask_path") or ""),
            "zone_mask_path": str(result.data.get("zone_mask_path") or ""),
            "t2w_input_path": str(result.data.get("t2w_input_path") or ""),
            "note": str(result.data.get("note") or ""),
            "warnings": list(result.warnings),
            "degraded_mode": bool(result.data.get("degraded_mode")),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("segment_prostate"),
        }
        node.notes = "Segmentation completed via v3 segment_prostate"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        self._record_runtime_tool_result(
            tool_name="segment_prostate",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Prostate segmentation completed via v3 segment_prostate",
            [a.artifact_id for a in artifacts],
            [],
        )

    # ------------------------------------------------------------------
    # Raw k-space reconstruction (cardiac chain entry point)
    # ------------------------------------------------------------------

    def _resolve_kspace_input(self, node: ActionNode) -> Path:
        """Resolve the single HDF5 k-space file the reconstruction consumes.

        The compiler emits ``inputs={"h5_path": "@case.input"}``; that placeholder is
        never resolvable through the sequence index (identify_sequences has not run --
        it *cannot* run until this node has produced an image), so it falls back to the
        case root.  A directory is only accepted when exactly one HDF5 lives in it:
        picking one of several silently would make the whole downstream chain describe
        a file nobody chose.
        """
        inputs = dict(node.inputs or {})
        raw = inputs.get("h5_path") or inputs.get("kspace_path") or inputs.get("h5") or ""
        text = str(raw).strip()
        if not text or text.startswith("@"):
            text = str(self._session.case_state.input_root or "").strip()
        if not text:
            raise ContractValidationError(
                "reconstruct_grappa could not resolve an HDF5 input: node inputs "
                f"{inputs!r} and case input_root are both empty"
            )
        # ``os.path.abspath`` rather than ``Path.resolve()``: a case directory may hold
        # symlinks into a read-only share that the operator does not want echoed back
        # into the graph (and therefore onto the screen).  The path the operator
        # registered is the path recorded; h5py follows the link either way.
        candidate = Path(os.path.abspath(str(Path(text).expanduser())))
        if candidate.is_file():
            return candidate
        if not candidate.is_dir():
            raise ContractValidationError(f"reconstruct_grappa input does not exist: {candidate}")
        kspace_files = _kspace_files_in(candidate)
        if not kspace_files:
            raise ContractValidationError(
                f"reconstruct_grappa found no .h5/.hdf5 file under {candidate}"
            )
        if len(kspace_files) > 1:
            joined = ", ".join(item.name for item in kspace_files)
            raise ContractValidationError(
                f"reconstruct_grappa input is ambiguous: {candidate} holds {len(kspace_files)} "
                f"HDF5 files ({joined}); set node.inputs['h5_path'] to the intended file"
            )
        return kspace_files[0]

    def _resolve_recon_params(self, node: ActionNode, h5_path: Path) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Collect the optional reconstruction arguments, with provenance.

        Precedence: explicit ``node.inputs`` beats a ``recon_params.json`` sidecar.
        Nothing is defaulted here -- a key that neither source supplies is simply not
        forwarded, and the engine tool's own documented default applies.
        """
        inputs = dict(node.inputs or {})
        sidecar_payload: Dict[str, Any] = {}
        sidecar_path = ""
        candidates = [h5_path.parent / _RECON_PARAMS_FILENAME]
        input_root = str(self._session.case_state.input_root or "").strip()
        if input_root:
            candidates.append(Path(input_root).expanduser() / _RECON_PARAMS_FILENAME)
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ContractValidationError(
                    f"reconstruct_grappa could not read {candidate}: {exc}"
                ) from exc
            if isinstance(loaded, dict):
                sidecar_payload = loaded
                sidecar_path = str(candidate)
                break

        params: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        for key in _RECON_FORWARDED_ARG_KEYS:
            if key in inputs:
                value = inputs[key]
                if value is not None and not (isinstance(value, str) and (not value.strip() or value.startswith("@"))):
                    params[key] = value
                    sources[key] = "node.inputs"
                    continue
            if key in sidecar_payload:
                value = sidecar_payload[key]
                if value is not None:
                    params[key] = value
                    sources[key] = sidecar_path
        return params, sources

    def _render_reconstruction_previews(
        self,
        *,
        node: ActionNode,
        node_dir: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Make the reconstruction visible: a centre-frame PNG, plus a before/after
        when a zero-filled reference exists.

        Auxiliary, exactly like the cardiac segmentation's snapshot: the NIfTI has
        already been contract-validated by the time we get here, so a failed PNG does
        not turn a real reconstruction into a failed node.  Failures are recorded as
        ``ok=false`` tool records plus ``preview_errors`` on the node -- never hidden,
        never replaced by a placeholder image.
        """
        out: Dict[str, Any] = {"artifacts": [], "snapshot_png": "", "comparison_png": "", "errors": [], "warnings": []}
        reconstructed = str(data.get("reconstructed_nifti") or "").strip()
        if not reconstructed:
            out["errors"].append("reconstruct_grappa returned no reconstructed_nifti to preview")
            return out

        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        base_subdir = node_dir.split("/", 1)[1] if "/" in node_dir else node_dir
        zerofilled = str(data.get("zerofilled_nifti") or "").strip()

        jobs: List[Tuple[str, Dict[str, Any], str, Optional[str]]] = [
            (
                "generate_qa_snapshot",
                {
                    "input_nifti": reconstructed,
                    "output_subdir": _artifact_rel_path(base_subdir, "qa"),
                    "title": f"{Path(reconstructed).name} | reconstructed cine (centre frame / centre slice)",
                },
                "snapshot_png",
                _qa_snapshot_profile_override(),
            )
        ]
        if zerofilled:
            jobs.append(
                (
                    "compare_nifti_slices",
                    {
                        "image_a": zerofilled,
                        "image_b": reconstructed,
                        "label_a": "Zero-filled (no GRAPPA)",
                        "label_b": "GRAPPA reconstruction",
                        "output_subdir": _artifact_rel_path(base_subdir, "qa"),
                    },
                    "comparison_png",
                    None,
                )
            )

        for tool_name, args, result_key, profile_override in jobs:
            try:
                kwargs: Dict[str, Any] = {
                    "case_id": self._session.case_state.case_id,
                    "run_id": self._session.graph.graph_id,
                    "run_dir": run_dir,
                    "artifacts_dir": artifacts_dir,
                    "case_state_path": case_state_path,
                }
                if profile_override:
                    kwargs["runtime_profile_override"] = profile_override
                result = run_v3_tool(tool_name, args, **kwargs)
                validation = self._validate_tool_result_contract(tool_name, result.data)
            except Exception as exc:
                message = str(exc).strip() or f"{tool_name} failed"
                self._record_runtime_tool_result(
                    tool_name=tool_name,
                    ok=False,
                    consumable=False,
                    data={"error": message, **args},
                    generated_artifacts=[],
                    validation={"error": message},
                    attempt_id=node.current_attempt_id,
                    rerun_from=node.rerun_from,
                    supersedes=node.supersedes,
                )
                out["errors"].append(f"{tool_name}: {message}")
                continue
            out["artifacts"].extend(
                self._artifact_refs_from_generated(
                    node=node,
                    tool_name=tool_name,
                    generated_artifacts=result.generated_artifacts,
                )
            )
            self._record_runtime_tool_result(
                tool_name=tool_name,
                ok=True,
                data=result.data,
                generated_artifacts=result.generated_artifacts,
                consumable=validation["consumable"],
                validation=validation,
                attempt_id=node.current_attempt_id,
                rerun_from=node.rerun_from,
                supersedes=node.supersedes,
            )
            out[result_key] = str(result.data.get("output_png") or "")
            out["warnings"].extend(str(item) for item in result.warnings)
        return out

    def _exec_reconstruct_grappa(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        h5_path = self._resolve_kspace_input(node)
        params, param_sources = self._resolve_recon_params(node, h5_path)

        # The reconstruction lands in its own directory holding exactly one NIfTI, so
        # that the directory can become the case input for the image-domain nodes that
        # follow.  The zero-filled reference is deliberately written elsewhere: dropped
        # next to the reconstruction it would be enumerated as a second "cine" and
        # pushed through nnUNet.
        recon_dir = (self._artifact_root / node_dir / "recon").resolve()
        zerofill_dir = (self._artifact_root / node_dir / "zerofill").resolve()
        recon_dir.mkdir(parents=True, exist_ok=True)

        args: Dict[str, Any] = {
            "h5_path": str(h5_path),
            "output_subdir": _artifact_rel_path(node_dir.split("/", 1)[1] if "/" in node_dir else node_dir, "recon"),
            # 'cine' in the filename is what identify_sequences keys its CINE guess on.
            "output_nifti": str(recon_dir / "reconstructed_cine.nii.gz"),
        }
        args.update(params)
        if params.get("undersample_factor"):
            zerofill_dir.mkdir(parents=True, exist_ok=True)
            args["zerofilled_nifti"] = str(zerofill_dir / "zerofilled_cine.nii.gz")

        result = run_v3_tool(
            "reconstruct_grappa",
            args,
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="reconstruct_grappa",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("reconstruct_grappa", result.data)
        self._record_runtime_tool_result(
            tool_name="reconstruct_grappa",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )

        previews = self._render_reconstruction_previews(node=node, node_dir=node_dir, data=result.data)
        artifacts.extend(previews["artifacts"])

        reconstructed = str(result.data.get("reconstructed_nifti") or "")
        previous_input_root = str(self._session.case_state.input_root or "")
        # Everything downstream is image-domain.  Point the case at the directory the
        # reconstruction wrote so identify_sequences inventories the reconstructed cine
        # rather than the HDF5 it can say nothing about.  Both roots are recorded.
        self._session.case_state.input_root = str(recon_dir)
        runtime_state = self._load_runtime_case_state()
        metadata = _ensure_dict(runtime_state.get("metadata"))
        metadata.update(
            {
                "domain": self._session.case_state.domain,
                "input_root": str(recon_dir),
                "input_root_before_reconstruction": previous_input_root,
                "source_kspace_h5": str(h5_path),
            }
        )
        runtime_state["metadata"] = metadata
        self._save_runtime_case_state(runtime_state)

        undersample = _ensure_dict(result.data.get("undersample"))
        warnings = list(result.warnings)
        warnings.extend(str(item) for item in previews["warnings"])
        warnings.extend(f"reconstruction preview unavailable: {item}" for item in previews["errors"])
        node.outputs = {
            "reconstructed_nifti": reconstructed,
            "zerofilled_nifti": str(result.data.get("zerofilled_nifti") or ""),
            "h5_path": str(h5_path),
            "mode": str(result.data.get("mode") or ""),
            "kspace_key": str(result.data.get("kspace_key") or result.data.get("source_key") or ""),
            "kspace_shape": list(result.data.get("kspace_shape") or []),
            "output_shape": list(result.data.get("output_shape") or []),
            "pixel_spacing": list(result.data.get("pixel_spacing") or []),
            "n_coils": result.data.get("n_coils"),
            "frames_total": result.data.get("frames_total"),
            "grappa_applied_frames": result.data.get("grappa_applied_frames"),
            "grappa_skipped_frames": result.data.get("grappa_skipped_frames"),
            "grappa_failed_frames": result.data.get("grappa_failed_frames"),
            "acs_lines_used": result.data.get("acs_lines_used"),
            "kernel_size": list(result.data.get("kernel_size") or []),
            "nonspatial_order": list(result.data.get("nonspatial_order") or []),
            "undersample": undersample,
            "elapsed_seconds": result.data.get("elapsed_seconds"),
            "recon_param_sources": dict(param_sources),
            "case_input_root": str(recon_dir),
            "previous_case_input_root": previous_input_root,
            "qa_snapshot_path": previews["snapshot_png"],
            "comparison_png_path": previews["comparison_png"],
            "preview_errors": list(previews["errors"]),
            "warnings": warnings,
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("reconstruct_grappa"),
        }

        applied = result.data.get("grappa_applied_frames")
        total = result.data.get("frames_total")
        if isinstance(applied, int) and isinstance(total, int) and total:
            kernel_note = f"GRAPPA kernel applied to {applied}/{total} frames"
        else:
            kernel_note = "GRAPPA kernel application count not reported by the tool"
        note = f"Reconstructed {h5_path.name} via v3 reconstruct_grappa (mode={result.data.get('mode')}): {kernel_note}"
        if undersample.get("applied"):
            note = (
                f"{note}; input k-space was retrospectively undersampled R={undersample.get('factor')} "
                f"with {undersample.get('acs_lines')} ACS lines "
                f"({undersample.get('ky_lines_kept')}/{undersample.get('ky_lines_total')} ky lines kept)"
            )
        if previews["errors"]:
            note = f"{note} | preview unavailable: {'; '.join(previews['errors'])}"
        node.notes = note

        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            note,
            [a.artifact_id for a in artifacts],
            [],
        )

    # ------------------------------------------------------------------
    # Cardiac cine
    # ------------------------------------------------------------------

    def _resolve_cardiac_cine_input(self, node: ActionNode) -> Path:
        """Resolve the single cine volume (or safe directory) to segment.

        The compiler emits ``inputs={"cine_ref": "@case.input"}``, which normally
        resolves through the ``identify_sequences`` mapping to the ``CINE`` entry.
        When it cannot (no CINE key, no mapping yet) we fall back to the case root
        -- and then we must not simply hand that directory to the tool: the shipped
        demo case keeps ``patient061_frame01_gt.nii.gz`` next to the cine, and
        ``segment_cardiac_cine`` segments *every* NIfTI in a directory it is given,
        so the ground truth would be pushed through nnUNet as if it were anatomy.
        """
        inputs = dict(node.inputs or {})
        raw = inputs.get("cine_path") or inputs.get("cine_ref") or inputs.get("cine") or ""
        resolved = self._resolve_sequence_reference(raw, fallback_modality="CINE")
        text = str(resolved or "").strip()
        if not text or text.startswith("@"):
            # Unresolved planner placeholder and no CINE in the sequence index. When the
            # case started from raw k-space the reconstruction node already produced the
            # only cine there is, so use it rather than guessing from the case root --
            # which for a k-space case still contains the HDF5 this tool cannot read.
            text = self._latest_reconstructed_cine()
        if not text or text.startswith("@"):
            text = str(self._session.case_state.input_root or "").strip()
        if not text:
            raise ContractValidationError(
                "segment_cardiac_cine could not resolve a cine input: node inputs "
                f"{inputs!r} and case input_root are both empty"
            )
        candidate = Path(text).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        if not candidate.is_dir():
            raise ContractValidationError(f"segment_cardiac_cine cine input does not exist: {candidate}")

        nifti_files = _nifti_files_in(candidate)
        if not nifti_files:
            raise ContractValidationError(f"segment_cardiac_cine found no NIfTI volume under {candidate}")
        labels = [item for item in nifti_files if _is_label_nifti(item)]
        images = [item for item in nifti_files if not _is_label_nifti(item)]
        if not images:
            joined = ", ".join(item.name for item in labels)
            raise ContractValidationError(
                f"segment_cardiac_cine found only label volumes under {candidate}: {joined}"
            )
        if not labels:
            # Nothing to exclude: hand the directory over and let the tool enumerate
            # cases itself, which is what the engine's own cardiac path does.
            return candidate.resolve()
        if len(images) == 1:
            return images[0].resolve()
        raise ContractValidationError(
            f"segment_cardiac_cine cine input is ambiguous: {candidate} holds {len(images)} candidate "
            f"cine volumes next to {len(labels)} label volume(s) ({', '.join(item.name for item in labels)}); "
            "the directory cannot be passed wholesale without segmenting the labels, so set "
            "node.inputs['cine_path'] to the intended volume"
        )

    def _latest_reconstructed_cine(self) -> str:
        """Path of the cine a succeeded ``reconstruct_grappa`` node produced, or ""."""
        for candidate in self._session.graph.nodes:
            if _node_tool_identity(candidate) != "reconstruct_grappa":
                continue
            if str(candidate.status) != "succeeded":
                continue
            value = str(_ensure_dict(candidate.outputs).get("reconstructed_nifti") or "").strip()
            if value:
                return value
        data = self._latest_runtime_tool_data("reconstruct", "reconstruct_grappa")
        return str(data.get("reconstructed_nifti") or "").strip()

    def _cardiac_snapshot_anatomy(self, *, data: Dict[str, Any], cine_path: Path) -> Optional[Path]:
        """Pick the volume the segmentation is actually defined on.

        ``segment_cardiac_cine`` stages each case as ``<input_dir>/<case_id>_0000.nii.gz``
        (a symlink to the source volume, or an extracted ED/ES frame for 4-D cine),
        and nnUNet predicts on that grid.  Overlaying on the staged volume therefore
        guarantees the mask and the anatomy share a shape; the raw ``cine_path`` may
        not when ``use_ed_es_only`` split a 4-D series.
        """
        case_results = [item for item in (data.get("case_results") or []) if isinstance(item, dict)]
        case_id = str(case_results[0].get("case_id") or "").strip() if case_results else ""
        input_dir = str(data.get("input_dir") or "").strip()
        if case_id and input_dir:
            for suffix in (".nii.gz", ".nii"):
                staged = Path(input_dir) / f"{case_id}_0000{suffix}"
                if staged.exists():
                    return staged
        if cine_path.is_file():
            return cine_path
        return None

    def _render_cardiac_qa_snapshot(
        self,
        *,
        node: ActionNode,
        node_dir: str,
        cine_path: Path,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Render a PNG of the centre slice with the cardiac mask overlaid.

        ``generate_qa_snapshot`` is not part of any compiled blueprint and wiring it
        in as its own node would mean editing ``packages/planner`` -- so the
        segmentation node emits the snapshot as an extra artifact of its own.

        The snapshot is auxiliary: the segmentation itself has already been contract
        validated by the time we get here, so a snapshot failure does NOT turn a real
        segmentation into a failed node.  It is recorded loudly instead -- as a
        ``generate_qa_snapshot`` record with ``ok=false`` in the runtime case_state,
        as ``qa_snapshot_error`` in ``node.outputs``, and on ``node.notes``.  Nothing
        fake is written in its place.
        """
        empty: Dict[str, Any] = {"artifacts": [], "output_png": "", "error": "", "warnings": []}
        seg_path = str(data.get("seg_path") or "").strip()
        if not seg_path:
            empty["error"] = "segment_cardiac_cine returned no seg_path to overlay"
            return empty
        anatomy = self._cardiac_snapshot_anatomy(data=data, cine_path=cine_path)
        if anatomy is None:
            empty["error"] = (
                "no single anatomy volume to overlay: neither the staged nnUNet input nor "
                f"{cine_path} resolves to a file"
            )
            return empty

        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        output_subdir = _artifact_rel_path(node_dir.split("/", 1)[1] if "/" in node_dir else node_dir, "qa")
        args: Dict[str, Any] = {
            "input_nifti": str(anatomy),
            "mask_nifti": seg_path,
            "output_subdir": output_subdir,
            "title": f"{anatomy.name} | cardiac segmentation overlay (1=RV, 2=MYO, 3=LV)",
        }
        pred_dir = str(data.get("pred_dir") or "").strip()
        if pred_dir:
            pred_path = Path(pred_dir)
            if pred_path.is_dir() and len(sorted(pred_path.glob("*.nii.gz"))) > 1:
                # Multi-frame run: let the tool frame-match the overlay itself.
                args["seg_dir"] = str(pred_path)

        try:
            result = run_v3_tool(
                "generate_qa_snapshot",
                args,
                case_id=self._session.case_state.case_id,
                run_id=self._session.graph.graph_id,
                run_dir=run_dir,
                artifacts_dir=artifacts_dir,
                case_state_path=case_state_path,
                runtime_profile_override=_qa_snapshot_profile_override(),
            )
            validation = self._validate_tool_result_contract("generate_qa_snapshot", result.data)
        except Exception as exc:
            message = str(exc).strip() or "generate_qa_snapshot failed"
            self._record_runtime_tool_result(
                tool_name="generate_qa_snapshot",
                ok=False,
                consumable=False,
                data={"error": message, **args},
                generated_artifacts=[],
                validation={"error": message},
                attempt_id=node.current_attempt_id,
                rerun_from=node.rerun_from,
                supersedes=node.supersedes,
            )
            empty["error"] = message
            return empty

        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="generate_qa_snapshot",
            generated_artifacts=result.generated_artifacts,
        )
        self._record_runtime_tool_result(
            tool_name="generate_qa_snapshot",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return {
            "artifacts": artifacts,
            "output_png": str(result.data.get("output_png") or ""),
            "error": "",
            "warnings": list(result.warnings),
        }

    def _exec_segment_cardiac_cine(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        cine_path = self._resolve_cardiac_cine_input(node)

        args: Dict[str, Any] = {
            "cine_path": str(cine_path),
            "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
        }
        inputs = dict(node.inputs or {})
        # Backend / weights selection is normally supplied by the MRI_AGENT_CARDIAC_*
        # environment the tool reads itself; only forward explicit node overrides.
        for key in (
            "backend_root",
            "nnunet_python",
            "results_folder",
            "task_name",
            "trainer_class_name",
            "model",
            "checkpoint",
            "acdc_info_cfg",
        ):
            value = str(inputs.get(key) or "").strip()
            if value:
                args[key] = value
        for key in ("disable_tta", "use_ed_es_only"):
            if key in inputs:
                args[key] = bool(inputs[key])
        folds = inputs.get("folds")
        if isinstance(folds, (list, tuple)) and folds:
            args["folds"] = [int(item) for item in folds]

        result = run_v3_tool(
            "segment_cardiac_cine",
            args,
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="segment_cardiac_cine",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("segment_cardiac_cine", result.data)
        # Record the segmentation before the snapshot so the runtime stage order
        # reflects what actually happened first.
        self._record_runtime_tool_result(
            tool_name="segment_cardiac_cine",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )

        snapshot = self._render_cardiac_qa_snapshot(
            node=node,
            node_dir=node_dir,
            cine_path=cine_path,
            data=result.data,
        )
        artifacts.extend(snapshot["artifacts"])

        warnings = list(result.warnings)
        warnings.extend(str(item) for item in snapshot["warnings"])
        if snapshot["error"]:
            warnings.append(f"generate_qa_snapshot failed: {snapshot['error']}")
        node.outputs = {
            "cine_path": str(cine_path),
            "seg_path": str(result.data.get("seg_path") or ""),
            "rv_mask_path": str(result.data.get("rv_mask_path") or ""),
            "myo_mask_path": str(result.data.get("myo_mask_path") or ""),
            "lv_mask_path": str(result.data.get("lv_mask_path") or ""),
            "pred_dir": str(result.data.get("pred_dir") or ""),
            "case_results": [item for item in (result.data.get("case_results") or []) if isinstance(item, dict)],
            "qa_snapshot_path": snapshot["output_png"],
            "qa_snapshot_error": snapshot["error"],
            "note": str(result.data.get("note") or ""),
            "warnings": warnings,
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("segment_cardiac_cine"),
        }
        node.notes = "Cardiac cine segmentation completed via v3 segment_cardiac_cine"
        if snapshot["error"]:
            node.notes = f"{node.notes} | QA snapshot unavailable: {snapshot['error']}"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Cardiac cine segmentation completed via v3 segment_cardiac_cine",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _latest_runtime_tool_data(self, stage: str, tool_name: str) -> Dict[str, Any]:
        """Most recent successful ``data`` block for a tool in the runtime case_state."""
        state = self._load_runtime_case_state()
        records = _ensure_dict(_ensure_dict(state.get("stage_outputs")).get(stage)).get(tool_name)
        if not isinstance(records, list):
            return {}
        for record in reversed(records):
            if isinstance(record, dict) and record.get("ok") and isinstance(record.get("data"), dict):
                return dict(record["data"])
        return {}

    def _resolve_cardiac_segmentation_outputs(self, node: ActionNode) -> Dict[str, str]:
        """Locate the upstream cardiac segmentation this classify node consumes."""
        inputs = dict(node.inputs or {})
        out: Dict[str, str] = {}
        for key in ("seg_path", "seg_dir", "cine_path", "ed_seg_path", "es_seg_path"):
            value = str(inputs.get(key) or "").strip()
            if value and not value.startswith("@"):
                out[key] = value
        if out.get("seg_path"):
            return out

        for candidate in self._session.graph.nodes:
            if _node_tool_identity(candidate) != "segment_cardiac_cine":
                continue
            outputs = _ensure_dict(candidate.outputs)
            seg_path = str(outputs.get("seg_path") or "").strip()
            if seg_path:
                out.setdefault("seg_path", seg_path)
                for source_key, target_key in (("pred_dir", "seg_dir"), ("cine_path", "cine_path")):
                    value = str(outputs.get(source_key) or "").strip()
                    if value:
                        out.setdefault(target_key, value)
                return out

        data = self._latest_runtime_tool_data("segment", "segment_cardiac_cine")
        seg_path = str(data.get("seg_path") or "").strip()
        if seg_path:
            out.setdefault("seg_path", seg_path)
            pred_dir = str(data.get("pred_dir") or "").strip()
            if pred_dir:
                out.setdefault("seg_dir", pred_dir)
        return out

    def _exec_classify_cardiac_cine_disease(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1

        resolved = self._resolve_cardiac_segmentation_outputs(node)
        seg_path = resolved.get("seg_path") or ""
        if not seg_path:
            raise ContractValidationError(
                "classify_cardiac_cine_disease could not resolve seg_path: no succeeded "
                "segment_cardiac_cine node outputs and no segment stage record in the runtime case_state"
            )

        args: Dict[str, Any] = {
            "seg_path": seg_path,
            "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
        }
        for key in ("cine_path", "ed_seg_path", "es_seg_path", "patient_info_path"):
            value = str(resolved.get(key) or dict(node.inputs or {}).get(key) or "").strip()
            if value and not value.startswith("@"):
                args[key] = value
        seg_dir = str(resolved.get("seg_dir") or "").strip()
        if seg_dir:
            # Only useful when the segmentation produced several per-frame volumes;
            # the tool ignores a single-file directory and falls back to seg_path.
            seg_dir_path = Path(seg_dir)
            if seg_dir_path.is_dir() and len(sorted(seg_dir_path.glob("*.nii.gz"))) > 1:
                args["seg_dir"] = seg_dir
        for key in ("ed_frame", "es_frame"):
            value = dict(node.inputs or {}).get(key)
            if isinstance(value, int):
                args[key] = int(value)

        result = run_v3_tool(
            "classify_cardiac_cine_disease",
            args,
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="classify_cardiac_cine_disease",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("classify_cardiac_cine_disease", result.data)
        predicted_group = str(result.data.get("predicted_group") or "")
        node.outputs = {
            "classification_path": str(result.data.get("classification_path") or ""),
            "predicted_group": predicted_group,
            "ground_truth_group": str(result.data.get("ground_truth_group") or ""),
            "ground_truth_match": bool(result.data.get("ground_truth_match")),
            "needs_vlm_review": bool(result.data.get("needs_vlm_review")),
            "metrics": _ensure_dict(result.data.get("metrics")),
            "phase_indices": _ensure_dict(result.data.get("phase_indices")),
            "seg_path": seg_path,
            "warnings": list(result.warnings),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("classify_cardiac_cine_disease"),
        }
        node.notes = f"Cardiac disease classification via v3 classify_cardiac_cine_disease: {predicted_group or 'unknown'}"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        self._record_runtime_tool_result(
            tool_name="classify_cardiac_cine_disease",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            f"Cardiac cine disease classified via v3 classify_cardiac_cine_disease: {predicted_group or 'unknown'}",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_generate_qa_snapshot(self, node: ActionNode) -> ExecutionOutcome:
        """Standalone QA-snapshot node.

        Unlike the snapshot the segmentation node piggybacks, a node whose whole job
        is the snapshot must fail when the snapshot fails.
        """
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        inputs = dict(node.inputs or {})
        input_nifti = self._resolve_sequence_reference(inputs.get("input_nifti"))
        if not input_nifti or str(input_nifti).startswith("@"):
            raise ContractValidationError(
                f"generate_qa_snapshot missing a resolvable input_nifti (node inputs {inputs!r})"
            )
        args: Dict[str, Any] = {
            "input_nifti": str(input_nifti),
            "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
        }
        for key in ("mask_nifti", "seg_dir", "title", "output_png"):
            value = str(inputs.get(key) or "").strip()
            if value and not value.startswith("@"):
                args[key] = value

        result = run_v3_tool(
            "generate_qa_snapshot",
            args,
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
            runtime_profile_override=_qa_snapshot_profile_override(),
        )
        artifacts = self._artifact_refs_from_generated(
            node=node,
            tool_name="generate_qa_snapshot",
            generated_artifacts=result.generated_artifacts,
        )
        validation = self._validate_tool_result_contract("generate_qa_snapshot", result.data)
        node.outputs = {
            "output_png": str(result.data.get("output_png") or ""),
            "input_nifti": str(result.data.get("input_nifti") or ""),
            "mask_nifti": str(result.data.get("mask_nifti") or ""),
            "selected_frame": result.data.get("selected_frame"),
            "selected_slice": result.data.get("selected_slice"),
            "warnings": list(result.warnings),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("generate_qa_snapshot"),
        }
        node.notes = "QA snapshot rendered via v3 generate_qa_snapshot"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        self._record_runtime_tool_result(
            tool_name="generate_qa_snapshot",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "QA snapshot rendered via v3 generate_qa_snapshot",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_package_vlm_evidence(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        result = run_v3_tool(
            "package_vlm_evidence",
            {
                "case_state_path": str(case_state_path),
                "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
            },
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        artifacts: List[ArtifactRef] = []
        for generated in result.generated_artifacts:
            artifact_path = Path(str(generated.get("path") or "")).resolve()
            try:
                uri = artifact_path.relative_to(self._root_dir).as_posix()
            except Exception:
                uri = str(artifact_path)
            kind = str(generated.get("kind") or "json").lower()
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=str(generated.get("description") or artifact_path.name or "vlm evidence"),
                    kind=("json" if kind == "json" else "text"),  # type: ignore[arg-type]
                    role="output",
                    uri=uri,
                    mime_type=generated.get("media_type"),
                    metadata={"source": "v3_tool", "tool_name": "package_vlm_evidence"},
                )
            )
        node.outputs = {
            "status": str(result.data.get("status") or "success"),
            "vlm_evidence_path": str(result.data.get("vlm_evidence_path") or ""),
            "summary": str(result.data.get("summary") or ""),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("package_vlm_evidence"),
        }
        node.notes = "Packaged VLM evidence bundle via v3 package_vlm_evidence"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-2:]]
        self._session.case_state.active_node_id = node.node_id
        validation = self._validate_tool_result_contract("package_vlm_evidence", result.data)
        self._record_runtime_tool_result(
            tool_name="package_vlm_evidence",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "VLM evidence bundle packaged via v3 package_vlm_evidence",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_generate_report(self, node: ActionNode) -> ExecutionOutcome:
        real_outcome = self._exec_generate_report_v3(node)
        if real_outcome is None:
            raise RuntimeError("generate_report v3 bridge unavailable")
        return real_outcome

    def _exec_generate_report_v3(self, node: ActionNode) -> Optional[ExecutionOutcome]:
        graph = self._session.graph
        run_dir, artifacts_dir, case_state_path = self._ensure_runtime_workspace()
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        result = run_v3_tool(
            "generate_report",
            {
                "case_state_path": str(case_state_path),
                "output_subdir": node_dir.split("/", 1)[1] if "/" in node_dir else node_dir,
                "domain": self._session.case_state.domain,
                "llm_mode": "disabled",
                "emit_structured_report_text": False,
            },
            case_id=self._session.case_state.case_id,
            run_id=graph.graph_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
        )
        report_json_path = Path(str(result.data.get("report_json_path") or "")).expanduser().resolve()
        clinical_report_path = Path(str(result.data.get("clinical_report_path") or "")).expanduser().resolve()
        if report_json_path.exists() and clinical_report_path.exists():
            try:
                report_payload = json.loads(report_json_path.read_text(encoding="utf-8"))
            except Exception:
                report_payload = None
            if isinstance(report_payload, dict):
                original_clinical_text = clinical_report_path.read_text(encoding="utf-8")
                normalized_clinical_text = _normalize_clinical_report_text(report_payload, original_clinical_text)
                if normalized_clinical_text != original_clinical_text:
                    clinical_report_path.write_text(normalized_clinical_text, encoding="utf-8")
                if _clinical_report_has_contradiction(report_payload, clinical_report_path.read_text(encoding="utf-8")):
                    raise ContractValidationError(
                        "generate_report produced a clinical markdown contradiction despite segmentation_usable=true"
                    )
        artifacts: List[ArtifactRef] = []
        for generated in result.generated_artifacts:
            artifact_path = Path(str(generated.get("path") or "")).resolve()
            try:
                uri = artifact_path.relative_to(self._root_dir).as_posix()
            except Exception:
                uri = str(artifact_path)
            kind_raw = str(generated.get("kind") or "text").lower()
            if kind_raw == "json":
                kind = "json"
            elif kind_raw in {"md", "markdown", "report"}:
                kind = "report"
            else:
                kind = "text"
            role = "output" if kind in {"json", "report"} else "evidence"
            artifacts.append(
                self._artifact_ref(
                    node=node,
                    name=str(generated.get("description") or artifact_path.name or "report artifact"),
                    kind=kind,  # type: ignore[arg-type]
                    role=role,  # type: ignore[arg-type]
                    uri=uri,
                    mime_type=generated.get("media_type"),
                    metadata={"source": "v3_tool", "tool_name": "generate_report"},
                )
            )
        node.outputs = {
            "report_txt_path": str(result.data.get("report_txt_path") or ""),
            "report_json_path": str(result.data.get("report_json_path") or ""),
            "clinical_report_path": str(result.data.get("clinical_report_path") or ""),
            "vlm_evidence_bundle_path": str(result.data.get("vlm_evidence_bundle_path") or ""),
            "execution_mode": "v3_tool",
            "runtime_profile": _runtime_profile_label("generate_report"),
        }
        node.notes = "Generated deterministic report via v3 generate_report"
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts[-3:]]
        self._session.case_state.active_node_id = node.node_id
        validation = self._validate_tool_result_contract("generate_report", result.data)
        self._record_runtime_tool_result(
            tool_name="generate_report",
            ok=True,
            data=result.data,
            generated_artifacts=result.generated_artifacts,
            consumable=validation["consumable"],
            validation=validation,
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Report generated via v3 generate_report",
            [a.artifact_id for a in artifacts],
            [],
        )

    def _exec_generate_report_mock(self, node: ActionNode) -> ExecutionOutcome:
        graph = self._session.graph
        case_state = self._session.case_state
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        report_text = _join_lines(
            f"Case ID: {case_state.case_id}",
            "Impression: Deterministic mock prostate workflow completed.",
            "Findings:",
            "- Sequence inventory validated.",
            "- ADC registered to T2w.",
            "- Prostate gland segmented.",
            "Recommendation: review the generated evidence artifacts in the workstation.",
        )
        payload = {
            "impression": "Deterministic mock prostate workflow completed.",
            "findings": [
                "Sequence inventory validated",
                "ADC registered to T2w",
                "Prostate gland segmented",
            ],
            "report_path": _artifact_rel_path(case_state.case_id, "report", "prostate_report.txt"),
            "summary_path": _artifact_rel_path(case_state.case_id, "report", "prostate_report.json"),
        }
        artifacts = self._write_step_bundle(
            node=node,
            title="Prostate Report",
            node_dir=node_dir,
            json_name="report_summary.json",
            txt_name="prostate_report.txt",
            svg_name="report_timeline.svg",
            json_payload=payload,
            txt_payload=report_text,
            svg_lines=[
                "Report summary",
                "Sequence inventory: complete",
                "Registration: complete",
                "Segmentation: complete",
                "Impression: workflow complete",
            ],
            svg_accent="#8a5a2b",
            metadata={"impression": payload["impression"]},
        )
        node.outputs = {
            "report_path": artifacts[1].uri if len(artifacts) > 1 else "",
            "summary_path": artifacts[0].uri if artifacts else "",
            "status": "draft_complete",
            "runtime_profile": _runtime_profile_label("generate_report"),
        }
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts]
        self._session.case_state.active_node_id = node.node_id
        self._record_runtime_tool_result(
            tool_name="generate_report",
            ok=True,
            data=node.outputs,
            generated_artifacts=self._artifact_dicts_from_v4_refs(artifacts),
            attempt_id=node.current_attempt_id,
            rerun_from=node.rerun_from,
            supersedes=node.supersedes,
        )
        return ExecutionOutcome(node.node_id, "succeeded", "Report drafted", [a.artifact_id for a in artifacts], [])

    def _exec_non_tool_node(self, node: ActionNode) -> ExecutionOutcome:
        """Placeholder bundle for nodes that are not tool calls.

        Only reachable for presentational/control nodes (``read_case``,
        ``review_checkpoint``, merges, ...).  Nodes that name a real tool are
        rejected in ``_simulate_node_execution`` instead of landing here.
        """
        graph = self._session.graph
        case_state = self._session.case_state
        node_dir = make_node_artifact_dir(graph_id=graph.graph_id, step_index=self._step_count + 1, node_id=node.node_id)
        self._step_count += 1
        payload = {
            "action_type": node.action_type,
            "kind": str(node.kind),
            "inputs": dict(node.inputs or {}),
            "outputs": dict(node.outputs or {}),
            "status": "completed",
            "tool_executed": False,
        }
        artifacts = self._write_step_bundle(
            node=node,
            title=node.title,
            node_dir=node_dir,
            json_name=f"{node.node_id}.json",
            txt_name=f"{node.node_id}.txt",
            svg_name=f"{node.node_id}.svg",
            json_payload=payload,
            txt_payload=_join_lines(
                f"Non-tool node {node.node_id} ({node.action_type}) completed.",
                "No tool was executed for this node.",
            ),
            svg_lines=[node.title, "Non-tool node", "Status: completed (no tool executed)"],
            metadata={"action_type": node.action_type, "tool_executed": False},
        )
        node.outputs = {
            "result": "completed",
            "tool_executed": False,
            "output_dir": _artifact_rel_path(case_state.case_id, node.node_id),
        }
        self._session.case_state.selected_artifacts = [artifact.artifact_id for artifact in artifacts]
        self._session.case_state.active_node_id = node.node_id
        return ExecutionOutcome(
            node.node_id,
            "succeeded",
            "Non-tool node completed (no tool executed)",
            [a.artifact_id for a in artifacts],
            [],
        )

    # Backwards-compatible alias; the old name implied a tool ran, which it never did.
    _exec_generic_tool = _exec_non_tool_node

    def _apply_patch_operations(self, graph: ActionGraph, operations: Sequence[PatchOperation]) -> None:
        for operation in operations:
            op = str(operation.op or "").strip()
            if op == "update_node":
                self._op_update_node(graph, operation)
            elif op == "add_node":
                self._op_add_node(graph, operation)
            elif op == "remove_node":
                self._op_remove_node(graph, operation)
            elif op == "add_edge":
                self._op_add_edge(graph, operation)
            elif op == "remove_edge":
                self._op_remove_edge(graph, operation)
            elif op == "rebind_artifact":
                self._op_rebind_artifact(graph, operation)
            elif op == "lock_node":
                self._op_lock_node(graph, operation, locked=True)
            elif op == "unlock_node":
                self._op_lock_node(graph, operation, locked=False)
            elif op == "insert_checkpoint":
                self._op_insert_checkpoint(graph, operation)
            elif op == "reroute_dependency":
                self._op_reroute_dependency(graph, operation)
            else:
                raise ValueError(f"unsupported patch operation: {op}")

    def _find_node(self, graph: ActionGraph, node_id: str) -> ActionNode:
        for node in graph.nodes:
            if str(node.node_id) == str(node_id):
                return node
        raise ValueError(f"node not found: {node_id}")

    def _op_update_node(self, graph: ActionGraph, operation: PatchOperation) -> None:
        node_id = str(operation.target or operation.value.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("update_node requires target or value.node_id")
        node = self._find_node(graph, node_id)
        if not bool(node.editable):
            raise ValueError(f"node is locked and cannot be patched: {node_id}")
        updates = dict(operation.value or {})
        updates.pop("node_id", None)
        fields = _field_names(ActionNode)
        updates = {k: v for k, v in updates.items() if k in fields}
        node_updates = node.model_copy(update=updates) if hasattr(node, "model_copy") else deepcopy(node)
        if not hasattr(node, "model_copy"):
            for key, value in updates.items():
                setattr(node_updates, key, value)
        index = graph.nodes.index(node)
        graph.nodes[index] = node_updates

    def _op_add_node(self, graph: ActionGraph, operation: PatchOperation) -> None:
        node = _build_model(ActionNode, **dict(operation.value or {}))
        if any(str(existing.node_id) == str(node.node_id) for existing in graph.nodes):
            raise ValueError(f"duplicate node_id: {node.node_id}")
        graph.nodes.append(node)

    def _op_remove_node(self, graph: ActionGraph, operation: PatchOperation) -> None:
        node_id = str(operation.target or operation.value.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("remove_node requires a target")
        graph.nodes = [node for node in graph.nodes if str(node.node_id) != node_id]
        graph.edges = [edge for edge in graph.edges if str(edge.from_node) != node_id and str(edge.to_node) != node_id]
        graph.artifacts = [artifact for artifact in graph.artifacts if str(artifact.node_id) != node_id]

    def _op_add_edge(self, graph: ActionGraph, operation: PatchOperation) -> None:
        edge = _build_model(ActionEdge, **dict(operation.value or {}))
        if any(str(existing.edge_id) == str(edge.edge_id) for existing in graph.edges):
            raise ValueError(f"duplicate edge_id: {edge.edge_id}")
        graph.edges.append(edge)

    def _op_remove_edge(self, graph: ActionGraph, operation: PatchOperation) -> None:
        edge_id = str(operation.target or operation.value.get("edge_id") or "").strip()
        if not edge_id:
            raise ValueError("remove_edge requires a target")
        graph.edges = [edge for edge in graph.edges if str(edge.edge_id) != edge_id]

    def _op_rebind_artifact(self, graph: ActionGraph, operation: PatchOperation) -> None:
        artifact_id = str(operation.target or operation.value.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("rebind_artifact requires an artifact target")
        artifact = None
        for candidate in graph.artifacts:
            if str(candidate.artifact_id) == artifact_id:
                artifact = candidate
                break
        if artifact is None:
            raise ValueError(f"artifact not found: {artifact_id}")
        updates = dict(operation.value or {})
        updates.pop("artifact_id", None)
        fields = _field_names(ArtifactRef)
        updates = {k: v for k, v in updates.items() if k in fields}
        updated = artifact.model_copy(update=updates) if hasattr(artifact, "model_copy") else deepcopy(artifact)
        if not hasattr(artifact, "model_copy"):
            for key, value in updates.items():
                setattr(updated, key, value)
        index = graph.artifacts.index(artifact)
        graph.artifacts[index] = updated

    def _op_lock_node(self, graph: ActionGraph, operation: PatchOperation, *, locked: bool) -> None:
        node_id = str(operation.target or operation.value.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("lock/unlock requires a node target")
        node = self._find_node(graph, node_id)
        node.editable = not locked

    def _op_insert_checkpoint(self, graph: ActionGraph, operation: PatchOperation) -> None:
        value = dict(operation.value or {})
        after_node = str(value.get("after_node") or "").strip()
        target_node = str(operation.target or value.get("target_node") or "").strip()
        if not after_node or not target_node:
            raise ValueError("insert_checkpoint requires after_node and target node")
        if not any(str(node.node_id) == after_node for node in graph.nodes):
            raise ValueError(f"after_node not found: {after_node}")
        if not any(str(node.node_id) == target_node for node in graph.nodes):
            raise ValueError(f"target node not found: {target_node}")
        existing = {str(node.node_id) for node in graph.nodes}
        base_id = str(value.get("node_id") or f"checkpoint_{target_node}")
        node_id = base_id
        suffix = 2
        while node_id in existing:
            node_id = f"{base_id}_{suffix}"
            suffix += 1
        insert_index = next(i for i, node in enumerate(graph.nodes) if str(node.node_id) == after_node) + 1
        checkpoint = _build_model(
            ActionNode,
            node_id=node_id,
            kind="human",
            title=str(value.get("title") or "Review Checkpoint"),
            action_type=str(value.get("action_type") or "review_checkpoint"),
            tool_name=None,
            status="planned",
            depends_on=[after_node],
            inputs=dict(value.get("inputs") or {}),
            outputs=dict(value.get("outputs") or {}),
            checks=list(value.get("checks") or ["manual review"]),
            artifact_refs=list(value.get("artifact_refs") or []),
            owner=str(value.get("owner") or "human"),
            editable=True,
            notes=str(value.get("notes") or "Inserted by patch"),
        )
        graph.nodes.insert(insert_index, checkpoint)
        target = self._find_node(graph, target_node)
        target.depends_on = [checkpoint.node_id if str(dep) == after_node else dep for dep in target.depends_on]
        if checkpoint.node_id not in target.depends_on:
            target.depends_on = [checkpoint.node_id]
        existing_edges = {(str(edge.from_node), str(edge.to_node)) for edge in graph.edges}
        if (after_node, checkpoint.node_id) not in existing_edges:
            graph.edges.append(
                _build_model(
                    ActionEdge,
                    edge_id=f"edge-{len(graph.edges) + 1:04d}",
                    from_node=after_node,
                    to_node=checkpoint.node_id,
                    type="approval",
                    label="review checkpoint",
                )
            )
        if (checkpoint.node_id, target_node) not in existing_edges:
            graph.edges.append(
                _build_model(
                    ActionEdge,
                    edge_id=f"edge-{len(graph.edges) + 2:04d}",
                    from_node=checkpoint.node_id,
                    to_node=target_node,
                    type="control",
                    label="resume workflow",
                )
            )

    def _op_reroute_dependency(self, graph: ActionGraph, operation: PatchOperation) -> None:
        node_id = str(operation.target or operation.value.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("reroute_dependency requires a target node")
        node = self._find_node(graph, node_id)
        if not bool(node.editable):
            raise ValueError(f"node is locked and cannot be patched: {node_id}")
        depends_on = operation.value.get("depends_on")
        if not isinstance(depends_on, list):
            raise ValueError("reroute_dependency requires depends_on list")
        node.depends_on = [str(item).strip() for item in depends_on if str(item).strip()]


def create_default_store() -> MockExecutorStore:
    return MockExecutorStore()
