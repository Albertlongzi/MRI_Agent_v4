from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


BRIDGE_MODULE = "MRI_Agent.mri_agent_shell.tool_registry"
DEFAULT_V3_REPO_NAME = "MRI_Agent"
V3_ROOT_ENV_VARS: Tuple[str, ...] = ("MRI_AGENT_V3_ROOT", "MRI_AGENT_ROOT")


@dataclass(frozen=True)
class BridgeHealth:
    status: str
    v3_root: Optional[str]
    import_root: Optional[str]
    module_name: str
    import_ok: bool
    registry_ok: bool
    tool_count: int
    domain_count: int
    capabilities: Tuple[str, ...]
    domains: Tuple[str, ...]
    warnings: Tuple[str, ...]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "v3_root": self.v3_root,
            "import_root": self.import_root,
            "module_name": self.module_name,
            "import_ok": self.import_ok,
            "registry_ok": self.registry_ok,
            "tool_count": self.tool_count,
            "domain_count": self.domain_count,
            "capabilities": list(self.capabilities),
            "domains": list(self.domains),
            "warnings": list(self.warnings),
            "error": self.error,
        }


def _candidate_v3_roots() -> List[Path]:
    candidates: List[Path] = []
    for env_var in V3_ROOT_ENV_VARS:
        raw = os.environ.get(env_var)
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.name == DEFAULT_V3_REPO_NAME:
            candidates.append(path)
        else:
            candidates.append(path / DEFAULT_V3_REPO_NAME)

    here = Path(__file__).resolve()
    medgemma_root = here.parents[3]
    candidates.append(medgemma_root / DEFAULT_V3_REPO_NAME)
    return candidates


@lru_cache(maxsize=1)
def resolve_v3_root() -> Optional[Path]:
    for candidate in _candidate_v3_roots():
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _ensure_import_root(v3_root: Optional[Path]) -> Optional[str]:
    if v3_root is None:
        return None
    import_root = str(v3_root.parent.resolve())
    if import_root not in sys.path:
        sys.path.insert(0, import_root)
    return import_root


@lru_cache(maxsize=1)
def _load_v3_tool_registry_module() -> Tuple[Optional[Any], Optional[str], Optional[str]]:
    v3_root = resolve_v3_root()
    import_root = _ensure_import_root(v3_root)
    if v3_root is None:
        return None, import_root, f"v3 repo '{DEFAULT_V3_REPO_NAME}' not found"

    try:
        module = importlib.import_module(BRIDGE_MODULE)
        return module, import_root, None
    except Exception:
        return None, import_root, traceback.format_exc()


def _build_registry(*, dry_run: bool = False, include_core: bool | None = None) -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
    module, _, import_error = _load_v3_tool_registry_module()
    if module is None:
        return None, None, import_error

    build_shell_registry = getattr(module, "build_shell_registry", None)
    if not callable(build_shell_registry):
        return None, module, "v3 tool registry module does not expose build_shell_registry"

    try:
        registry = build_shell_registry(dry_run=dry_run, include_core=include_core)
        return registry, module, None
    except Exception:
        return None, module, traceback.format_exc()


def _registry_specs(registry: Any) -> List[Dict[str, Any]]:
    if registry is None:
        return []
    list_specs = getattr(registry, "list_specs", None)
    if callable(list_specs):
        try:
            specs = list_specs()
            if isinstance(specs, list):
                return [spec for spec in specs if isinstance(spec, dict)]
            return [spec for spec in list(specs) if isinstance(spec, dict)]
        except Exception:
            return []
    return []


def _as_list(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def discover_tools(*, dry_run: bool = False, include_core: bool | None = None) -> List[Dict[str, Any]]:
    registry, module, _ = _build_registry(dry_run=dry_run, include_core=include_core)
    if registry is None or module is None:
        return []

    list_tool_metadata = getattr(module, "list_tool_metadata", None)
    if callable(list_tool_metadata):
        try:
            metadata = list_tool_metadata(registry)
            if isinstance(metadata, list):
                return [rec for rec in metadata if isinstance(rec, dict)]
            return [rec for rec in list(metadata) if isinstance(rec, dict)]
        except Exception:
            pass

    specs = _registry_specs(registry)
    out: List[Dict[str, Any]] = []
    for spec in specs:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": str(spec.get("description") or ""),
                "domains": [],
                "capabilities": [],
                "required_args": _as_list((spec.get("input_schema") or {}).get("required") or []),
                "tags": _as_list(spec.get("tags") or []),
            }
        )
    return out


def discover_domains(*, dry_run: bool = False, include_core: bool | None = None) -> Dict[str, Dict[str, Any]]:
    registry, module, _ = _build_registry(dry_run=dry_run, include_core=include_core)
    if registry is None or module is None:
        return {}

    discover_domain_catalog = getattr(module, "discover_domain_catalog", None)
    if callable(discover_domain_catalog):
        try:
            catalog = discover_domain_catalog(registry)
            if isinstance(catalog, dict):
                return {str(k): v for k, v in catalog.items() if isinstance(v, dict)}
        except Exception:
            pass

    tools = discover_tools(dry_run=dry_run, include_core=include_core)
    by_domain: Dict[str, List[str]] = {}
    for tool in tools:
        for domain in tool.get("domains") or []:
            by_domain.setdefault(str(domain), []).append(str(tool.get("name") or ""))
    return {
        domain: {
            "capabilities": [],
            "tool_count": len(sorted(set(names))),
            "tools": sorted(set(names)),
        }
        for domain, names in by_domain.items()
    }


def discover_capabilities(*, dry_run: bool = False, include_core: bool | None = None) -> Dict[str, Any]:
    tools = discover_tools(dry_run=dry_run, include_core=include_core)
    domains = discover_domains(dry_run=dry_run, include_core=include_core)

    tool_capabilities: Dict[str, List[str]] = {}
    capability_set = set()
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        caps = _as_list(tool.get("capabilities") or [])
        if name:
            tool_capabilities[name] = caps
        capability_set.update(caps)

    domain_capabilities: Dict[str, List[str]] = {}
    for domain, info in domains.items():
        caps = _as_list((info or {}).get("capabilities") or [])
        domain_capabilities[domain] = caps
        capability_set.update(caps)

    return {
        "capabilities": sorted(capability_set),
        "tool_capabilities": tool_capabilities,
        "domain_capabilities": domain_capabilities,
    }


def bridge_health(*, dry_run: bool = False, include_core: bool | None = None) -> Dict[str, Any]:
    v3_root = resolve_v3_root()
    import_root = str(v3_root.parent.resolve()) if v3_root is not None else None
    warnings: List[str] = []

    module, _, import_error = _load_v3_tool_registry_module()
    if module is None:
        health = BridgeHealth(
            status="down",
            v3_root=str(v3_root) if v3_root is not None else None,
            import_root=import_root,
            module_name=BRIDGE_MODULE,
            import_ok=False,
            registry_ok=False,
            tool_count=0,
            domain_count=0,
            capabilities=(),
            domains=(),
            warnings=tuple(warnings),
            error=import_error,
        )
        return health.to_dict()

    registry, _, registry_error = _build_registry(dry_run=dry_run, include_core=include_core)
    if registry is None:
        health = BridgeHealth(
            status="partial",
            v3_root=str(v3_root) if v3_root is not None else None,
            import_root=import_root,
            module_name=BRIDGE_MODULE,
            import_ok=True,
            registry_ok=False,
            tool_count=0,
            domain_count=0,
            capabilities=(),
            domains=(),
            warnings=tuple(warnings),
            error=registry_error,
        )
        return health.to_dict()

    tools = discover_tools(dry_run=dry_run, include_core=include_core)
    domains = discover_domains(dry_run=dry_run, include_core=include_core)
    caps = discover_capabilities(dry_run=dry_run, include_core=include_core)
    if dry_run or include_core is False:
        warnings.append("bridge built without core registry tools")

    health = BridgeHealth(
        status="ok",
        v3_root=str(v3_root) if v3_root is not None else None,
        import_root=import_root,
        module_name=BRIDGE_MODULE,
        import_ok=True,
        registry_ok=True,
        tool_count=len(tools),
        domain_count=len(domains),
        capabilities=tuple(caps.get("capabilities") or ()),
        domains=tuple(sorted(domains.keys())),
        warnings=tuple(warnings),
        error=None,
    )
    return health.to_dict()

