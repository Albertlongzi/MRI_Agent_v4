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

    def chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 384) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
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
