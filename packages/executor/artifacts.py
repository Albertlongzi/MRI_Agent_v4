from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


def _slug(text: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(text or ""))
    while "--" in raw:
        raw = raw.replace("--", "-")
    return raw.strip("-") or "artifact"


def _svg_text(lines: Sequence[str], *, title: str, accent: str = "#9c4f2f") -> str:
    safe_lines = [str(line) for line in lines]
    height = max(160, 48 + 24 * len(safe_lines))
    body = []
    for idx, line in enumerate(safe_lines):
        y = 92 + idx * 24
        body.append(f'<text x="32" y="{y}" fill="#1f1c18" font-size="16">{_escape(line)}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{height}" viewBox="0 0 960 {height}">'
        f'<rect x="0" y="0" width="960" height="{height}" rx="24" fill="#f8f3ea"/>'
        f'<rect x="24" y="24" width="912" height="{height - 48}" rx="18" fill="#fffdf8" stroke="#d8d0c2"/>'
        f'<rect x="24" y="24" width="912" height="8" rx="4" fill="{accent}"/>'
        f'<text x="32" y="66" fill="#1f1c18" font-size="24" font-weight="700">{_escape(title)}</text>'
        + "".join(body)
        + "</svg>"
    )


def _escape(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@dataclass(frozen=True)
class ArtifactWriteResult:
    path: Path
    uri: str
    mime_type: str


class ArtifactWriter:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative_path: str, payload: Any) -> ArtifactWriteResult:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ArtifactWriteResult(path=path, uri=str(path.relative_to(self.root.parent)), mime_type="application/json")

    def write_text(self, relative_path: str, text: str) -> ArtifactWriteResult:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or "") + "\n", encoding="utf-8")
        return ArtifactWriteResult(path=path, uri=str(path.relative_to(self.root.parent)), mime_type="text/plain")

    def write_svg(self, relative_path: str, *, title: str, lines: Sequence[str], accent: str = "#9c4f2f") -> ArtifactWriteResult:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_svg_text(lines, title=title, accent=accent), encoding="utf-8")
        return ArtifactWriteResult(path=path, uri=str(path.relative_to(self.root.parent)), mime_type="image/svg+xml")

    def _resolve(self, relative_path: str) -> Path:
        rel = str(relative_path or "").lstrip("/").replace("\\", "/")
        return self.root / rel


def make_node_artifact_dir(*, graph_id: str, step_index: int, node_id: str) -> str:
    return f"{_slug(graph_id)}/{step_index:02d}_{_slug(node_id)}"
