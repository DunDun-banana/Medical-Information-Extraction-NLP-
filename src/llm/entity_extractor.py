from __future__ import annotations

from dataclasses import dataclass
import re

from src.common.schema import Entity
from src.llm.alignment import align_rows_to_chunk
from src.llm.client import LocalChatClient
from src.llm.config import ExtractionConfig
from src.llm.parser import parse_entity_payload
from src.llm.prompts import SYSTEM_PROMPT, build_system_prompt, build_user_prompt
from src.section_parser import SectionSpan


@dataclass(frozen=True)
class TextChunk:
    section: str
    start: int
    end: int
    text: str


def _split_range(raw_text: str, start: int, end: int, label: str,
                 max_chars: int, overlap: int) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    cursor = start
    while cursor < end:
        tentative = min(cursor + max_chars, end)
        if tentative < end:
            newline = raw_text.rfind("\n", cursor + max_chars // 2, tentative)
            if newline > cursor:
                tentative = newline + 1
        if tentative <= cursor:
            tentative = min(cursor + max_chars, end)
        chunks.append(TextChunk(label, cursor, tentative, raw_text[cursor:tentative]))
        if tentative >= end:
            break
        cursor = max(cursor + 1, tentative - max(0, overlap))
    return chunks


def _section_coverage(raw_text: str, sections: list[SectionSpan], allowed: set[str]) -> float:
    intervals = [(s.content_start, s.content_end) for s in sections if not allowed or s.canonical in allowed]
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    covered = sum(max(0, b - a) for a, b in merged)
    return covered / max(1, len(raw_text))


PROMPT_GROUP_TYPES = {
    "clinical": {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN", "THUỐC"},
    "labs": {"TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"},
    "all": {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN", "THUỐC", "TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"},
}


class LLMEntityExtractor:
    def __init__(self, client: LocalChatClient, config: ExtractionConfig):
        self.client = client
        self.config = config
        self.record_header_re = re.compile(config.record_header_pattern) if config.record_header_pattern else None

    def _record_chunks(self, raw_text: str) -> list[TextChunk]:
        if not self.record_header_re:
            return []
        matches = list(self.record_header_re.finditer(raw_text))
        if not matches:
            return []
        chunks: list[TextChunk] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
            chunks.extend(_split_range(
                raw_text, start, end, "DOCUMENT_RECORD",
                self.config.max_chunk_chars, self.config.chunk_overlap_chars,
            ))
        return chunks

    def _section_chunks(self, raw_text: str, sections: list[SectionSpan]) -> list[TextChunk]:
        allowed = set(self.config.sections)
        chunks: list[TextChunk] = []
        for section in sections:
            if allowed and section.canonical not in allowed:
                continue
            chunks.extend(_split_range(
                raw_text, section.content_start, section.content_end, section.canonical,
                self.config.max_chunk_chars, self.config.chunk_overlap_chars,
            ))
        return chunks

    def selected_chunks(self, raw_text: str, sections: list[SectionSpan]) -> list[TextChunk]:
        strategy = self.config.chunk_strategy
        record_chunks = self._record_chunks(raw_text)
        if strategy == "records":
            return record_chunks or _split_range(raw_text, 0, len(raw_text), "DOCUMENT", self.config.max_chunk_chars, self.config.chunk_overlap_chars)
        if strategy == "document":
            return _split_range(raw_text, 0, len(raw_text), "DOCUMENT", self.config.max_chunk_chars, self.config.chunk_overlap_chars)
        if strategy == "auto" and len(record_chunks) >= 2:
            return record_chunks
        if strategy in {"auto", "sections"}:
            coverage = _section_coverage(raw_text, sections, set(self.config.sections))
            if strategy == "sections" or coverage >= self.config.document_fallback_min_coverage:
                chunks = self._section_chunks(raw_text, sections)
                if chunks:
                    return chunks
        return _split_range(raw_text, 0, len(raw_text), "DOCUMENT", self.config.max_chunk_chars, self.config.chunk_overlap_chars)

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> tuple[list[Entity], list[dict]]:
        entities: list[Entity] = []
        traces: list[dict] = []
        chunks = self.selected_chunks(raw_text, sections)
        groups = (
            self.config.prompt_groups or ("clinical", "labs")
            if self.config.split_prompt_by_type
            else ("all",)
        )
        for chunk in chunks:
            for prompt_group in groups:
                system_prompt = build_system_prompt(prompt_group)
                prompt = build_user_prompt(
                    chunk.section,
                    chunk.text,
                    prompt_group=prompt_group,
                )
                raw_output = self.client.chat(system_prompt, prompt)
                parsed_rows = parse_entity_payload(raw_output)
                allowed_for_group = PROMPT_GROUP_TYPES[prompt_group]
                rows = [
                    row for row in parsed_rows
                    if row["type"] in allowed_for_group
                ]
                aligned = align_rows_to_chunk(
                    raw_text, chunk.text, chunk.start, chunk.section, rows, self.config
                )
                entities.extend(aligned)
                traces.append({
                    "section": chunk.section,
                    "prompt_group": prompt_group,
                    "position": [chunk.start, chunk.end],
                    "raw_output": raw_output,
                    "parsed_rows": rows,
                    "dropped_wrong_group_rows": len(parsed_rows) - len(rows),
                    "aligned_entities": [e.to_debug_dict() for e in aligned],
                })
        return entities, traces
