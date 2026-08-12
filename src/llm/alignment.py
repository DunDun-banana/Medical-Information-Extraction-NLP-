from __future__ import annotations

import re

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.llm.config import ExtractionConfig


def _flexible_pattern(phrase: str) -> re.Pattern[str]:
    pieces = re.split(r"(\s+)", phrase)
    pattern = "".join(r"\s+" if p.isspace() else re.escape(p) for p in pieces)
    return re.compile(pattern, flags=re.IGNORECASE)


def _final_assertions(raw_text: str, start: int, end: int, entity_type: str,
                      section: str, llm_assertions: list[str], mode: str) -> list[str]:
    rule_assertions = infer_assertions(raw_text, start, end, entity_type, section)
    if mode == "llm_only":
        values = llm_assertions
    elif mode == "rule_only":
        values = rule_assertions
    elif mode == "union":
        values = llm_assertions + rule_assertions
    else:
        values = list(llm_assertions)
        if "isHistorical" in rule_assertions:
            values.append("isHistorical")
    return list(dict.fromkeys(values))


# def _matches_with_context(chunk_text: str, phrase: str, context: str) -> list[tuple[int, int]]:
#     phrase_pattern = _flexible_pattern(phrase)
#     if context: 
#         context_pattern = _flexible_pattern(context)
#         spans: list[tuple[int, int]] = []
#         for cm in context_pattern.finditer(chunk_text):
#             local = chunk_text[cm.start():cm.end()]
#             for pm in phrase_pattern.finditer(local):
#                 spans.append((cm.start() + pm.start(), cm.start() + pm.end()))
#         if spans:
#             return spans
#     return [(m.start(), m.end()) for m in phrase_pattern.finditer(chunk_text)]


# Gợi ý sửa lại hàm _matches_with_context trong alignment.py
def _matches_with_context(chunk_text: str, phrase: str, context: str) -> list[tuple[int, int]]:
    phrase_pattern = _flexible_pattern(phrase)
    
    # 1. Tìm tất cả vị trí của phrase trong chunk
    all_matches = [(m.start(), m.end()) for m in phrase_pattern.finditer(chunk_text)]
    
    if not context or len(all_matches) <= 1:
        return all_matches

    # 2. Nếu có nhiều hơn 1 vị trí, dùng context để chọn vị trí đúng nhất
    context_pattern = _flexible_pattern(context)
    for cm in context_pattern.finditer(chunk_text):
        local = chunk_text[cm.start():cm.end()]
        for pm in phrase_pattern.finditer(local):
            abs_start = cm.start() + pm.start()
            abs_end = cm.start() + pm.end()
            if (abs_start, abs_end) in all_matches:
                return [(abs_start, abs_end)] # Trả về ngay vị trí khớp ngữ cảnh
                
    return all_matches # Fallback về tất cả các vị trí nếu context không khớp



def align_rows_to_chunk(raw_text: str, chunk_text: str, chunk_start: int,
                        section: str, rows: list[dict], config: ExtractionConfig) -> list[Entity]:
    entities: list[Entity] = []
    seen: set[tuple[int, int, str]] = set()
    allowed = set(config.allowed_types)

    for row in rows:
        phrase = row["text"]
        entity_type = row["type"]
        if allowed and entity_type not in allowed:
            continue
        if not (config.min_entity_chars <= len(phrase) <= config.max_entity_chars):
            continue
        matches = _matches_with_context(chunk_text, phrase, row.get("context", ""))
        for rel_start, rel_end in matches[:config.max_occurrences_per_row]:
            start = chunk_start + rel_start
            end = chunk_start + rel_end
            key = (start, end, entity_type)
            if key in seen:
                continue
            seen.add(key)
            entity = Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type=entity_type,
                assertions=_final_assertions(
                    raw_text, start, end, entity_type, section,
                    row.get("assertions", []), config.assertion_mode,
                ),
                candidates=[],
                confidence=0.80 if row.get("context") else 0.72,
                source="self_host_llm",
                section=section,
                metadata={
                    "llm_text": phrase,
                    "normalized": row.get("normalized", ""),
                    "llm_context": row.get("context", ""),
                },
            )
            entity.validate(raw_text)
            entities.append(entity)
    return entities
