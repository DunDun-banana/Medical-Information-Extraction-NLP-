from __future__ import annotations

import json
import urllib.error
import urllib.request

from src.llm.config import GenerationConfig, ServerConfig


class LocalLLMError(RuntimeError):
    pass


class LocalChatClient:
    """Client for a self-hosted OpenAI-compatible chat endpoint."""

    def __init__(self, server: ServerConfig, generation: GenerationConfig):
        self.server = server
        self.generation = generation

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.server.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.generation.temperature,
            "top_p": self.generation.top_p,
            "max_tokens": self.generation.max_tokens,
            "presence_penalty": self.generation.presence_penalty,
            "seed": self.generation.seed,
            "stream": False,
        }
        if self.server.use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            self.server.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.server.timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LocalLLMError(
                f"Local LLM returned HTTP {exc.code}: {detail[:1000]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LocalLLMError(
                f"Cannot connect to local LLM endpoint "
                f"{self.server.endpoint}: {exc}"
            ) from exc
        except TimeoutError as exc:
            raise LocalLLMError(
                f"Local LLM timed out after {self.server.timeout_seconds}s"
            ) from exc

        try:
            parsed = json.loads(body)
            return str(parsed["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LocalLLMError(
                f"Unexpected local LLM response: {body[:1000]}"
            ) from exc
