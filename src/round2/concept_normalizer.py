from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from src.common.schema import Entity
from src.llm.cached_client import CachedChatClient
from src.round2.prompts import NORMALIZATION_PROMPT


class ConceptNormalizer:
    def __init__(
        self,
        client: CachedChatClient,
        cache_path: Path,
        batch_size: int = 24,
        types: Iterable[str] = ("CHẨN_ĐOÁN", "THUỐC"),
    ):
        self.client = client
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.batch_size = max(1, int(batch_size))
        self.types = set(types)
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.cache = {}
        else:
            self.cache = {}

    @staticmethod
    def _key(entity: Entity) -> str:
        return f"{entity.type}\t{entity.text.casefold().strip()}"

    @staticmethod
    def _parse(raw: str) -> dict[int, str]:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            payload = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return {}
        output: dict[int, str] = {}
        for row in payload.get("items", []):
            try:
                item_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            output[item_id] = str(row.get("normalized", "")).strip()[:160]
        return output

    def normalize(self, entities: list[Entity]) -> dict:
        pending: list[Entity] = []
        cache_hits = 0
        for entity in entities:
            if entity.type not in self.types:
                continue
            existing = str(
                entity.metadata.get("normalized")
                or entity.metadata.get("canonical_name")
                or ""
            ).strip()
            if existing:
                continue
            key = self._key(entity)
            if key in self.cache:
                entity.metadata["normalized"] = self.cache[key]
                entity.metadata["normalization_method"] = "cache"
                cache_hits += 1
            else:
                pending.append(entity)

        unique: dict[str, Entity] = {}
        for entity in pending:
            unique.setdefault(self._key(entity), entity)
        rows = list(unique.values())
        calls = 0
        for left in range(0, len(rows), self.batch_size):
            batch = rows[left:left + self.batch_size]
            items = [
                {"id": index + 1, "type": entity.type, "text": entity.text}
                for index, entity in enumerate(batch)
            ]
            user = "/no_think\nINPUT=" + json.dumps(items, ensure_ascii=False) + "\nOUTPUT:\n"
            raw = self.client.chat(NORMALIZATION_PROMPT, user)
            normalized = self._parse(raw)
            calls += 1
            for index, entity in enumerate(batch, 1):
                value = normalized.get(index, "")
                key = self._key(entity)
                self.cache[key] = value

        # Apply newly cached values to all duplicate mentions.
        for entity in pending:
            value = self.cache.get(self._key(entity), "")
            entity.metadata["normalized"] = value
            entity.metadata["normalization_method"] = "qwen_batch" if value else "qwen_empty"

        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {
            "eligible": len(pending) + cache_hits,
            "cache_hits": cache_hits,
            "unique_llm_items": len(rows),
            "llm_calls": calls,
        }
