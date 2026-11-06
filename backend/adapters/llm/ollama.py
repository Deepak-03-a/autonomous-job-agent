"""Small verified Ollama client for explanations and extraction."""

import json
from typing import Any

import httpx


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        result = json.loads(payload.get("response", "{}"))
        if not isinstance(result, dict):
            raise ValueError("Ollama returned a non-object JSON response")
        return result

