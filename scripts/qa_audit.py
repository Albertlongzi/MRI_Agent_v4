from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import error, request

from packages.tools.runtime import resolve_demo_case


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_BASE_URL = "http://127.0.0.1:8008"
DEFAULT_GRAPH_MESSAGE = "Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report."
DEFAULT_PATCH_MESSAGE = "pause before segmentation"
EXPECTED_STAGE_ORDER: Tuple[Tuple[str, str], ...] = (
    ("identify", "identify_sequences"),
    ("register", "register_to_reference"),
    ("segment", "segment_prostate"),
    ("vlm", "package_vlm_evidence"),
    ("report", "generate_report"),
)
VALID_PLANNER_MODES = {"graph", "patch", "reply"}
CONTRADICTION_PATTERNS: Tuple[str, ...] = (
    "segmentation_usable=false",
    "prostate mask unavailable",
    "prostate mask unusable",
    "segmentation unusable",
    "segmentation unavailable",
    "pipeline could not reliably assess lesions",
    "missing adc and/or segmentation issues",
)


class AuditError(RuntimeError):
    pass


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditSummary:
    api_base_url: str
    graph_id: Optional[str]
    checks: List[CheckResult]

    @property
    def passed(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    def counts(self) -> Dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for check in self.checks:
            out[check.status] = out.get(check.status, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "api_base_url": self.api_base_url,
            "graph_id": self.graph_id,
            "counts": self.counts(),
            "checks": [asdict(check) for check in self.checks],
        }


def _json_request(
    method: str,
    url: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: float = 30.0,
) -> Any:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout_s) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else None


def _text_request(url: str, *, timeout_s: float = 30.0) -> str:
    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout_s) as response:
        return response.read().decode("utf-8")


def _http_ok(method: str, url: str, *, timeout_s: float = 15.0) -> Tuple[bool, str]:
    req = request.Request(url, method=method)
    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            return True, f"http {response.status}"
    except error.HTTPError as exc:
        return False, f"http {exc.code}"
    except Exception as exc:  # pragma: no cover - exercised in integration mode
        return False, str(exc)


def _result(name: str, passed: bool, detail: str, *, context: Optional[Dict[str, Any]] = None, warn: bool = False) -> CheckResult:
    status = "warn" if warn and passed else "fail" if not passed else "pass"
    return CheckResult(name=name, status=status, detail=detail, context=dict(context or {}))


def _demo_input_root() -> str:
    demo_case = resolve_demo_case("prostate")
    if demo_case is None:
        raise AuditError("could not resolve prostate demo input_root from the shared v3 repo")
    return str(demo_case)


def validate_planner_mode(planner_payload: Dict[str, Any]) -> Optional[str]:
    mode = str(planner_payload.get("mode") or "").strip()
    if mode and mode not in VALID_PLANNER_MODES:
        return mode
    return None


def load_runtime_case_state(repo_root: Path, graph_id: str) -> Dict[str, Any]:
    case_state_path = repo_root / "runtime" / graph_id / "case_state.json"
    if not case_state_path.exists():
        raise AuditError(f"runtime case state missing: {case_state_path}")
    return json.loads(case_state_path.read_text(encoding="utf-8"))


def _record_for_tool(runtime_case_state: Dict[str, Any], stage: str, tool_name: str) -> Optional[Dict[str, Any]]:
    return (
        runtime_case_state.get("stage_outputs", {})
        .get(stage, {})
        .get(tool_name, [None])[-1]
    )


def evaluate_runtime_case_state(runtime_case_state: Dict[str, Any]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    for stage, tool_name in EXPECTED_STAGE_ORDER:
        record = _record_for_tool(runtime_case_state, stage, tool_name)
        if not isinstance(record, dict):
            checks.append(
                _result(
                    f"runtime.{tool_name}",
                    False,
                    "missing stage output record",
                    context={"stage": stage},
                )
            )
            continue
        ok = bool(record.get("ok"))
        consumable = bool(record.get("consumable"))
        validation = record.get("validation") if isinstance(record.get("validation"), dict) else {}
        resolved = validation.get("resolved_output_paths") if isinstance(validation, dict) else {}
        missing_paths = validation.get("missing_output_paths") if isinstance(validation, dict) else []
        paths_exist = True
        absent: List[str] = []
        if isinstance(resolved, dict):
            for key, raw_path in resolved.items():
                path = Path(str(raw_path))
                if not path.exists():
                    paths_exist = False
                    absent.append(f"{key}={path}")
        detail_bits = [f"ok={ok}", f"consumable={consumable}"]
        if missing_paths:
            detail_bits.append(f"missing={len(missing_paths)}")
        if absent:
            detail_bits.append(f"absent={len(absent)}")
        checks.append(
            _result(
                f"runtime.{tool_name}",
                ok and consumable and paths_exist,
                ", ".join(detail_bits),
                context={
                    "stage": stage,
                    "missing_output_paths": missing_paths,
                    "absent_paths": absent,
                },
            )
        )
    return checks


def detect_report_contradictions(
    *,
    graph_status: str,
    report_json: Dict[str, Any],
    clinical_report_text: str,
) -> List[str]:
    contradictions: List[str] = []
    lesion_meta = report_json.get("lesion_assessment_meta")
    lesion_meta = lesion_meta if isinstance(lesion_meta, dict) else {}
    stage_status = report_json.get("stage_status")
    stage_status = stage_status if isinstance(stage_status, dict) else {}
    limitations = report_json.get("limitations")
    limitations = limitations if isinstance(limitations, list) else []

    if lesion_meta.get("segmentation_usable") is False:
        contradictions.append("report.json states segmentation_usable=false")

    if str(graph_status) == "completed":
        upstream_good = (
            stage_status.get("identify_sequences") is True
            and stage_status.get("register_to_reference") is True
            and stage_status.get("segment_prostate") is True
            and lesion_meta.get("segmentation_usable") is True
        )
        lowered_report = clinical_report_text.lower()
        lowered_limitations = " | ".join(str(item).lower() for item in limitations)
        if upstream_good:
            for pattern in CONTRADICTION_PATTERNS:
                if pattern in lowered_report or pattern in lowered_limitations:
                    contradictions.append(f"clinical report still contains contradiction phrase: {pattern}")
    return contradictions


def _artifact_http_url(api_base_url: str, artifact_uri: str) -> Optional[str]:
    uri = str(artifact_uri or "").strip().lstrip("/")
    if not uri:
        return None
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    if not uri.startswith("artifacts/"):
        return None
    return f"{api_base_url.rstrip('/')}/{uri}"


def evaluate_graph_artifacts(
    *,
    repo_root: Path,
    api_base_url: str,
    graph: Dict[str, Any],
) -> List[CheckResult]:
    graph_id = str(graph.get("graph_id") or "")
    checks: List[CheckResult] = []
    artifacts = graph.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, list) else []
    if not artifacts:
        return [_result("artifacts.graph", False, "graph.artifacts is empty", context={"graph_id": graph_id})]

    missing_local: List[str] = []
    http_failures: List[str] = []
    sampled_urls: List[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        uri = str(artifact.get("uri") or "").strip()
        if not uri:
            missing_local.append("<empty uri>")
            continue
        local_path = repo_root / uri
        if not local_path.exists():
            missing_local.append(str(local_path))
        http_url = _artifact_http_url(api_base_url, uri)
        mime_type = str(artifact.get("mime_type") or "")
        kind = str(artifact.get("kind") or "")
        if http_url and (mime_type.startswith("text/") or kind in {"json", "report", "text", "log", "png"}):
            sampled_urls.append(http_url)
            ok, detail = _http_ok("HEAD", http_url)
            if not ok:
                http_failures.append(f"{http_url} ({detail})")

    checks.append(
        _result(
            "artifacts.local",
            not missing_local,
            f"missing_local={len(missing_local)}",
            context={"missing": missing_local[:20], "graph_id": graph_id},
        )
    )
    checks.append(
        _result(
            "artifacts.http",
            not http_failures,
            f"http_failures={len(http_failures)}",
            context={"failures": http_failures[:20], "sampled": sampled_urls[:20]},
        )
    )
    return checks


def _find_artifact(graph: Dict[str, Any], *, predicate) -> Optional[Dict[str, Any]]:
    artifacts = graph.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, list) else []
    for artifact in artifacts:
        if isinstance(artifact, dict) and predicate(artifact):
            return artifact
    return None


def _read_repo_artifact(repo_root: Path, artifact: Optional[Dict[str, Any]]) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(artifact, dict):
        return None, None
    uri = str(artifact.get("uri") or "").strip()
    if not uri:
        return None, None
    path = repo_root / uri
    if not path.exists():
        return path, None
    return path, path.read_text(encoding="utf-8")


def run_audit(
    *,
    api_base_url: str,
    repo_root: Path,
    case_id: str,
    input_root: str,
    domain: str,
    graph_message: str,
    patch_message: str,
    require_planner_up: bool,
    timeout_s: float,
) -> AuditSummary:
    checks: List[CheckResult] = []

    health = _json_request("GET", f"{api_base_url}/api/health", timeout_s=timeout_s)
    checks.append(_result("api.health", health.get("status") == "ok", f"status={health.get('status')}", context=health))

    frontend_html = _text_request(f"{api_base_url}/", timeout_s=timeout_s)
    checks.append(
        _result(
            "frontend.shell",
            "MRI AI STUDIO" in frontend_html and 'id="graph-canvas"' in frontend_html,
            "root index served integrated workstation shell",
        )
    )
    static_asset_failures: List[str] = []
    for asset_path in ("/static/styles.css", "/static/app.js"):
        ok, detail = _http_ok("HEAD", f"{api_base_url}{asset_path}", timeout_s=timeout_s)
        if not ok:
            static_asset_failures.append(f"{asset_path} ({detail})")
    checks.append(
        _result(
            "frontend.assets",
            not static_asset_failures,
            f"missing_assets={len(static_asset_failures)}",
            context={"failures": static_asset_failures},
        )
    )

    planner_health = _json_request("GET", f"{api_base_url}/api/planner/health", timeout_s=timeout_s)
    planner_status = str(planner_health.get("status") or "")
    planner_ok = planner_status == "ok"
    checks.append(
        _result(
            "planner.health",
            planner_ok if require_planner_up else planner_status in {"ok", "disabled", "error"},
            f"status={planner_status}",
            context=planner_health,
            warn=not require_planner_up and not planner_ok,
        )
    )

    _json_request("POST", f"{api_base_url}/api/reset", payload={}, timeout_s=timeout_s)
    register_payload = {
        "case_id": case_id,
        "input_root": input_root,
        "domain": domain,
        "session_id": f"qa-audit-{case_id}",
    }
    session_resp = _json_request("POST", f"{api_base_url}/api/session", payload=register_payload, timeout_s=timeout_s)
    session = session_resp.get("session") if isinstance(session_resp, dict) else {}
    graph = session_resp.get("graph") if isinstance(session_resp, dict) else {}
    checks.append(
        _result(
            "session.bootstrap",
            session_resp.get("status") == "registered"
            and session.get("case_state", {}).get("case_id") == case_id
            and graph.get("domain") == domain,
            "registered session and initialized graph",
            context={"response_status": session_resp.get("status"), "graph_id": graph.get("graph_id")},
        )
    )

    graph_resp = _json_request("GET", f"{api_base_url}/api/graph", timeout_s=timeout_s)
    graph_nodes = graph_resp.get("nodes") if isinstance(graph_resp, dict) else []
    checks.append(
        _result(
            "graph.fetch",
            bool(graph_resp.get("graph_id")) and isinstance(graph_nodes, list) and len(graph_nodes) >= 1,
            f"graph_id={graph_resp.get('graph_id')}, nodes={len(graph_nodes) if isinstance(graph_nodes, list) else 0}",
            context={"graph_id": graph_resp.get("graph_id"), "node_count": len(graph_nodes) if isinstance(graph_nodes, list) else None},
        )
    )

    graph_chat = _json_request(
        "POST",
        f"{api_base_url}/api/chat",
        payload={"message": graph_message},
        timeout_s=timeout_s,
    )
    planner_payload = graph_chat.get("planner") if isinstance(graph_chat, dict) else {}
    invalid_mode = validate_planner_mode(planner_payload if isinstance(planner_payload, dict) else {})
    checks.append(
        _result(
            "chat.graph_path",
            isinstance(planner_payload, dict)
            and planner_payload.get("mode") == "graph"
            and invalid_mode is None
            and isinstance(graph_chat.get("graph", {}).get("nodes"), list)
            and len(graph_chat["graph"]["nodes"]) >= 6,
            f"planner.mode={planner_payload.get('mode')}",
            context={"planner_mode": planner_payload.get("mode"), "invalid_mode": invalid_mode},
        )
    )

    patch_chat = _json_request(
        "POST",
        f"{api_base_url}/api/chat",
        payload={"message": patch_message},
        timeout_s=timeout_s,
    )
    patch_planner = patch_chat.get("planner") if isinstance(patch_chat, dict) else {}
    invalid_patch_mode = validate_planner_mode(patch_planner if isinstance(patch_planner, dict) else {})
    checks.append(
        _result(
            "chat.patch_path",
            isinstance(patch_planner, dict)
            and patch_planner.get("mode") == "patch"
            and invalid_patch_mode is None
            and isinstance(patch_chat.get("patch"), dict)
            and int(patch_chat.get("proposal_count") or 0) >= 1,
            f"planner.mode={patch_planner.get('mode')}, proposals={patch_chat.get('proposal_count')}",
            context={"planner_mode": patch_planner.get("mode"), "invalid_mode": invalid_patch_mode},
        )
    )

    apply_resp = _json_request("POST", f"{api_base_url}/api/proposals/apply-latest", payload={}, timeout_s=timeout_s)
    checks.append(
        _result(
            "patch.apply_latest",
            bool(apply_resp.get("applied")),
            f"applied={apply_resp.get('applied')}",
            context={"graph_version": apply_resp.get("graph", {}).get("version")},
        )
    )

    execute_resp = _json_request("POST", f"{api_base_url}/api/execute/until-done", payload={}, timeout_s=timeout_s)
    final_graph = execute_resp.get("graph") if isinstance(execute_resp, dict) else {}
    steps = execute_resp.get("steps") if isinstance(execute_resp, dict) else []
    checks.append(
        _result(
            "execute.until_done",
            final_graph.get("status") == "completed" and isinstance(steps, list) and len(steps) >= 1,
            f"graph.status={final_graph.get('status')}, steps={len(steps) if isinstance(steps, list) else 0}",
            context={"graph_id": final_graph.get("graph_id"), "graph_status": final_graph.get("status")},
        )
    )

    graph_id = str(final_graph.get("graph_id") or graph_resp.get("graph_id") or "")
    runtime_case_state = load_runtime_case_state(repo_root, graph_id)
    checks.extend(evaluate_runtime_case_state(runtime_case_state))
    checks.extend(evaluate_graph_artifacts(repo_root=repo_root, api_base_url=api_base_url, graph=final_graph))

    report_json_artifact = _find_artifact(
        final_graph,
        predicate=lambda artifact: "report.json" in str(artifact.get("uri") or ""),
    )
    report_md_artifact = _find_artifact(
        final_graph,
        predicate=lambda artifact: "clinical_report.md" in str(artifact.get("uri") or ""),
    )
    report_json_path, report_json_text = _read_repo_artifact(repo_root, report_json_artifact)
    report_md_path, report_md_text = _read_repo_artifact(repo_root, report_md_artifact)
    if not report_json_text or not report_md_text:
        checks.append(
            _result(
                "report.presence",
                False,
                "missing report.json or clinical_report.md",
                context={"report_json_path": str(report_json_path), "report_md_path": str(report_md_path)},
            )
        )
    else:
        report_payload = json.loads(report_json_text)
        contradictions = detect_report_contradictions(
            graph_status=str(final_graph.get("status") or ""),
            report_json=report_payload,
            clinical_report_text=report_md_text,
        )
        checks.append(
            _result(
                "report.consistency",
                not contradictions,
                "no report contradiction detected" if not contradictions else "; ".join(contradictions),
                context={"contradictions": contradictions},
            )
        )

    return AuditSummary(api_base_url=api_base_url, graph_id=graph_id or None, checks=checks)


def _wait_for_api(api_base_url: str, *, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last_error = "timeout"
    while time.time() < deadline:
        try:
            payload = _json_request("GET", f"{api_base_url}/api/health", timeout_s=2.0)
            if isinstance(payload, dict) and payload.get("status") == "ok":
                return
            last_error = str(payload)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise AuditError(f"API did not become ready at {api_base_url}: {last_error}")


def start_local_api(*, repo_root: Path, api_base_url: str) -> subprocess.Popen[str]:
    port = api_base_url.rsplit(":", 1)[-1]
    env = os.environ.copy()
    python_path = [str(repo_root)]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    def _cleanup() -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    atexit.register(_cleanup)
    _wait_for_api(api_base_url)
    return process


def _print_summary(summary: AuditSummary) -> None:
    counts = summary.counts()
    print(f"QA audit against {summary.api_base_url}")
    print(f"graph_id: {summary.graph_id or '<unknown>'}")
    print(f"summary: pass={counts.get('pass', 0)} warn={counts.get('warn', 0)} fail={counts.get('fail', 0)}")
    for check in summary.checks:
        print(f"[{check.status.upper()}] {check.name}: {check.detail}")
        if check.status != "pass" and check.context:
            print(json.dumps(check.context, ensure_ascii=False, indent=2))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MRI_Agent_v4 QA acceptance audit against a live API.")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--case-id", default="prostate_qa_audit_001")
    parser.add_argument("--input-root", default=None)
    parser.add_argument("--domain", default="prostate")
    parser.add_argument("--graph-message", default=DEFAULT_GRAPH_MESSAGE)
    parser.add_argument("--patch-message", default=DEFAULT_PATCH_MESSAGE)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument("--require-planner-up", action="store_true")
    parser.add_argument("--start-local-api", action="store_true")
    parser.add_argument("--write-json", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    input_root = str(args.input_root or _demo_input_root())
    api_process: Optional[subprocess.Popen[str]] = None
    try:
        if args.start_local_api:
            api_process = start_local_api(repo_root=repo_root, api_base_url=args.api_base_url)
        summary = run_audit(
            api_base_url=args.api_base_url.rstrip("/"),
            repo_root=repo_root,
            case_id=str(args.case_id),
            input_root=input_root,
            domain=str(args.domain),
            graph_message=str(args.graph_message),
            patch_message=str(args.patch_message),
            require_planner_up=bool(args.require_planner_up),
            timeout_s=float(args.timeout_s),
        )
        _print_summary(summary)
        if args.write_json:
            output_path = Path(args.write_json).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if summary.passed else 1
    except KeyboardInterrupt:  # pragma: no cover - interactive safety
        if api_process is not None and api_process.poll() is None:
            api_process.send_signal(signal.SIGINT)
        return 130
    except Exception as exc:
        print(f"QA audit failed to run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
