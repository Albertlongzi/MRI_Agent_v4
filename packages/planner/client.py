from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from urllib import error, request


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, model: str, api_key: str = "EMPTY", timeout_s: float = 20.0) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.api_key = str(api_key or "EMPTY")
        self.timeout_s = float(timeout_s)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=self._headers(),
            method=method,
        )
        with request.urlopen(req, timeout=self.timeout_s) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def health(self) -> Dict[str, Any]:
        started = time.time()
        try:
            payload = self._request_json("GET", "/models")
            data = payload.get("data") or []
            model_ids = [str(item.get("id") or "") for item in data if isinstance(item, dict)]
            return {
                "status": "ok",
                "base_url": self.base_url,
                "configured_model": self.model,
                "reachable_models": model_ids,
                "latency_ms": int((time.time() - started) * 1000),
            }
        except Exception as exc:
            return {
                "status": "error",
                "base_url": self.base_url,
                "configured_model": self.model,
                "error": str(exc),
                "latency_ms": int((time.time() - started) * 1000),
            }

    @staticmethod
    def _merge_adjacent_roles(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Collapse consecutive same-role turns into one.

        Several chat templates -- Gemma's among them -- require roles to
        alternate and reject a run of two `system` turns with
        "Conversation roles must alternate...". The planner naturally builds
        [system(persona), system(context), user], so against those models every
        call returned HTTP 400 and the planner silently fell back to its
        heuristic path with llm_status="error". Merging here keeps the callers
        free to append context turns without knowing the served model's rules.
        """
        merged: List[Dict[str, str]] = []
        for item in messages:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            if merged and merged[-1]["role"] == role:
                merged[-1]["content"] = f"{merged[-1]['content']}\n\n{content}".strip()
            else:
                merged.append({"role": role, "content": content})
        return merged

    def chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 384) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self._merge_adjacent_roles(messages),
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        started = time.time()
        try:
            response = self._request_json("POST", "/chat/completions", payload)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:2000]}") from exc
        except Exception as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        choices = response.get("choices") or []
        message = {}
        if choices and isinstance(choices[0], dict):
            message = dict(choices[0].get("message") or {})
        return {
            "content": str(message.get("content") or "").strip(),
            "raw": response,
            "latency_ms": int((time.time() - started) * 1000),
        }
