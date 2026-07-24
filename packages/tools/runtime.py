from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

from .bridge import (
    BRIDGE_MODULE,
    BRIDGE_SCHEMAS_MODULE,
    DEFAULT_V3_REPO_NAME,
    V3_ROOT_ENV_VARS,
    ensure_import_root,
    resolve_v3_root,
)
from .runtime_profiles import get_runtime_profile, resolve_tool_runtime_profile


ROOT_DIR = Path(__file__).resolve().parents[2]


class RuntimeDispatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        runtime_profile: Optional[str] = None,
        launcher: Optional[str] = None,
        host: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.runtime_profile = runtime_profile
        self.launcher = launcher
        self.host = host
        self.details = dict(details or {})

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.runtime_profile:
            parts.append(f"profile={self.runtime_profile}")
        if self.launcher:
            parts.append(f"launcher={self.launcher}")
        if self.host:
            parts.append(f"host={self.host}")
        if self.details:
            details = ", ".join(f"{key}={value}" for key, value in sorted(self.details.items()))
            parts.append(f"details={details}")
        return " | ".join(parts)


@dataclass(frozen=True)
class V3ToolRunResult:
    tool_name: str
    data: Dict[str, Any]
    warnings: List[str]
    source_artifacts: List[Dict[str, Any]]
    generated_artifacts: List[Dict[str, Any]]
    runtime_profile: str = "control-plane"
    launcher: str = "inproc"
    invocation: Optional[List[str]] = None
    host: Optional[str] = None
    container_image: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


# Demo cases shipped by the open engine repo (`BCER_open/demo/cases/`).
_DOMAIN_DEMO_CASES: Dict[str, List[str]] = {
    "prostate": ["demo/cases/sub-019_2"],
    "brain": ["demo/cases/Brats18_CBICA_AAM_1"],
    "cardiac": ["demo/cases/acdc_multiseq_patient061_ed"],
}


def _local_hostname() -> str:
    return socket.gethostname()


def _ensure_v3_import_root() -> Path:
    """Thin wrapper over the single shared helper in `bridge.py`.

    The sys.path rules live in `bridge.ensure_import_root` -- keep them there so
    the in-process and worker paths can never drift apart.
    """
    v3_root = resolve_v3_root()
    if v3_root is None:
        raise RuntimeError(
            f"engine repo '{DEFAULT_V3_REPO_NAME}' not found; set {V3_ROOT_ENV_VARS[0]}"
        )
    ensure_import_root(v3_root)
    return v3_root


def _normalize_artifact(item: Any) -> Optional[Dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, dict):
        path = str(item.get("path") or "").strip()
        if not path:
            return None
        return {
            "path": path,
            "kind": str(item.get("kind") or "unknown"),
            "description": str(item.get("description") or ""),
            "media_type": str(item.get("media_type") or "") or None,
        }
    path = str(getattr(item, "path", "") or "").strip()
    if not path:
        return None
    return {
        "path": path,
        "kind": str(getattr(item, "kind", "unknown") or "unknown"),
        "description": str(getattr(item, "description", "") or ""),
        "media_type": str(getattr(item, "media_type", "") or "") or None,
    }


def _as_str_list(values: Any) -> List[str]:
    return [str(item) for item in (values or []) if str(item).strip()]


def _profile_env_assignments(profile: Dict[str, Any]) -> Dict[str, str]:
    env_map = {str(key): str(value) for key, value in dict(profile.get("env") or {}).items() if str(key).strip() and value is not None}
    for name in _as_str_list(profile.get("env_passthrough")):
        if name in os.environ and str(os.environ.get(name) or "").strip():
            env_map[name] = str(os.environ[name])
    python_path_items = [str(ROOT_DIR)]
    inherit_pythonpath = bool(profile.get("inherit_pythonpath", True))
    if inherit_pythonpath:
        existing_python_path = env_map.get("PYTHONPATH") or os.environ.get("PYTHONPATH")
        if existing_python_path:
            python_path_items.append(str(existing_python_path))
    env_map["PYTHONPATH"] = os.pathsep.join(python_path_items)
    env_map["PYTHONNOUSERSITE"] = "1"
    return env_map


def _resolve_python_bin(profile: Dict[str, Any]) -> str:
    python_bin = str(profile.get("python_bin") or "").strip()
    if python_bin:
        return python_bin
    python_env = str(profile.get("python_env") or "").strip()
    if python_env:
        base = Path(python_env)
        if not base.is_absolute():
            base = (ROOT_DIR / base).resolve()
        candidate = base / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _resolve_conda_executable(profile: Dict[str, Any]) -> str:
    return str(profile.get("conda_executable") or "conda").strip()


def _worker_command(profile: Dict[str, Any]) -> List[str]:
    python_bin = str(profile.get("python_bin") or "").strip()
    if python_bin:
        return [python_bin, "-m", "packages.tools.runtime_worker"]
    conda_env = str(profile.get("conda_env") or "").strip()
    if conda_env:
        return [
            _resolve_conda_executable(profile),
            "run",
            "-n",
            conda_env,
            "python",
            "-m",
            "packages.tools.runtime_worker",
        ]
    return [_resolve_python_bin(profile), "-m", "packages.tools.runtime_worker"]


def _shell_join(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _bind_mounts(profile: Dict[str, Any]) -> List[str]:
    return _as_str_list(profile.get("bind_mounts"))


def _bind_sources(profile: Dict[str, Any]) -> List[str]:
    sources: List[str] = []
    for item in _bind_mounts(profile):
        raw = str(item).strip()
        if not raw:
            continue
        source = raw.split(":", 1)[0].strip()
        if source:
            sources.append(source)
    return sources


def _container_image(profile: Dict[str, Any]) -> str:
    image = str(profile.get("container_image") or profile.get("image_hint") or "").strip()
    if not image:
        raise RuntimeDispatchError(
            "container image is missing for apptainer profile",
            runtime_profile=str(profile.get("profile_id") or ""),
            launcher="apptainer",
            host=str(profile.get("ssh_host") or _local_hostname()),
        )
    return image


def _launcher_bin(profile: Dict[str, Any], launcher: str) -> str:
    if launcher == "apptainer":
        return str(profile.get("launcher_bin") or "apptainer").strip()
    if launcher == "ssh":
        return str(profile.get("ssh_bin") or "ssh").strip()
    return str(profile.get("launcher_bin") or "").strip()


def _ssh_options(profile: Dict[str, Any]) -> List[str]:
    options = _as_str_list(profile.get("ssh_options"))
    if not options:
        return ["-o", "BatchMode=yes"]
    return options


def _resolve_demo_case_bind_mounts() -> List[str]:
    return []


def resolve_demo_case(domain: str) -> Optional[Path]:
    v3_root = resolve_v3_root()
    if v3_root is None:
        return None
    for rel_path in _DOMAIN_DEMO_CASES.get(str(domain or "").strip().lower(), []):
        candidate = (v3_root / rel_path).resolve()
        if candidate.exists():
            return candidate
    return None


def _serialize_result(result: V3ToolRunResult) -> Dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "data": dict(result.data or {}),
        "warnings": list(result.warnings or []),
        "source_artifacts": list(result.source_artifacts or []),
        "generated_artifacts": list(result.generated_artifacts or []),
        "runtime_profile": result.runtime_profile,
        "launcher": result.launcher,
        "invocation": list(result.invocation or []),
        "host": result.host,
        "container_image": result.container_image,
        "provenance": dict(result.provenance or {}),
    }


def _deserialize_result(payload: Dict[str, Any]) -> V3ToolRunResult:
    return V3ToolRunResult(
        tool_name=str(payload.get("tool_name") or ""),
        data=dict(payload.get("data") or {}),
        warnings=[str(item) for item in (payload.get("warnings") or []) if str(item).strip()],
        source_artifacts=[dict(item) for item in (payload.get("source_artifacts") or []) if isinstance(item, dict)],
        generated_artifacts=[dict(item) for item in (payload.get("generated_artifacts") or []) if isinstance(item, dict)],
        runtime_profile=str(payload.get("runtime_profile") or "control-plane"),
        launcher=str(payload.get("launcher") or "inproc"),
        invocation=[str(item) for item in (payload.get("invocation") or []) if str(item).strip()] or None,
        host=str(payload.get("host") or "") or None,
        container_image=str(payload.get("container_image") or "") or None,
        provenance=dict(payload.get("provenance") or {}),
    )


def _runtime_provenance(
    *,
    profile: Dict[str, Any],
    launcher: str,
    host: str,
    invocation: Optional[List[str]],
    container_image: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "runtime_profile": str(profile.get("profile_id") or "control-plane"),
        "launcher": launcher,
        "host": host,
        "ssh_host": str(profile.get("ssh_host") or "") or None,
        "remote_workdir": str(profile.get("remote_workdir") or "") or None,
        "gpu": bool(profile.get("gpu")),
        "python_env": str(profile.get("python_env") or "") or None,
        "python_bin": str(profile.get("python_bin") or "") or None,
        "conda_env": str(profile.get("conda_env") or "") or None,
        "conda_executable": str(profile.get("conda_executable") or "") or None,
        "container_image": container_image,
        "bind_mounts": _bind_mounts(profile),
        "env": dict(profile.get("env") or {}),
        "env_passthrough": _as_str_list(profile.get("env_passthrough")),
        "launcher_bin": str(profile.get("launcher_bin") or "") or None,
        "ssh_bin": str(profile.get("ssh_bin") or "") or None,
        "ssh_options": _ssh_options(profile),
        "inherit_pythonpath": bool(profile.get("inherit_pythonpath", True)),
        "timeout_seconds": profile.get("timeout_seconds"),
        "containerized": launcher == "apptainer",
        "remote_execution": bool(profile.get("ssh_host")),
        "invocation": list(invocation or []),
    }


def _apply_runtime_provenance(
    result: V3ToolRunResult,
    *,
    profile: Dict[str, Any],
    launcher: str,
    host: str,
    invocation: Optional[List[str]],
    container_image: Optional[str] = None,
    extra_warnings: Optional[Sequence[str]] = None,
) -> V3ToolRunResult:
    provenance = _runtime_provenance(
        profile=profile,
        launcher=launcher,
        host=host,
        invocation=invocation,
        container_image=container_image,
    )
    data = dict(result.data or {})
    data["runtime_profile"] = provenance["runtime_profile"]
    data["launcher"] = provenance["launcher"]
    data["runtime_provenance"] = provenance
    warnings = list(result.warnings or [])
    warnings.extend(str(item) for item in (extra_warnings or []) if str(item).strip())
    generated_artifacts: List[Dict[str, Any]] = []
    for artifact in result.generated_artifacts:
        updated = dict(artifact)
        updated["runtime_profile"] = provenance["runtime_profile"]
        updated["launcher"] = provenance["launcher"]
        updated["host"] = provenance["host"]
        if container_image:
            updated["container_image"] = container_image
        generated_artifacts.append(updated)
    return V3ToolRunResult(
        tool_name=result.tool_name,
        data=data,
        warnings=warnings,
        source_artifacts=[dict(item) for item in result.source_artifacts],
        generated_artifacts=generated_artifacts,
        runtime_profile=str(provenance["runtime_profile"]),
        launcher=launcher,
        invocation=list(invocation or []) or None,
        host=host,
        container_image=container_image,
        provenance=provenance,
    )


def _run_v3_tool_inproc(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    runtime_profile: str = "control-plane",
    launcher: str = "inproc",
    invocation: Optional[List[str]] = None,
) -> V3ToolRunResult:
    _ensure_v3_import_root()
    tool_registry_module = importlib.import_module(BRIDGE_MODULE)
    schemas_module = importlib.import_module(BRIDGE_SCHEMAS_MODULE)
    build_shell_registry = getattr(tool_registry_module, "build_shell_registry")
    ToolContext = getattr(schemas_module, "ToolContext")

    registry = build_shell_registry(dry_run=False, include_core=True)
    tool = registry.get(str(tool_name))
    artifacts_dir = Path(artifacts_dir).resolve()
    run_dir = Path(run_dir).resolve()
    case_state_path = Path(case_state_path).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    case_state_path.parent.mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(
        case_id=case_id,
        run_id=run_id,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        case_state_path=case_state_path,
    )
    raw = tool.func(dict(args or {}), ctx) or {}

    source_artifacts = []
    for item in raw.get("source_artifacts") or []:
        normalized = _normalize_artifact(item)
        if normalized:
            source_artifacts.append(normalized)

    generated_artifacts = []
    for item in (raw.get("generated_artifacts") or raw.get("artifacts") or []):
        normalized = _normalize_artifact(item)
        if normalized:
            generated_artifacts.append(normalized)

    result = V3ToolRunResult(
        tool_name=str(tool_name),
        data=dict(raw.get("data") or {}),
        warnings=[str(item) for item in (raw.get("warnings") or []) if str(item).strip()],
        source_artifacts=source_artifacts,
        generated_artifacts=generated_artifacts,
        runtime_profile=str(runtime_profile or "control-plane"),
        launcher=str(launcher or "inproc"),
        invocation=list(invocation or []) or None,
    )
    profile_stub = {
        "profile_id": str(runtime_profile or "control-plane"),
        "launcher": str(launcher or "inproc"),
        "gpu": False,
        "env": {},
        "env_passthrough": [],
        "bind_mounts": [],
        "ssh_options": [],
        "timeout_seconds": None,
    }
    return _apply_runtime_provenance(
        result,
        profile=profile_stub,
        launcher=str(launcher or "inproc"),
        host=_local_hostname(),
        invocation=invocation,
    )


def _dispatch_request_payload(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    profile: Dict[str, Any],
    launcher: str,
) -> Dict[str, Any]:
    return {
        "tool_name": str(tool_name),
        "args": dict(args or {}),
        "case_id": str(case_id),
        "run_id": str(run_id),
        "run_dir": str(Path(run_dir).resolve()),
        "artifacts_dir": str(Path(artifacts_dir).resolve()),
        "case_state_path": str(Path(case_state_path).resolve()),
        "runtime_profile": str(profile.get("profile_id") or "control-plane"),
        "launcher": launcher,
    }


def _prepare_dispatch_files(run_dir: Path, tool_name: str, request: Dict[str, Any]) -> tuple[Path, Path]:
    dispatch_dir = Path(run_dir).resolve() / ".runtime_dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    payload_path = dispatch_dir / f"{tool_name}.request.json"
    output_path = dispatch_dir / f"{tool_name}.result.json"
    payload_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_path.exists():
        output_path.unlink()
    return payload_path, output_path


def _validate_local_launcher(profile: Dict[str, Any], launcher: str) -> None:
    if launcher == "apptainer":
        launcher_bin = _launcher_bin(profile, launcher)
        if launcher_bin and launcher_bin.startswith("/"):
            if not Path(launcher_bin).exists():
                raise RuntimeDispatchError(
                    "apptainer executable is missing",
                    runtime_profile=str(profile.get("profile_id") or ""),
                    launcher=launcher,
                    host=_local_hostname(),
                    details={"launcher_bin": launcher_bin},
                )
        elif launcher_bin and shutil.which(launcher_bin) is None:
            raise RuntimeDispatchError(
                "apptainer executable is not available on PATH",
                runtime_profile=str(profile.get("profile_id") or ""),
                launcher=launcher,
                host=_local_hostname(),
                details={"launcher_bin": launcher_bin},
            )
        image = _container_image(profile)
        if "://" not in image and not Path(image).exists():
            raise RuntimeDispatchError(
                "container image is missing",
                runtime_profile=str(profile.get("profile_id") or ""),
                launcher=launcher,
                host=_local_hostname(),
                details={"container_image": image},
            )
        for source in _bind_sources(profile):
            if not Path(source).exists():
                raise RuntimeDispatchError(
                    "bind mount source is missing",
                    runtime_profile=str(profile.get("profile_id") or ""),
                    launcher=launcher,
                    host=_local_hostname(),
                    details={"bind_source": source},
                )


def _remote_preflight(profile: Dict[str, Any], *, launcher: str) -> List[str]:
    checks: List[str] = []
    if launcher == "apptainer":
        launcher_bin = _launcher_bin(profile, launcher)
        if launcher_bin:
            checks.append(
                f"test -x {shlex.quote(launcher_bin)} || "
                f"{{ echo 'missing apptainer executable: {launcher_bin}' >&2; exit 111; }}"
            )
        image = _container_image(profile)
        if "://" not in image:
            checks.append(
                f"test -f {shlex.quote(image)} || "
                f"{{ echo 'missing container image: {image}' >&2; exit 112; }}"
            )
        for source in _bind_sources(profile):
            checks.append(
                f"test -e {shlex.quote(source)} || "
                f"{{ echo 'missing bind source: {source}' >&2; exit 113; }}"
            )
    return checks


def _ssh_command(profile: Dict[str, Any], *, inner_command: List[str], launcher: str) -> List[str]:
    ssh_host = str(profile.get("ssh_host") or "").strip()
    if not ssh_host:
        raise RuntimeDispatchError(
            "ssh launcher requires ssh_host",
            runtime_profile=str(profile.get("profile_id") or ""),
            launcher=launcher,
            host=_local_hostname(),
        )
    ssh_bin = str(profile.get("ssh_bin") or "ssh").strip()
    remote_workdir = str(profile.get("remote_workdir") or ROOT_DIR).strip()
    assignments = _profile_env_assignments(profile)
    remote_steps: List[str] = []
    remote_steps.extend(_remote_preflight(profile, launcher=launcher))
    remote_steps.append(f"cd {shlex.quote(remote_workdir)}")
    for key, value in assignments.items():
        remote_steps.append(f"export {key}={shlex.quote(value)}")
    remote_steps.append(_shell_join(inner_command))
    remote_shell = " && ".join(remote_steps)
    remote_command = f"bash -lc {shlex.quote(remote_shell)}"
    return [ssh_bin] + _ssh_options(profile) + [ssh_host, remote_command]


def _run_command(
    command: List[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    timeout_seconds: Optional[int],
    launcher: str,
    profile: Dict[str, Any],
    host: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeDispatchError(
            f"{launcher} launcher timed out",
            runtime_profile=str(profile.get("profile_id") or ""),
            launcher=launcher,
            host=host,
            details={"timeout_seconds": timeout_seconds, "tool_command": _shell_join(command)},
        ) from exc


def _completed_warnings(completed: subprocess.CompletedProcess[str], *, launcher: str) -> List[str]:
    warnings: List[str] = []
    if (completed.stderr or "").strip():
        warnings.append(f"{launcher} stderr: {completed.stderr.strip()[:1000]}")
    if (completed.stdout or "").strip():
        warnings.append(f"{launcher} stdout: {completed.stdout.strip()[:1000]}")
    return warnings


def _load_dispatch_result(
    output_path: Path,
    *,
    launcher: str,
    profile: Dict[str, Any],
    host: str,
    completed: subprocess.CompletedProcess[str],
) -> V3ToolRunResult:
    if completed.returncode != 0:
        raise RuntimeDispatchError(
            f"{launcher} launcher failed",
            runtime_profile=str(profile.get("profile_id") or ""),
            launcher=launcher,
            host=host,
            details={
                "returncode": completed.returncode,
                "stdout": (completed.stdout or "").strip()[:4000],
                "stderr": (completed.stderr or "").strip()[:4000],
            },
        )
    for _ in range(20):
        if output_path.exists():
            break
        time.sleep(0.25)
    if not output_path.exists():
        raise RuntimeDispatchError(
            f"{launcher} launcher did not produce result file",
            runtime_profile=str(profile.get("profile_id") or ""),
            launcher=launcher,
            host=host,
            details={"output_path": str(output_path)},
        )
    return _deserialize_result(json.loads(output_path.read_text(encoding="utf-8")))


def _run_v3_tool_subprocess(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    profile: Dict[str, Any],
) -> V3ToolRunResult:
    request = _dispatch_request_payload(
        tool_name,
        args,
        case_id=case_id,
        run_id=run_id,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        case_state_path=case_state_path,
        profile=profile,
        launcher="subprocess",
    )
    payload_path, output_path = _prepare_dispatch_files(run_dir, tool_name, request)
    command = _worker_command(profile) + ["--payload", str(payload_path), "--output", str(output_path)]
    env = os.environ.copy()
    env.update(_profile_env_assignments(profile))
    completed = _run_command(
        command,
        cwd=ROOT_DIR,
        env=env,
        timeout_seconds=int(profile["timeout_seconds"]) if profile.get("timeout_seconds") is not None else None,
        launcher="subprocess",
        profile=profile,
        host=_local_hostname(),
    )
    result = _load_dispatch_result(output_path, launcher="subprocess", profile=profile, host=_local_hostname(), completed=completed)
    return _apply_runtime_provenance(
        result,
        profile=profile,
        launcher="subprocess",
        host=_local_hostname(),
        invocation=command,
        extra_warnings=_completed_warnings(completed, launcher="subprocess"),
    )


def _run_v3_tool_ssh(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    profile: Dict[str, Any],
) -> V3ToolRunResult:
    host = str(profile.get("ssh_host") or "").strip() or _local_hostname()
    request = _dispatch_request_payload(
        tool_name,
        args,
        case_id=case_id,
        run_id=run_id,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        case_state_path=case_state_path,
        profile=profile,
        launcher="ssh",
    )
    payload_path, output_path = _prepare_dispatch_files(run_dir, tool_name, request)
    command = _ssh_command(
        profile,
        inner_command=_worker_command(profile) + ["--payload", str(payload_path), "--output", str(output_path)],
        launcher="ssh",
    )
    completed = _run_command(
        command,
        cwd=ROOT_DIR,
        env=os.environ.copy(),
        timeout_seconds=int(profile["timeout_seconds"]) if profile.get("timeout_seconds") is not None else None,
        launcher="ssh",
        profile=profile,
        host=host,
    )
    result = _load_dispatch_result(output_path, launcher="ssh", profile=profile, host=host, completed=completed)
    return _apply_runtime_provenance(
        result,
        profile=profile,
        launcher="ssh",
        host=host,
        invocation=command,
        extra_warnings=_completed_warnings(completed, launcher="ssh"),
    )


def _apptainer_inner_command(profile: Dict[str, Any], *, payload_path: Path, output_path: Path) -> List[str]:
    inner_worker = _worker_command(profile) + ["--payload", str(payload_path), "--output", str(output_path)]
    launcher_bin = _launcher_bin(profile, "apptainer")
    if not launcher_bin:
        launcher_bin = "apptainer"
    command: List[str] = [launcher_bin, "exec"]
    if bool(profile.get("gpu")):
        command.append("--nv")
    for bind in _bind_mounts(profile):
        command.extend(["-B", bind])
    for key, value in sorted(_profile_env_assignments(profile).items()):
        command.extend(["--env", f"{key}={value}"])
    container_workdir = str(profile.get("container_workdir") or profile.get("remote_workdir") or ROOT_DIR).strip()
    if container_workdir:
        command.extend(["--pwd", container_workdir])
    command.append(_container_image(profile))
    command.extend(inner_worker)
    return command


def _run_v3_tool_apptainer(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    profile: Dict[str, Any],
) -> V3ToolRunResult:
    request = _dispatch_request_payload(
        tool_name,
        args,
        case_id=case_id,
        run_id=run_id,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        case_state_path=case_state_path,
        profile=profile,
        launcher="apptainer",
    )
    payload_path, output_path = _prepare_dispatch_files(run_dir, tool_name, request)
    inner_command = _apptainer_inner_command(profile, payload_path=payload_path, output_path=output_path)
    host = str(profile.get("ssh_host") or "").strip() or _local_hostname()
    if str(profile.get("ssh_host") or "").strip():
        command = _ssh_command(profile, inner_command=inner_command, launcher="apptainer")
        env = os.environ.copy()
    else:
        _validate_local_launcher(profile, "apptainer")
        command = inner_command
        env = os.environ.copy()
        env.update(_profile_env_assignments(profile))
    completed = _run_command(
        command,
        cwd=ROOT_DIR,
        env=env,
        timeout_seconds=int(profile["timeout_seconds"]) if profile.get("timeout_seconds") is not None else None,
        launcher="apptainer",
        profile=profile,
        host=host,
    )
    result = _load_dispatch_result(output_path, launcher="apptainer", profile=profile, host=host, completed=completed)
    return _apply_runtime_provenance(
        result,
        profile=profile,
        launcher="apptainer",
        host=host,
        invocation=command,
        container_image=_container_image(profile),
        extra_warnings=_completed_warnings(completed, launcher="apptainer"),
    )


def run_v3_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    case_state_path: Path,
    runtime_profile_override: Optional[str] = None,
) -> V3ToolRunResult:
    profile = get_runtime_profile(runtime_profile_override) if runtime_profile_override else resolve_tool_runtime_profile(str(tool_name))
    launcher = str(profile.get("launcher") or "inproc").strip().lower()
    if launcher == "inproc":
        return _run_v3_tool_inproc(
            tool_name,
            args,
            case_id=case_id,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
            runtime_profile=str(profile.get("profile_id") or "control-plane"),
            launcher=launcher,
        )
    if launcher == "subprocess":
        return _run_v3_tool_subprocess(
            tool_name,
            args,
            case_id=case_id,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
            profile=profile,
        )
    if launcher == "ssh":
        return _run_v3_tool_ssh(
            tool_name,
            args,
            case_id=case_id,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
            profile=profile,
        )
    if launcher == "apptainer":
        return _run_v3_tool_apptainer(
            tool_name,
            args,
            case_id=case_id,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            case_state_path=case_state_path,
            profile=profile,
        )
    raise RuntimeDispatchError(
        f"unsupported launcher for tool {tool_name}",
        runtime_profile=str(profile.get("profile_id") or ""),
        launcher=launcher,
        host=str(profile.get("ssh_host") or _local_hostname()),
    )
