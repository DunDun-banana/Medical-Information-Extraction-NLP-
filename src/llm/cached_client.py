from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.llm.client import LocalChatClient


class CachedChatClient:
    def __init__(self, client: LocalChatClient, cache_dir: Path):
        self.client = client
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.client.server.model,
            "generation": self.client.generation.__dict__,
            "system": system_prompt,
            "user": user_prompt,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        key = self._key(system_prompt, user_prompt)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data["content"])
        content = self.client.chat(system_prompt, user_prompt)
        path.write_text(
            json.dumps({"content": content}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return content
