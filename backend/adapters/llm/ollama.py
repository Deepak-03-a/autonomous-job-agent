"""Small Ollama client for grounded explanations and background extraction."""

import json
from typing import Any

import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 90.0,
        keep_alive: str = "10m",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                # Ollama accepts a JSON schema here and constrains generation
                # to the shape expected by the caller.
                "format": schema,
                "stream": False,
                "keep_alive": self.keep_alive,
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

    def explain(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        """Answer using only the supplied job and candidate facts."""
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["answer", "evidence"],
            "additionalProperties": False,
        }
        prompt = (
            "You are TalentAlign's job-match assistant. Answer the user's question "
            "using only the facts in CONTEXT. Do not invent requirements, experience, "
            "salary, company details, or actions. If context does not answer the "
            "question, say so. Return concise JSON with answer and evidence. Every "
            "evidence item must be copied verbatim from a value in CONTEXT.\n"
            f"CONTEXT: {json.dumps(context, ensure_ascii=False)}\n"
            f"QUESTION: {question}"
        )
        result = self.generate_json(prompt, schema)
        if not isinstance(result.get("answer"), str) or not isinstance(result.get("evidence"), list):
            raise ValueError("Ollama returned an invalid explanation")
        return result

