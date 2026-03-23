from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .bridge import discover_tools
from .runtime_profiles import resolve_tool_runtime_profile


@dataclass(frozen=True)
class ToolContract:
    tool_name: str
    domains: List[str]
    capabilities: List[str]
    required_inputs: List[str]
    produced_outputs: List[str]
    runtime_profile: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyRule:
    rule_name: str
    target_tool: str
    depends_on: List[str]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityExpansionRule:
    rule_name: str
    domain: str
    when_any: List[str]
    select_tools: List[str]
    reason: str
    dependency_overrides: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_TOOL_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "identify_sequences": {
        "domains": ["prostate", "brain", "cardiac"],
        "capabilities": ["intake", "sequence_inventory"],
        "required_inputs": ["dicom_case_dir"],
        "produced_outputs": ["mapping", "series_inventory_path", "dicom_meta_path", "dicom_headers_index_path"],
        "notes": ["Always the first materialized node for radiology workups."],
    },
    "register_to_reference": {
        "domains": ["prostate"],
        "capabilities": ["register"],
        "required_inputs": ["fixed", "moving"],
        "produced_outputs": ["resampled_path", "transform_path"],
        "notes": ["Alignment prerequisite for most prostate expansion rules."],
    },
    "segment_prostate": {
        "domains": ["prostate"],
        "capabilities": ["segment", "prostate_segmentation"],
        "required_inputs": ["t2w_ref"],
        "produced_outputs": ["prostate_mask_path", "zone_mask_path", "t2w_input_path"],
        "notes": ["Containerized GPU-capable tool on cp082."],
    },
    "detect_lesion_candidates": {
        "domains": ["prostate"],
        "capabilities": ["lesion", "lesion_detection"],
        "required_inputs": ["prostate_mask_path", "resampled_path"],
        "produced_outputs": ["lesion_candidates_path"],
        "notes": ["Capability expansion node, not a hard-coded terminal step."],
    },
    "extract_roi_features": {
        "domains": ["prostate", "brain", "cardiac"],
        "capabilities": ["roi_features", "feature_extraction"],
        "required_inputs": ["segmentation_path"],
        "produced_outputs": ["feature_table_path"],
        "notes": ["Can consume lesion or segmentation outputs depending on domain."],
    },
    "package_vlm_evidence": {
        "domains": ["prostate", "brain", "cardiac"],
        "capabilities": ["evidence", "report_prep"],
        "required_inputs": ["case_state_path"],
        "produced_outputs": ["vlm_evidence_path"],
        "notes": ["Turns upstream analysis into report-ready evidence."],
    },
    "generate_report": {
        "domains": ["prostate", "brain", "cardiac"],
        "capabilities": ["report"],
        "required_inputs": ["case_state_path"],
        "produced_outputs": ["report_path"],
        "notes": ["Final report synthesis node."],
    },
    "brats_mri_segmentation": {
        "domains": ["brain"],
        "capabilities": ["segment", "tumor_segmentation"],
        "required_inputs": ["dicom_case_dir"],
        "produced_outputs": ["segmentation_path"],
        "notes": ["Brain segmentation expansion node."],
    },
    "classify_brain_glioma_grade": {
        "domains": ["brain"],
        "capabilities": ["classify", "grade"],
        "required_inputs": ["feature_table_path"],
        "produced_outputs": ["classification_path"],
        "notes": ["Consumes ROI features rather than raw model output."],
    },
    "segment_cardiac_cine": {
        "domains": ["cardiac"],
        "capabilities": ["segment"],
        "required_inputs": ["cine_ref"],
        "produced_outputs": ["seg_path"],
        "notes": ["Cardiac cine segmentation node."],
    },
    "classify_cardiac_cine_disease": {
        "domains": ["cardiac"],
        "capabilities": ["classify"],
        "required_inputs": ["feature_table_path"],
        "produced_outputs": ["classification_path"],
        "notes": ["Consumes segmentation-derived features."],
    },
}


DOMAIN_RULEBOOK: Dict[str, Dict[str, Any]] = {
    "prostate": {
        "entry_tools": ["identify_sequences"],
        "tool_order": [
            "identify_sequences",
            "register_to_reference",
            "segment_prostate",
            "detect_lesion_candidates",
            "extract_roi_features",
            "package_vlm_evidence",
            "generate_report",
        ],
        "dependency_rules": [
            {"rule_name": "prostate-register-after-intake", "target_tool": "register_to_reference", "depends_on": ["identify_sequences"], "reason": "registration follows sequence discovery"},
            {"rule_name": "prostate-segment-after-register", "target_tool": "segment_prostate", "depends_on": ["register_to_reference"], "reason": "segmentation follows alignment"},
            {"rule_name": "prostate-lesion-after-segment", "target_tool": "detect_lesion_candidates", "depends_on": ["segment_prostate"], "reason": "lesion detection consumes segmentation"},
            {"rule_name": "prostate-roi-after-lesion", "target_tool": "extract_roi_features", "depends_on": ["detect_lesion_candidates"], "reason": "ROI features consume lesion candidates"},
            {"rule_name": "prostate-report-after-evidence", "target_tool": "package_vlm_evidence", "depends_on": ["extract_roi_features", "detect_lesion_candidates", "segment_prostate"], "reason": "evidence packaging follows the richest available analysis step"},
            {"rule_name": "prostate-report-final", "target_tool": "generate_report", "depends_on": ["package_vlm_evidence"], "reason": "report follows packaged evidence"},
        ],
        "capability_rules": [
            {"rule_name": "prostate-base-alignment", "when_any": ["register", "segment", "report", "full_pipeline", "lesion", "classify", "roi_features"], "select_tools": ["register_to_reference", "segment_prostate"], "reason": "base prostate workup needs alignment and gland segmentation"},
            {"rule_name": "prostate-lesion-expansion", "when_any": ["lesion", "classify", "roi_features"], "select_tools": ["detect_lesion_candidates", "extract_roi_features"], "reason": "lesion or feature-analysis requests expand into candidate detection and ROI feature extraction"},
            {"rule_name": "prostate-report-expansion", "when_any": ["report", "full_pipeline"], "select_tools": ["package_vlm_evidence", "generate_report"], "reason": "report requests require evidence packaging and final report synthesis"},
        ],
    },
    "brain": {
        "entry_tools": ["identify_sequences"],
        "tool_order": [
            "identify_sequences",
            "brats_mri_segmentation",
            "extract_roi_features",
            "classify_brain_glioma_grade",
            "package_vlm_evidence",
            "generate_report",
        ],
        "dependency_rules": [
            {"rule_name": "brain-segment-after-intake", "target_tool": "brats_mri_segmentation", "depends_on": ["identify_sequences"], "reason": "brain segmentation follows sequence discovery"},
            {"rule_name": "brain-roi-after-segment", "target_tool": "extract_roi_features", "depends_on": ["brats_mri_segmentation"], "reason": "ROI features consume segmentation output"},
            {"rule_name": "brain-classify-after-roi", "target_tool": "classify_brain_glioma_grade", "depends_on": ["extract_roi_features"], "reason": "glioma grading consumes ROI features"},
            {"rule_name": "brain-report-after-evidence", "target_tool": "package_vlm_evidence", "depends_on": ["classify_brain_glioma_grade", "extract_roi_features", "brats_mri_segmentation"], "reason": "evidence packaging follows the richest available analysis step"},
            {"rule_name": "brain-report-final", "target_tool": "generate_report", "depends_on": ["package_vlm_evidence"], "reason": "report follows packaged evidence"},
        ],
        "capability_rules": [
            {"rule_name": "brain-segmentation", "when_any": ["segment", "classify", "report", "full_pipeline"], "select_tools": ["brats_mri_segmentation"], "reason": "brain requests usually start with tumor segmentation"},
            {"rule_name": "brain-roi-and-classify", "when_any": ["classify", "full_pipeline"], "select_tools": ["extract_roi_features", "classify_brain_glioma_grade"], "reason": "glioma classification requires ROI features"},
            {"rule_name": "brain-report-expansion", "when_any": ["report", "full_pipeline"], "select_tools": ["package_vlm_evidence", "generate_report"], "reason": "report requests require evidence packaging and final report synthesis"},
        ],
    },
    "cardiac": {
        "entry_tools": ["identify_sequences"],
        "tool_order": [
            "identify_sequences",
            "segment_cardiac_cine",
            "classify_cardiac_cine_disease",
            "package_vlm_evidence",
            "generate_report",
        ],
        "dependency_rules": [
            {"rule_name": "cardiac-segment-after-intake", "target_tool": "segment_cardiac_cine", "depends_on": ["identify_sequences"], "reason": "cardiac segmentation follows sequence discovery"},
            {"rule_name": "cardiac-classify-after-seg", "target_tool": "classify_cardiac_cine_disease", "depends_on": ["segment_cardiac_cine"], "reason": "disease classification consumes segmentation output"},
            {"rule_name": "cardiac-report-after-evidence", "target_tool": "package_vlm_evidence", "depends_on": ["classify_cardiac_cine_disease", "segment_cardiac_cine"], "reason": "evidence packaging follows the richest available analysis step"},
            {"rule_name": "cardiac-report-final", "target_tool": "generate_report", "depends_on": ["package_vlm_evidence"], "reason": "report follows packaged evidence"},
        ],
        "capability_rules": [
            {"rule_name": "cardiac-segmentation", "when_any": ["segment", "classify", "report", "full_pipeline"], "select_tools": ["segment_cardiac_cine"], "reason": "cardiac requests usually start with cine segmentation"},
            {"rule_name": "cardiac-classification", "when_any": ["classify", "full_pipeline"], "select_tools": ["classify_cardiac_cine_disease"], "reason": "disease classification follows segmentation"},
            {"rule_name": "cardiac-report-expansion", "when_any": ["report", "full_pipeline"], "select_tools": ["package_vlm_evidence", "generate_report"], "reason": "report requests require evidence packaging and final report synthesis"},
        ],
    },
}


def _normalized_selected_tools(selected_tools: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for tool in selected_tools:
        tool_name = str(tool).strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        out.append(tool_name)
    return out


def get_tool_contract(tool_name: str) -> Dict[str, Any]:
    tool_key = str(tool_name or "").strip()
    contract = dict(DEFAULT_TOOL_CONTRACTS.get(tool_key, {}))
    if not contract:
        return {
            "tool_name": tool_key,
            "domains": [],
            "capabilities": [],
            "required_inputs": [],
            "produced_outputs": [],
            "runtime_profile": "control-plane",
            "notes": ["No explicit compiler metadata found; fallback contract synthesized."],
        }
    contract["tool_name"] = tool_key
    contract["runtime_profile"] = str(resolve_tool_runtime_profile(tool_key).get("profile_id") or "control-plane")
    return contract


def get_domain_rulebook(domain: str) -> Dict[str, Any]:
    domain_key = str(domain or "").strip().lower() or "prostate"
    rulebook = dict(DOMAIN_RULEBOOK.get(domain_key, DOMAIN_RULEBOOK["prostate"]))
    rulebook["domain"] = domain_key
    return rulebook


def build_compiler_input(
    *,
    intent_spec: Dict[str, Any],
    selected_tools: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    domain = str(intent_spec.get("domain") or "prostate").strip().lower() or "prostate"
    rulebook = get_domain_rulebook(domain)
    contract_names = _normalized_selected_tools(selected_tools or rulebook.get("tool_order") or [])
    contracts = [get_tool_contract(tool_name) for tool_name in contract_names if tool_name]
    available_registry_tools = {
        str(tool.get("name") or "").strip()
        for tool in discover_tools()
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    }
    for contract in contracts:
        tool_name = str(contract.get("tool_name") or "").strip()
        contract["available_in_registry"] = tool_name in available_registry_tools
    return {
        "intent_spec": dict(intent_spec),
        "tool_contracts": contracts,
        "dependency_rules": [dict(rule) for rule in rulebook.get("dependency_rules") or []],
        "capability_expansion_rules": [dict(rule) for rule in rulebook.get("capability_rules") or []],
        "tool_order": list(rulebook.get("tool_order") or []),
        "entry_tools": list(rulebook.get("entry_tools") or []),
    }
