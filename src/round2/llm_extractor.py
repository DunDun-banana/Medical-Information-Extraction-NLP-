from __future__ import annotations

import re
from dataclasses import dataclass

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.llm.cached_client import CachedChatClient
from src.llm.parser import parse_entity_payload
from src.round2.prompts import SYSTEM_PROMPT, build_user_prompt
from src.round2.semantic_chunks import SemanticChunk, semantic_chunks
from src.section_parser import SectionParser, SectionSpan


@dataclass(frozen=True)
class LLMExtractionOptions:
    max_chars: int = 1800
    overlap_chars: int = 120
    min_medical_signal: int = 1
    min_entity_chars: int = 2
    max_entity_chars: int = 180
    max_occurrences_per_row: int = 4
    allowed_types: tuple[str, ...] = ()


def _flexible_pattern(phrase: str) -> re.Pattern[str]:
    pieces = re.split(r"(\s+)", phrase)
    pattern = "".join(r"\s+" if piece.isspace() else re.escape(piece) for piece in pieces)
    return re.compile(pattern, flags=re.IGNORECASE)


def _matches_with_context(text: str, phrase: str, context: str) -> list[tuple[int, int]]:
    phrase_pattern = _flexible_pattern(phrase)
    all_matches = [(m.start(), m.end()) for m in phrase_pattern.finditer(text)]
    if not context or len(all_matches) <= 1:
        return all_matches
    context_pattern = _flexible_pattern(context)
    selected: list[tuple[int, int]] = []
    for context_match in context_pattern.finditer(text):
        local = text[context_match.start():context_match.end()]
        for phrase_match in phrase_pattern.finditer(local):
            selected.append((
                context_match.start() + phrase_match.start(),
                context_match.start() + phrase_match.end(),
            ))
    return selected or all_matches


def _proposals_in_chunk(chunk: SemanticChunk, proposals: list[Entity]) -> list[dict]:
    rows = []
    for entity in proposals:
        if max(chunk.start, entity.start) >= min(chunk.end, entity.end):
            continue
        rows.append({
            "text": entity.text,
            "type": entity.type,
            "source": entity.source,
            "confidence": entity.confidence,
        })
    return rows


def _supported(start: int, end: int, typ: str, proposals: list[Entity]) -> tuple[bool, bool]:
    exact = any(row.type == typ and row.start == start and row.end == end for row in proposals)
    overlap = any(
        row.type == typ and max(row.start, start) < min(row.end, end)
        for row in proposals
    )
    return exact, overlap


class Round2LLMExtractor:
    def __init__(self, client: CachedChatClient, options: LLMExtractionOptions):
        self.client = client
        self.options = options

    def extract(
        self,
        raw_text: str,
        proposals: list[Entity],
        sections: list[SectionSpan] | None = None,
    ) -> tuple[list[Entity], list[dict]]:
        sections = sections or []
        chunks = semantic_chunks(
            raw_text,
            max_chars=self.options.max_chars,
            overlap_chars=self.options.overlap_chars,
            min_medical_signal=self.options.min_medical_signal,
            sections=sections,
        )
        allowed = set(self.options.allowed_types)
        entities: list[Entity] = []
        traces: list[dict] = []
        seen: set[tuple[int, int, str]] = set()

        for chunk in chunks:
            hints = _proposals_in_chunk(chunk, proposals)
            user_prompt = build_user_prompt(chunk.heading, chunk.text, hints)
            raw_output = self.client.chat(SYSTEM_PROMPT, user_prompt)
            rows = parse_entity_payload(raw_output)
            aligned = 0
            for row in rows:
                phrase = row["text"]
                entity_type = row["type"]
                if allowed and entity_type not in allowed:
                    continue
                if not self.options.min_entity_chars <= len(phrase) <= self.options.max_entity_chars:
                    continue
                matches = _matches_with_context(chunk.text, phrase, row.get("context", ""))
                for rel_start, rel_end in matches[:self.options.max_occurrences_per_row]:
                    start = chunk.start + rel_start
                    end = chunk.start + rel_end
                    key = (start, end, entity_type)
                    if key in seen:
                        continue
                    seen.add(key)
                    exact_support, overlap_support = _supported(start, end, entity_type, proposals)
                    section = SectionParser.section_at(sections, start) if sections else "UNKNOWN"
                    confidence = 0.90 if exact_support else (0.84 if overlap_support else 0.72)
                    entity = Entity(
                        text=raw_text[start:end],
                        start=start,
                        end=end,
                        type=entity_type,
                        assertions=infer_assertions(raw_text, start, end, entity_type, section),
                        confidence=confidence,
                        source="self_host_llm",
                        section=section,
                        metadata={
                            "normalized": row.get("normalized", ""),
                            "llm_context": row.get("context", ""),
                            "chunk_heading": chunk.heading,
                            "proposal_exact_support": exact_support,
                            "proposal_overlap_support": overlap_support,
                        },
                    )
                    entity.validate(raw_text)
                    entities.append(entity)
                    aligned += 1
            traces.append({
                "start": chunk.start,
                "end": chunk.end,
                "heading": chunk.heading,
                "signal": chunk.signal,
                "proposal_count": len(hints),
                "parsed_rows": len(rows),
                "aligned_entities": aligned,
                "raw_output": raw_output,
            })
        return entities, traces
