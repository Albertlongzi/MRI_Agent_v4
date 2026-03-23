from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "configs" / "tool_runtime_profiles.json"


def _as_str_list(values: Any) -> List[str]:
    return [str(item) for item in (values or []) if str(item).strip()]


def _as_env_map(values: Any) -> Dict[str, str]:
    if not isinstance(values, dict):
        return {}
    return {str(key): str(value) for key, value in values.items() if str(key).strip() and value is not None}


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    description: str
    launcher: str
    python_env: Optional[str]
    python_bin: Optional[str]
    conda_env: Optional[str]
    conda_executable: Optional[str]
    image_hint: Optional[str]
    container_image: Optional[str]
    launcher_bin: Optional[str]
    ssh_bin: Optional[str]
    ssh_host: Optional[str]
    ssh_options: List[str]
    remote_workdir: Optional[str]
    container_workdir: Optional[str]
    bind_mounts: List[str]
    env: Dict[str, str]
    env_passthrough: List[str]
    inherit_pythonpath: bool
    gpu: bool
    timeout_seconds: Optional[int]
    notes: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "description": self.description,
            "launcher": self.launcher,
            "python_env": self.python_env,
            "python_bin": self.python_bin,
            "conda_env": self.conda_env,
            "conda_executable": self.conda_executable,
            "image_hint": self.image_hint,
            "container_image": self.container_image,
            "launcher_bin": self.launcher_bin,
            "ssh_bin": self.ssh_bin,
            "ssh_host": self.ssh_host,
            "ssh_options": list(self.ssh_options),
            "remote_workdir": self.remote_workdir,
            "container_workdir": self.container_workdir,
            "bind_mounts": list(self.bind_mounts),
            "env": dict(self.env),
            "env_passthrough": list(self.env_passthrough),
            "inherit_pythonpath": self.inherit_pythonpath,
            "gpu": self.gpu,
            "timeout_seconds": self.timeout_seconds,
            "notes": list(self.notes),
        }


def _read_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"version": 1, "profiles": {}, "tool_profiles": {}}
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"runtime profile config must be a JSON object: {CONFIG_PATH}")
    return payload


@lru_cache(maxsize=1)
def load_runtime_profile_catalog() -> Dict[str, Any]:
    payload = _read_config()
    raw_profiles = payload.get("profiles") or {}
    tool_profiles = payload.get("tool_profiles") or {}
    profiles: Dict[str, RuntimeProfile] = {}
    for profile_id, raw in raw_profiles.items():
        item = raw if isinstance(raw, dict) else {}
        profiles[str(profile_id)] = RuntimeProfile(
            profile_id=str(profile_id),
            description=str(item.get("description") or ""),
            launcher=str(item.get("launcher") or "inproc"),
            python_env=str(item.get("python_env") or "") or None,
            python_bin=str(item.get("python_bin") or "") or None,
            conda_env=str(item.get("conda_env") or "") or None,
            conda_executable=str(item.get("conda_executable") or "") or None,
            image_hint=str(item.get("image_hint") or "") or None,
            container_image=str(item.get("container_image") or item.get("image_hint") or "") or None,
            launcher_bin=str(item.get("launcher_bin") or "") or None,
            ssh_bin=str(item.get("ssh_bin") or "") or None,
            ssh_host=str(item.get("ssh_host") or "") or None,
            ssh_options=_as_str_list(item.get("ssh_options")),
            remote_workdir=str(item.get("remote_workdir") or "") or None,
            container_workdir=str(item.get("container_workdir") or "") or None,
            bind_mounts=_as_str_list(item.get("bind_mounts")),
            env=_as_env_map(item.get("env")),
            env_passthrough=_as_str_list(item.get("env_passthrough")),
            inherit_pythonpath=bool(item.get("inherit_pythonpath", True)),
            gpu=bool(item.get("gpu")),
            timeout_seconds=int(item["timeout_seconds"]) if item.get("timeout_seconds") is not None else None,
            notes=_as_str_list(item.get("notes")),
        )
    normalized_tool_profiles = {
        str(tool_name): str(profile_id)
        for tool_name, profile_id in tool_profiles.items()
        if str(tool_name).strip() and str(profile_id).strip()
    }
    return {
        "version": int(payload.get("version") or 1),
        "profiles": profiles,
        "tool_profiles": normalized_tool_profiles,
    }


def reset_runtime_profile_cache() -> None:
    load_runtime_profile_catalog.cache_clear()


def list_runtime_profiles() -> List[Dict[str, Any]]:
    catalog = load_runtime_profile_catalog()
    profiles = catalog["profiles"]
    return [profiles[key].as_dict() for key in sorted(profiles.keys())]


def get_runtime_profile(profile_id: str) -> Dict[str, Any]:
    catalog = load_runtime_profile_catalog()
    profiles: Dict[str, RuntimeProfile] = catalog["profiles"]
    key = str(profile_id or "").strip()
    profile = profiles.get(key)
    if profile is None:
        raise KeyError(f"runtime profile not found: {key}")
    return profile.as_dict()


def resolve_tool_runtime_profile(tool_name: str) -> Dict[str, Any]:
    catalog = load_runtime_profile_catalog()
    profiles: Dict[str, RuntimeProfile] = catalog["profiles"]
    tool_profiles: Dict[str, str] = catalog["tool_profiles"]
    tool_key = str(tool_name or "").strip()
    profile_id = tool_profiles.get(tool_key, "control-plane")
    profile = profiles.get(profile_id)
    if profile is None:
        profile_id = "control-plane"
        profile = profiles.get(profile_id)
    if profile is None:
        raise KeyError(f"runtime profile not found for tool '{tool_key}': {profile_id}")
    resolved = profile.as_dict()
    resolved["tool_name"] = tool_key
    return resolved


def summarize_runtime_profiles() -> Dict[str, Any]:
    catalog = load_runtime_profile_catalog()
    profiles: Dict[str, RuntimeProfile] = catalog["profiles"]
    tool_profiles: Dict[str, str] = catalog["tool_profiles"]
    tools_by_profile: Dict[str, List[str]] = {profile_id: [] for profile_id in profiles}
    for tool_name, profile_id in sorted(tool_profiles.items()):
        tools_by_profile.setdefault(profile_id, []).append(tool_name)
    return {
        "version": catalog["version"],
        "config_path": str(CONFIG_PATH),
        "profiles": [profiles[key].as_dict() for key in sorted(profiles.keys())],
        "tool_profiles": dict(sorted(tool_profiles.items())),
        "tools_by_profile": tools_by_profile,
        "defaults": {
            "fallback_profile_id": "control-plane",
            "dispatch_policy": "planner proposes tools; executor resolves runtime profile before launch",
        },
    }
