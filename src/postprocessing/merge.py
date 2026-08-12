from __future__ import annotations

import re
from src.common.schema import Entity


DIAGNOSIS_SECTIONS = {"PAST_HISTORY", "DIAGNOSIS", "DIAGNOSIS_EXTRA"}
SYMPTOM_SECTIONS = {"CURRENT_ILLNESS", "ADMISSION_REASON", "ONSET", "CURRENT_SYMPTOMS", "CURRENT_SYMPTOMS_EXTRA", "SYMPTOM_CHARACTERISTICS", "PRE_ADMISSION_STATUS"}
TYPE_PRIORITY = {"THUỐC": 5, "TÊN_XÉT_NGHIỆM": 4, "KẾT_QUẢ_XÉT_NGHIỆM": 3, "CHẨN_ĐOÁN": 2, "TRIỆU_CHỨNG": 1}


def _rank(entity: Entity) -> tuple[float, int, int, int]:
    source_bonus = 1 if entity.source != "self_host_llm" else 0
    return (entity.confidence, source_bonus, len(entity.candidates), entity.end - entity.start)


def _merge_same(a: Entity, b: Entity) -> Entity:
    winner, other = (a, b) if _rank(a) >= _rank(b) else (b, a)
    winner.assertions = list(dict.fromkeys(winner.assertions + other.assertions))
    winner.candidates = list(dict.fromkeys(winner.candidates + other.candidates))
    merged_meta = dict(other.metadata)
    merged_meta.update(winner.metadata)
    winner.metadata = merged_meta
    return winner


def _choose_cross_type(group: list[Entity]) -> Entity:
    rules = [e for e in group if e.source != "self_host_llm"]
    if rules:
        return max(rules, key=_rank)
    text = group[0].text
    if re.search(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|%)\b", text):
        drugs = [e for e in group if e.type == "THUỐC"]
        if drugs:
            return max(drugs, key=_rank)
    if re.search(r"(?ix)\b(?:x-?quang|ct\b|mri\b|siêu\s+âm|ecg\b|điện\s+tâm\s+đồ|xét\s+nghiệm|nội\s+soi)\b", text):
        tests = [e for e in group if e.type == "TÊN_XÉT_NGHIỆM"]
        if tests:
            return max(tests, key=_rank)
    if re.match(r"(?ix)^(?:âm\s+tính|dương\s+tính|bình\s+thường|không\s+ghi\s+nhận|\d)", text.strip()):
        results = [e for e in group if e.type == "KẾT_QUẢ_XÉT_NGHIỆM"]
        if results:
            return max(results, key=_rank)
    return max(group, key=lambda e: (_rank(e), TYPE_PRIORITY.get(e.type, 0)))


def merge_entities(entities: list[Entity]) -> list[Entity]:
    exact: dict[tuple[int, int, str], Entity] = {}
    for entity in entities:
        key = (entity.start, entity.end, entity.type)
        exact[key] = entity if key not in exact else _merge_same(exact[key], entity)

    by_span: dict[tuple[int, int], list[Entity]] = {}
    for entity in exact.values():
        by_span.setdefault((entity.start, entity.end), []).append(entity)
    candidates = [_choose_cross_type(group) if len(group) > 1 else group[0] for group in by_span.values()]

    accepted: list[Entity] = []
    for entity in sorted(candidates, key=lambda x: (x.start, -x.confidence, -(x.end - x.start), x.type)):
        conflicts = [p for p in accepted if p.type == entity.type and max(p.start, entity.start) < min(p.end, entity.end)]
        if not conflicts:
            accepted.append(entity)
            continue
        best = max([entity] + conflicts, key=_rank)
        for conflict in conflicts:
            if conflict in accepted:
                accepted.remove(conflict)
        accepted.append(best)
    return sorted(accepted, key=lambda x: (x.start, x.end, x.type))
