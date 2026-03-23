from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime import _run_v3_tool_inproc, _serialize_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one v3 tool invocation inside a selected runtime.")
    parser.add_argument("--payload", required=True, help="Path to the JSON request file.")
    parser.add_argument("--output", required=True, help="Path to the JSON result file.")
    args = parser.parse_args()

    payload_path = Path(args.payload).resolve()
    output_path = Path(args.output).resolve()
    request = json.loads(payload_path.read_text(encoding="utf-8"))
    result = _run_v3_tool_inproc(
        str(request.get("tool_name") or ""),
        dict(request.get("args") or {}),
        case_id=str(request.get("case_id") or ""),
        run_id=str(request.get("run_id") or ""),
        run_dir=Path(str(request.get("run_dir") or "")).resolve(),
        artifacts_dir=Path(str(request.get("artifacts_dir") or "")).resolve(),
        case_state_path=Path(str(request.get("case_state_path") or "")).resolve(),
        runtime_profile=str(request.get("runtime_profile") or "control-plane"),
        launcher=str(request.get("launcher") or "subprocess"),
        invocation=[sys.executable, "-m", "packages.tools.runtime_worker"],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(_serialize_result(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
