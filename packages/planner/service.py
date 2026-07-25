from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from packages.schemas import ActionGraph, ActionNode, CaseState, ExecutionPatch, PatchOperation
from packages.tools.compiler_metadata import DEFAULT_TOOL_CONTRACTS, DOMAIN_RULEBOOK
from packages.tools import discover_capabilities, discover_domains, discover_tools, resolve_tool_runtime_profile

from .compiler import build_intent_spec as build_compiler_intent_spec
from .compiler import compile_intent_spec
from .client import OpenAICompatibleClient
from .contracts import IntentConstraint, IntentPreference, IntentSpec, PlannerMetadata, PlannerReply, PlannerResult
from .validator import validate_graph_semantics


_PROMPT_ASSET_DIR = Path(__file__).with_name("prompt_assets")


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw not in {"0", "false", "off", "no", ""}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _graph_summary(graph: ActionGraph, case_state: CaseState) -> str:
    lines = [
        f"Case domain: {case_state.domain}",
        f"Case id: {case_state.case_id}",
        f"Input root: {case_state.input_root}",
        f"Graph status: {graph.status}",
        f"Root goal: {graph.root_goal}",
        "Nodes:",
    ]
    for node in graph.nodes:
        lines.append(
            f"- {node.node_id}: {node.title} | action={node.action_type} | status={node.status} | depends_on={','.join(node.depends_on or []) or 'none'}"
        )
    if case_state.available_modalities:
        lines.append(f"Available modalities: {', '.join(case_state.available_modalities)}")
    if case_state.sequence_index:
        lines.append(f"Sequence index keys: {', '.join(sorted(case_state.sequence_index.keys()))}")
    return "\n".join(lines)


def _extract_final_reply(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""

    final_match = re.search(r"<final>(.*?)</final>", text, flags=re.IGNORECASE | re.DOTALL)
    if final_match:
        text = final_match.group(1).strip()

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    text = re.sub(r"^\s*final answer\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\s*answer\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _has_final_wrapper(raw_text: str) -> bool:
    return bool(re.search(r"<final>.*?</final>", str(raw_text or ""), flags=re.IGNORECASE | re.DOTALL))


def _load_prompt_asset(name: str) -> str:
    path = _PROMPT_ASSET_DIR / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _planner_persona_prompt() -> str:
    parts = [_load_prompt_asset("SOUL.md"), _load_prompt_asset("AGENT.md")]
    return "\n\n".join(part for part in parts if part).strip()


def _looks_like_hidden_reasoning(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    suspicious_markers = [
        "the user wants",
        "the user is asking",
        "the user asked",
        "looking at the existing nodes",
        "looking at the previous conversation",
        "let me check",
        "i need to",
        "first, i need",
        "the system says",
        "need to respond",
        "check if the user",
        "make sure to",
        "wait,",
        "all the nodes are in the correct order",
        "the key here is",
        "response should be",
        "the user's question",
        "the next step is",
    ]
    hit_count = sum(1 for marker in suspicious_markers if marker in lowered)
    return hit_count >= 2


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def _known_tool_names() -> set[str]:
    discovered = {
        str(tool.get("name") or "").strip()
        for tool in discover_tools()
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    }
    discovered.update(str(tool_name) for tool_name in DEFAULT_TOOL_CONTRACTS)
    return discovered


def _tool_metadata_map() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for tool in discover_tools():
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            out[name] = dict(tool)
    return out


def _domain_catalog() -> Dict[str, Dict[str, Any]]:
    catalog = discover_domains()
    return {str(name): dict(payload) for name, payload in catalog.items() if isinstance(payload, dict)}


def _domain_capability_map() -> Dict[str, List[str]]:
    raw = discover_capabilities().get("domain_capabilities") or {}
    out: Dict[str, List[str]] = {}
    for domain, capabilities in raw.items():
        out[str(domain)] = [str(item) for item in (capabilities or []) if str(item).strip()]
    for domain, rulebook in DOMAIN_RULEBOOK.items():
        current = set(out.get(domain, []))
        tool_order = {str(tool_name).strip() for tool_name in rulebook.get("tool_order") or [] if str(tool_name).strip()}
        if "extract_roi_features" in tool_order:
            current.add("roi_features")
        if "reconstruct_grappa" in tool_order:
            # The engine registry lists reconstruct_grappa with no capabilities, so
            # discover_capabilities() cannot surface it; the rulebook is the thing that
            # actually knows the domain can reconstruct.
            current.add("reconstruct")
        out[domain] = sorted(current)
    return out


def _dedupe_preserve(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(str(item) for item in items if str(item).strip()))


def _normalized_request_text(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "").strip())


def _intent_type_from_message(message: str, case_state: Optional[CaseState] = None) -> str:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return "reply_request"
    if any(token in lowered for token in ["pause before segmentation", "review before segmentation", "checkpoint before segmentation"]):
        return "patch_request"
    if "before segmentation" in lowered and any(token in lowered for token in ["pause", "review", "checkpoint", "approve"]):
        return "patch_request"
    if any(token in lowered for token in ["pause", "checkpoint", "review"]) and "seg" in lowered:
        return "patch_request"
    graph_tokens = ["inspect", "analyze", "plan", "draft", "workup", "workflow", "review", "report", "segment", "classify", "register"]
    domain_tokens = ["prostate", "brain", "glioma", "tumor", "cardiac", "cine", "heart", "adc", "t2", "case"]
    if any(token in lowered for token in graph_tokens) and (
        any(token in lowered for token in domain_tokens) or str(getattr(case_state, "domain", "") or "").strip()
    ):
        return "graph_request"
    return "reply_request"


def _intent_label_from_type(intent_type: str) -> str:
    if intent_type == "graph_request":
        return "graph_domain_workup"
    if intent_type == "patch_request":
        return "patch_review_before_segmentation"
    return "reply"


def _explicit_requested_capabilities(message: str, *, available_capabilities: Sequence[str]) -> List[str]:
    lowered = str(message or "").strip().lower()
    available = set(str(item) for item in available_capabilities)
    requested: List[str] = []

    if any(token in lowered for token in ["full pipeline", "workup", "workflow", "end-to-end", "end to end"]) and "full_pipeline" in available:
        requested.append("full_pipeline")
    if any(token in lowered for token in ["register", "registration", "align", "alignment"]) and "register" in available:
        requested.append("register")
    if any(
        token in lowered
        for token in ["k-space", "kspace", "k space", "grappa", "reconstruct", "reconstruction", "raw multi-coil", "raw multicoil", "hdf5", ".h5"]
    ) and "reconstruct" in available:
        requested.append("reconstruct")
    if any(token in lowered for token in ["segment", "segmentation", "mask"]) and "segment" in available:
        requested.append("segment")
    if any(token in lowered for token in ["classify", "classification", "grade", "diagnosis", "disease"]) and "classify" in available:
        requested.append("classify")
    if any(token in lowered for token in ["lesion", "candidate"]) and "lesion" in available:
        requested.append("lesion")
    if any(token in lowered for token in ["feature analysis", "feature extract", "feature extraction", "roi feature", "roi analysis", "radiomics"]) and "roi_features" in available:
        requested.append("roi_features")
    if any(token in lowered for token in ["report", "summary", "impression"]) and "report" in available:
        requested.append("report")
    if any(token in lowered for token in ["qa", "question answer", "search"]) and "qa" in available:
        requested.append("qa")
    if any(token in lowered for token in ["sandbox", "custom analysis", "custom"]) and "custom_analysis" in available:
        requested.append("custom_analysis")

    return _dedupe_preserve(requested)


def _inferred_requested_capabilities(
    message: str,
    *,
    domain: str,
    available_capabilities: Sequence[str],
    explicit_requested_capabilities: Sequence[str],
) -> List[str]:
    lowered = str(message or "").strip().lower()
    available = set(str(item) for item in available_capabilities)
    explicit = set(str(item) for item in explicit_requested_capabilities)
    inferred: List[str] = []

    workflow_signal = any(token in lowered for token in ["inspect", "analyze", "plan", "draft", "workup", "workflow"])
    if workflow_signal and "full_pipeline" in available and "full_pipeline" not in explicit:
        inferred.append("full_pipeline")
    if not explicit and "report" in available and any(token in lowered for token in ["report", "summary", "impression"]):
        inferred.append("report")
    if not explicit and not inferred:
        if "full_pipeline" in available:
            inferred.append("full_pipeline")
        elif "report" in available:
            inferred.append("report")
        elif "segment" in available:
            inferred.append("segment")
    if domain in {"brain", "cardiac"} and "segment" in available and "segment" not in explicit:
        if any(token in lowered for token in ["segment", "mask"]):
            inferred.append("segment")

    return _dedupe_preserve([item for item in inferred if item in available])


def _extract_intent_constraints(message: str, *, intent_type: str) -> List[IntentConstraint]:
    lowered = str(message or "").strip().lower()
    constraints: List[IntentConstraint] = []

    if intent_type == "patch_request":
        if any(token in lowered for token in ["pause", "review", "checkpoint", "approve"]) and "seg" in lowered:
            constraints.append(
                IntentConstraint(
                    kind="ordering",
                    value="before_segmentation",
                    required=True,
                    source_text=str(message or ""),
                )
            )
            if "review" in lowered or "approve" in lowered:
                constraints.append(
                    IntentConstraint(
                        kind="approval",
                        value="human_review_required",
                        required=True,
                        source_text=str(message or ""),
                    )
                )

    for token, capability in (
        ("no report", "report"),
        ("without report", "report"),
        ("no segmentation", "segment"),
        ("without segmentation", "segment"),
        ("don't segment", "segment"),
        ("do not segment", "segment"),
        ("don't classify", "classify"),
        ("do not classify", "classify"),
        ("don't register", "register"),
        ("do not register", "register"),
    ):
        if token in lowered:
            constraints.append(
                IntentConstraint(
                    kind="exclude_capability",
                    value=capability,
                    required=True,
                    source_text=str(message or ""),
                )
            )

    return constraints


def _extract_intent_preferences(message: str) -> List[IntentPreference]:
    lowered = str(message or "").strip().lower()
    preferences: List[IntentPreference] = []

    if any(token in lowered for token in ["short report", "brief", "concise"]):
        preferences.append(
            IntentPreference(
                kind="report_length",
                value="short",
                source_text=str(message or ""),
            )
        )
    if any(token in lowered for token in ["detailed", "comprehensive", "thorough"]):
        preferences.append(
            IntentPreference(
                kind="report_length",
                value="detailed",
                source_text=str(message or ""),
            )
        )
    if any(token in lowered for token in ["high confidence", "careful", "robust"]):
        preferences.append(
            IntentPreference(
                kind="risk_tolerance",
                value="conservative",
                source_text=str(message or ""),
            )
        )

    return preferences


def _normalize_capability_list(values: Sequence[Any], *, available_capabilities: Sequence[str]) -> List[str]:
    available = {str(item).strip() for item in available_capabilities if str(item).strip()}
    return _dedupe_preserve(str(item).strip() for item in values if str(item).strip() in available)


def _merge_constraints(*constraint_groups: Sequence[IntentConstraint]) -> List[IntentConstraint]:
    merged: List[IntentConstraint] = []
    seen: set[tuple[str, str]] = set()
    for group in constraint_groups:
        for constraint in group:
            key = (str(constraint.kind), str(constraint.value))
            if key in seen:
                continue
            seen.add(key)
            merged.append(constraint)
    return merged


def _merge_preferences(*preference_groups: Sequence[IntentPreference]) -> List[IntentPreference]:
    merged: List[IntentPreference] = []
    seen: set[tuple[str, str]] = set()
    for group in preference_groups:
        for preference in group:
            key = (str(preference.kind), str(preference.value))
            if key in seen:
                continue
            seen.add(key)
            merged.append(preference)
    return merged


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced_match:
        text = fenced_match.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _build_intent_spec(
    user_message: str,
    case_state: CaseState,
    graph: ActionGraph,
) -> IntentSpec:
    intent_type = _intent_type_from_message(user_message, case_state)
    domain = _domain_from_message(user_message, case_state, _domain_catalog())
    available_capabilities = _domain_capability_map().get(domain, [])
    explicit_requested_capabilities = _explicit_requested_capabilities(user_message, available_capabilities=available_capabilities)
    inferred_requested_capabilities = _inferred_requested_capabilities(
        user_message,
        domain=domain,
        available_capabilities=available_capabilities,
        explicit_requested_capabilities=explicit_requested_capabilities,
    )
    normalized_request = _normalized_request_text(user_message)
    intent_id = f"intent-{_normalize_text(domain)}-{_normalize_text(normalized_request)[:48] or 'reply'}"
    constraints = _extract_intent_constraints(user_message, intent_type=intent_type)
    preferences = _extract_intent_preferences(user_message)
    notes: List[str] = []
    if case_state.domain and str(case_state.domain).strip().lower() == domain:
        notes.append("domain aligned with case_state")
    elif case_state.domain:
        notes.append(f"domain inferred as {domain} from request/case context")
    if intent_type == "graph_request" and "full_pipeline" in inferred_requested_capabilities:
        notes.append("workflow language expanded to full_pipeline")
    if intent_type == "patch_request":
        notes.append("patch intent anchored on pre-segmentation review")

    patch_anchor = None
    target_node_id = None
    if intent_type == "patch_request":
        patch_anchor = "before_segmentation"
        for node in graph.nodes:
            tool_name = str(node.tool_name or "")
            action_type = str(node.action_type or "")
            if tool_name.startswith("segment_") or action_type.startswith("segment_") or tool_name == "brats_mri_segmentation" or action_type == "brats_mri_segmentation":
                target_node_id = str(node.node_id)
                break

    return IntentSpec(
        intent_id=intent_id,
        intent_type=intent_type,
        domain=domain,
        normalized_request=normalized_request,
        explicit_requested_capabilities=explicit_requested_capabilities,
        inferred_requested_capabilities=inferred_requested_capabilities,
        constraints=constraints,
        preferences=preferences,
        target_graph_id=str(graph.graph_id) if graph.graph_id else None,
        target_node_id=target_node_id,
        patch_anchor=patch_anchor,
        notes=notes,
    )


def _domain_from_message(message: str, case_state: CaseState, domain_catalog: Dict[str, Dict[str, Any]]) -> str:
    lowered = str(message or "").strip().lower()
    if any(token in lowered for token in ["glioma", "tumor", "brain", "brats"]):
        return "brain"
    if any(token in lowered for token in ["cardiac", "heart", "cine", "lv", "rv"]):
        return "cardiac"
    if any(token in lowered for token in ["prostate", "pirads", "adc", "dwi", "t2w"]):
        return "prostate"
    case_domain = str(case_state.domain or "").strip().lower()
    if case_domain in domain_catalog:
        return case_domain
    return "prostate"


def _runtime_hints_for_tools(tool_names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    hints: Dict[str, Dict[str, Any]] = {}
    for tool_name in tool_names:
        try:
            profile = resolve_tool_runtime_profile(str(tool_name))
        except Exception:
            continue
        hints[str(tool_name)] = {
            "profile_id": profile.get("profile_id"),
            "launcher": profile.get("launcher"),
            "gpu": profile.get("gpu"),
            "ssh_host": profile.get("ssh_host"),
        }
    return hints


def _find_node_by_match(graph: ActionGraph, *, action_type: Optional[str] = None, tool_name: Optional[str] = None) -> Optional[ActionNode]:
    for node in graph.nodes:
        if action_type and str(node.action_type) == action_type:
            return node
        if tool_name and str(node.tool_name or "") == tool_name:
            return node
    return None


def _build_review_patch(user_message: str, graph: ActionGraph) -> Tuple[Optional[ExecutionPatch], List[str]]:
    warnings: List[str] = []
    segment_node = next(
        (
            node
            for node in graph.nodes
            if str(node.tool_name or "").startswith("segment_")
            or str(node.action_type).startswith("segment_")
            or str(node.tool_name or "") == "brats_mri_segmentation"
            or str(node.action_type) == "brats_mri_segmentation"
        ),
        None,
    )
    register_node = _find_node_by_match(graph, action_type="register_to_reference", tool_name="register_to_reference")
    if segment_node is None:
        warnings.append("Patch target could not be resolved because no segmentation node exists in the active graph.")
        return None, warnings
    after_node = register_node.node_id if register_node is not None else str(segment_node.depends_on[0]) if segment_node.depends_on else ""
    if not after_node:
        warnings.append("Patch insertion point could not be resolved because the segmentation node has no upstream dependency.")
        return None, warnings
    reason = str(user_message or "").strip() or "Insert a review checkpoint before segmentation."
    patch = ExecutionPatch(
        patch_id=f"planner-patch-v{int(graph.version or 0)}",
        graph_id=graph.graph_id,
        author_type="supervisor",
        author_id="brain",
        timestamp=_utc_now(),
        reason=reason,
        applies_to_version=graph.version,
        result="preview",
        operations=[
            PatchOperation(
                op="insert_checkpoint",
                target=segment_node.node_id,
                value={
                    "after_node": after_node,
                    "title": "Review Upstream Output",
                    "action_type": "review_checkpoint",
                    "checks": ["manual review", "approval recorded"],
                    "owner": "human",
                    "notes": "Inserted by planner from operator follow-up request.",
                },
            )
        ],
    )
    return patch, warnings


def _validate_graph(graph: ActionGraph, known_tools: set[str], *, domain_tools: Optional[set[str]] = None) -> List[str]:
    errors: List[str] = []
    node_ids = [str(node.node_id) for node in graph.nodes]
    node_set = set(node_ids)
    if len(node_set) != len(node_ids):
        errors.append("graph contains duplicate node_id values")
    for node in graph.nodes:
        missing_deps = [dep for dep in node.depends_on if str(dep) not in node_set]
        if missing_deps:
            errors.append(f"node {node.node_id} depends_on missing nodes: {', '.join(str(dep) for dep in missing_deps)}")
        if node.tool_name and str(node.tool_name) not in known_tools:
            errors.append(f"node {node.node_id} references unknown tool_name: {node.tool_name}")
        if node.tool_name and domain_tools is not None and str(node.tool_name) not in domain_tools:
            errors.append(f"node {node.node_id} uses tool outside domain catalog `{graph.domain}`: {node.tool_name}")
    for edge in graph.edges:
        if str(edge.from_node) not in node_set:
            errors.append(f"edge {edge.edge_id} has missing from_node: {edge.from_node}")
        if str(edge.to_node) not in node_set:
            errors.append(f"edge {edge.edge_id} has missing to_node: {edge.to_node}")
    return errors


def _validate_patch(graph: ActionGraph, patch: ExecutionPatch) -> List[str]:
    errors: List[str] = []
    node_set = {str(node.node_id) for node in graph.nodes}
    for operation in patch.operations:
        if str(operation.op) == "insert_checkpoint":
            target_node = str(operation.target or operation.value.get("target_node") or "").strip()
            after_node = str(operation.value.get("after_node") or "").strip()
            if target_node and target_node not in node_set:
                errors.append(f"patch target node does not exist: {target_node}")
            if after_node and after_node not in node_set:
                errors.append(f"patch insertion node does not exist: {after_node}")
    return errors


def _intent_from_message(message: str, case_state: Optional[CaseState] = None) -> str:
    return _intent_label_from_type(_intent_type_from_message(message, case_state))


def _default_reply(*, intent: str, mode: str, graph: Optional[ActionGraph] = None, patch: Optional[ExecutionPatch] = None) -> PlannerReply:
    if mode == "patch" and patch is not None:
        target = patch.operations[0].target if patch.operations else "the requested node"
        return PlannerReply(content=f"I proposed a preview patch that inserts a human review checkpoint before `{target}`.")
    if mode == "graph" and graph is not None:
        return PlannerReply(content=f"I proposed a draft {graph.domain} workflow with {len(graph.nodes)} nodes. Review it before execution.")
    if intent == "reply":
        return PlannerReply(content="I kept this as a chat reply because the request did not clearly ask for a new graph or a graph patch.")
    return PlannerReply(content="Planner proposal ready.")


class BrainService:
    def __init__(self) -> None:
        self.enabled = _env_flag("MRI_AGENT_V4_LLM_ENABLED", True)
        self.intent_llm_enabled = _env_flag("MRI_AGENT_V4_LLM_INTENT_ENABLED", False)
        self.base_url = str(os.environ.get("MRI_AGENT_V4_LLM_BASE_URL", "http://127.0.0.1:8000/v1")).rstrip("/")
        self.model = str(os.environ.get("MRI_AGENT_V4_LLM_MODEL", "Qwen/Qwen3-VL-30B-A3B-Thinking"))
        self.api_key = str(os.environ.get("MRI_AGENT_V4_LLM_API_KEY", "EMPTY"))
        self.timeout_s = float(os.environ.get("MRI_AGENT_V4_LLM_TIMEOUT_S", "20"))
        self.client = OpenAICompatibleClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            timeout_s=self.timeout_s,
        )
        self._known_tools = _known_tool_names()
        self._tool_metadata = _tool_metadata_map()
        self._domain_catalog = _domain_catalog()
        self._domain_capabilities = _domain_capability_map()
        self._planner_persona = _planner_persona_prompt()

    def health(self) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "base_url": self.base_url,
                "configured_model": self.model,
            }
        result = self.client.health()
        result["enabled"] = True
        return result

    def suggest_patch_reason(self, user_message: str) -> str | None:
        intent_type = _intent_type_from_message(user_message)
        if intent_type == "patch_request":
            return "Insert a human review checkpoint after registration before segmentation."
        return None

    def _llm_intent_spec(
        self,
        *,
        user_message: str,
        graph: ActionGraph,
        case_state: CaseState,
        heuristic_intent_spec: IntentSpec,
        available_capabilities: Sequence[str],
    ) -> Tuple[Optional[IntentSpec], str, Dict[str, Any]]:
        if not self.enabled or not self.intent_llm_enabled:
            return None, "disabled", {}

        system_prompt = "\n\n".join(
            part
            for part in [
                self._planner_persona,
                (
                    "You are producing a semantic planning intent for MRI_Agent_v4. "
                    "Do not produce a final graph, node list, patch operations, or chain-of-thought. "
                    "Return JSON only with keys: intent_type, domain, explicit_requested_capabilities, "
                    "inferred_requested_capabilities, constraints, preferences, notes. "
                    "Only use capabilities from the provided domain capability list."
                ),
            ]
            if part
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "system",
                "content": json.dumps(
                    {
                        "case_domain": case_state.domain,
                        "graph_domain": graph.domain,
                        "available_capabilities": list(available_capabilities),
                        "heuristic_intent_spec": heuristic_intent_spec.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            },
            {"role": "user", "content": str(user_message)},
        ]
        try:
            result = self.client.chat(messages, temperature=0.0, max_tokens=512)
        except Exception as exc:
            return None, "error", {"error": str(exc)}

        payload = _extract_json_object(str(result.get("content") or ""))
        if payload is None:
            return None, "llm_filtered", {"latency_ms": result.get("latency_ms"), "error": "intent JSON missing or invalid"}

        explicit_requested_capabilities = _normalize_capability_list(
            payload.get("explicit_requested_capabilities") or [],
            available_capabilities=available_capabilities,
        )
        inferred_requested_capabilities = _normalize_capability_list(
            payload.get("inferred_requested_capabilities") or [],
            available_capabilities=available_capabilities,
        )

        constraints: List[IntentConstraint] = []
        for item in payload.get("constraints") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            value = str(item.get("value") or "").strip()
            source_text = str(item.get("source_text") or user_message).strip()
            if not kind or not value:
                continue
            constraints.append(
                IntentConstraint(
                    kind=kind,
                    value=value,
                    required=bool(item.get("required", True)),
                    source_text=source_text,
                )
            )

        preferences: List[IntentPreference] = []
        for item in payload.get("preferences") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            value = str(item.get("value") or "").strip()
            source_text = str(item.get("source_text") or user_message).strip()
            if not kind or not value:
                continue
            preferences.append(IntentPreference(kind=kind, value=value, source_text=source_text))

        intent_type = str(payload.get("intent_type") or heuristic_intent_spec.intent_type)
        if intent_type not in {"graph_request", "patch_request", "reply_request"}:
            intent_type = heuristic_intent_spec.intent_type
        domain = str(payload.get("domain") or heuristic_intent_spec.domain).strip().lower() or heuristic_intent_spec.domain
        if domain not in DOMAIN_RULEBOOK:
            domain = heuristic_intent_spec.domain

        llm_intent_spec = IntentSpec(
            intent_id=heuristic_intent_spec.intent_id,
            intent_type=intent_type,
            domain=domain,
            normalized_request=heuristic_intent_spec.normalized_request,
            explicit_requested_capabilities=_dedupe_preserve(
                list(heuristic_intent_spec.explicit_requested_capabilities) + explicit_requested_capabilities
            ),
            inferred_requested_capabilities=_dedupe_preserve(
                list(heuristic_intent_spec.inferred_requested_capabilities) + inferred_requested_capabilities
            ),
            constraints=_merge_constraints(heuristic_intent_spec.constraints, constraints),
            preferences=_merge_preferences(heuristic_intent_spec.preferences, preferences),
            target_graph_id=heuristic_intent_spec.target_graph_id,
            target_node_id=heuristic_intent_spec.target_node_id,
            patch_anchor=heuristic_intent_spec.patch_anchor,
            notes=_dedupe_preserve(
                list(heuristic_intent_spec.notes)
                + [str(item).strip() for item in payload.get("notes") or [] if str(item).strip()]
                + ["semantic intent merged from LLM planner context"]
            ),
        )
        return llm_intent_spec, "llm", {"latency_ms": result.get("latency_ms")}

    def _llm_reply(
        self,
        *,
        user_message: str,
        graph: ActionGraph,
        case_state: CaseState,
        chat_history: List[Dict[str, str]],
    ) -> Tuple[Optional[PlannerReply], str, Dict[str, Any]]:
        if not self.enabled:
            return None, "disabled", {}
        system_prompt = "\n\n".join(
            part
            for part in [
                self._planner_persona,
                (
                    "You are the Brain for MRI_Agent_v4, a radiology MRI workstation planner. "
                    "You may see an already-generated structured graph or patch proposal elsewhere in the system. "
                    "Produce only a short operator-facing summary wrapped in <final>...</final>. "
                    "Do not reveal chain-of-thought or hidden reasoning. "
                    "Do not claim you executed tools."
                ),
            ]
            if part
        )
        history_tail = [item for item in chat_history[-6:] if isinstance(item, dict) and item.get("role") and item.get("content")]
        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "system", "content": _graph_summary(graph, case_state)})
        messages.extend({"role": str(item["role"]), "content": str(item["content"])} for item in history_tail)
        messages.append({"role": "user", "content": str(user_message)})
        try:
            result = self.client.chat(messages)
        except Exception as exc:
            return None, "error", {"error": str(exc)}
        raw_content = str(result.get("content") or "")
        if not _has_final_wrapper(raw_content):
            metadata = {"latency_ms": result.get("latency_ms")}
            return None, "llm_filtered", metadata
        content = _extract_final_reply(raw_content)
        if not content or _looks_like_hidden_reasoning(content):
            metadata = {"latency_ms": result.get("latency_ms")}
            return None, "llm_filtered", metadata
        return PlannerReply(content=content), "llm", {"latency_ms": result.get("latency_ms")}

    def reply(self, *, user_message: str, graph: ActionGraph, case_state: CaseState, chat_history: List[Dict[str, str]]) -> Dict[str, Any]:
        heuristic_intent_spec = _build_intent_spec(user_message, case_state, graph)
        intent_spec = heuristic_intent_spec
        intent = _intent_label_from_type(intent_spec.intent_type)
        warnings: List[str] = []
        proposal_graph: Optional[ActionGraph] = None
        proposal_patch: Optional[ExecutionPatch] = None

        if intent_spec.intent_type == "graph_request":
            domain = intent_spec.domain
            available_capabilities = self._domain_capabilities.get(domain, [])
            llm_intent_spec, llm_intent_status, llm_intent_meta = self._llm_intent_spec(
                user_message=user_message,
                graph=graph,
                case_state=case_state,
                heuristic_intent_spec=heuristic_intent_spec,
                available_capabilities=available_capabilities,
            )
            if llm_intent_spec is not None:
                intent_spec = llm_intent_spec
                domain = intent_spec.domain
                available_capabilities = self._domain_capabilities.get(domain, available_capabilities)
                intent = _intent_label_from_type(intent_spec.intent_type)
            requested_capabilities = _dedupe_preserve(
                list(intent_spec.explicit_requested_capabilities) + list(intent_spec.inferred_requested_capabilities)
            )
            compiler_intent_spec = build_compiler_intent_spec(
                user_message=user_message,
                case_state=case_state,
                graph=graph,
                domain=domain,
                requested_capabilities=requested_capabilities,
                available_capabilities=available_capabilities,
                available_tools=sorted(self._known_tools),
                intent=intent_spec.intent_type,
            )
            compiler_result = compile_intent_spec(compiler_intent_spec)
            proposal_graph = compiler_result.graph
            warnings.extend(compiler_result.warnings)
            domain_tools = set((self._domain_catalog.get(domain) or {}).get("tools") or []) | set(compiler_result.selected_tools)
            graph_errors = _validate_graph(proposal_graph, self._known_tools, domain_tools=domain_tools)
            graph_errors.extend(validate_graph_semantics(intent_spec, proposal_graph, compiler_result.compiler_input))
            selected_tools = [str(node.tool_name) for node in proposal_graph.nodes if node.tool_name]
            metadata = PlannerMetadata(
                intent=intent,
                source="hybrid" if llm_intent_spec is not None else "heuristic",
                llm_status="not_used",
                model=self.model,
                base_url=self.base_url,
                validation_passed=not graph_errors,
                validation_errors=graph_errors,
                fallback_used=True,
                extras={
                    "domain": domain,
                    "proposal_kind": "draft_graph",
                    "requested_capabilities": requested_capabilities,
                    "intent_spec": intent_spec.model_dump(mode="json"),
                    "compiler_input": compiler_result.compiler_input,
                    "compiler_selected_tools": compiler_result.selected_tools,
                    "compiler_applied_rules": compiler_result.applied_rules,
                    "available_domain_capabilities": available_capabilities,
                    "selected_tools": selected_tools,
                    "runtime_hints": _runtime_hints_for_tools(selected_tools),
                    "prompt_assets": ["SOUL.md", "AGENT.md"],
                    "intent_extraction_status": llm_intent_status,
                    "intent_extraction_latency_ms": llm_intent_meta.get("latency_ms"),
                },
            )
            if llm_intent_status == "error" and llm_intent_meta.get("error"):
                warnings.append(f"LLM intent extraction unavailable: {llm_intent_meta['error']}")
            if graph_errors:
                warnings.extend(graph_errors)
                result = PlannerResult(
                    mode="reply",
                    intent_spec=intent_spec,
                    reply=PlannerReply(content="Planner could not validate the draft graph proposal."),
                    warnings=warnings,
                    planner_metadata=metadata,
                )
                return result.model_dump(mode="json")
            llm_reply, llm_status, llm_meta = self._llm_reply(
                user_message=user_message,
                graph=proposal_graph,
                case_state=case_state,
                chat_history=chat_history,
            )
            metadata.llm_status = llm_status  # type: ignore[assignment]
            metadata.latency_ms = llm_meta.get("latency_ms")
            if llm_status in {"disabled", "error", "llm_filtered"}:
                metadata.fallback_used = True
                if llm_status == "error" and llm_meta.get("error"):
                    warnings.append(f"LLM reply unavailable: {llm_meta['error']}")
                if llm_status in {"disabled", "llm_filtered"}:
                    warnings.append(f"LLM reply mode: {llm_status}")
            reply = llm_reply or _default_reply(intent=intent, mode="graph", graph=proposal_graph)
            result = PlannerResult(
                mode="graph",
                intent_spec=intent_spec,
                reply=reply,
                graph=proposal_graph.model_dump(mode="json"),
                warnings=warnings,
                planner_metadata=metadata,
            )
            return result.model_dump(mode="json")

        if intent_spec.intent_type == "patch_request":
            proposal_patch, patch_warnings = _build_review_patch(user_message, graph)
            warnings.extend(patch_warnings)
            patch_errors = _validate_patch(graph, proposal_patch) if proposal_patch is not None else ["patch could not be constructed"]
            metadata = PlannerMetadata(
                intent=intent,
                source="heuristic",
                llm_status="not_used",
                model=self.model,
                base_url=self.base_url,
                validation_passed=not patch_errors,
                validation_errors=patch_errors,
                fallback_used=True,
                extras={
                    "proposal_kind": "execution_patch",
                    "graph_id": graph.graph_id,
                    "graph_version": graph.version,
                    "intent_spec": intent_spec.model_dump(mode="json"),
                },
            )
            if proposal_patch is None or patch_errors:
                warnings.extend(patch_errors)
                result = PlannerResult(
                    mode="reply",
                    intent_spec=intent_spec,
                    reply=PlannerReply(content="Planner could not validate the requested patch proposal."),
                    warnings=warnings,
                    planner_metadata=metadata,
                    patch_reason=self.suggest_patch_reason(user_message),
                )
                return result.model_dump(mode="json")
            llm_reply, llm_status, llm_meta = self._llm_reply(
                user_message=user_message,
                graph=graph,
                case_state=case_state,
                chat_history=chat_history,
            )
            metadata.llm_status = llm_status  # type: ignore[assignment]
            metadata.latency_ms = llm_meta.get("latency_ms")
            if llm_status in {"disabled", "error", "llm_filtered"}:
                metadata.fallback_used = True
                if llm_status == "error" and llm_meta.get("error"):
                    warnings.append(f"LLM reply unavailable: {llm_meta['error']}")
                if llm_status in {"disabled", "llm_filtered"}:
                    warnings.append(f"LLM reply mode: {llm_status}")
            reply = llm_reply or _default_reply(intent=intent, mode="patch", patch=proposal_patch)
            result = PlannerResult(
                mode="patch",
                intent_spec=intent_spec,
                reply=reply,
                patch=proposal_patch.model_dump(mode="json"),
                warnings=warnings,
                planner_metadata=metadata,
                patch_reason=proposal_patch.reason,
            )
            return result.model_dump(mode="json")

        llm_reply, llm_status, llm_meta = self._llm_reply(
            user_message=user_message,
            graph=graph,
            case_state=case_state,
            chat_history=chat_history,
        )
        metadata = PlannerMetadata(
            intent=intent,
            source="llm" if llm_reply is not None else "heuristic",
            llm_status=llm_status,  # type: ignore[arg-type]
            model=self.model,
            base_url=self.base_url,
            latency_ms=llm_meta.get("latency_ms"),
            validation_passed=True,
            validation_errors=[],
            fallback_used=llm_reply is None,
            extras={"proposal_kind": "reply_only", "intent_spec": intent_spec.model_dump(mode="json")},
        )
        if llm_status == "error" and llm_meta.get("error"):
            warnings.append(f"LLM reply unavailable: {llm_meta['error']}")
        if llm_status in {"disabled", "llm_filtered"}:
            warnings.append(f"LLM reply mode: {llm_status}")
        reply = llm_reply or _default_reply(intent=intent, mode="reply")
        result = PlannerResult(
            mode="reply",
            intent_spec=intent_spec,
            reply=reply,
            warnings=warnings,
            planner_metadata=metadata,
            patch_reason=self.suggest_patch_reason(user_message),
        )
        return result.model_dump(mode="json")


def create_default_brain_service() -> BrainService:
    return BrainService()
