from __future__ import annotations

import re

from src.assertion_rules import infer_assertions
from src.common.schema import Entity


SOURCE_PRIORITY = {
    "baseline_anchor": 100,
    "structured_test_label": 12,
    "structured_test_result": 12,
    "structured_imaging_test": 12,
    "test_alias": 10,
    "drug_dictionary": 10,
    "symptom_dictionary": 9,
    "diagnosis_dictionary": 9,
    "self_host_llm": 6,
    "gliner": 5,
    "vihealthbert": 4,
    "rule": 3,
}
TYPE_PRIORITY = {
    "THUỐC": 5,
    "TÊN_XÉT_NGHIỆM": 4,
    "KẾT_QUẢ_XÉT_NGHIỆM": 3,
    "CHẨN_ĐOÁN": 2,
    "TRIỆU_CHỨNG": 1,
}


def _source_priority(entity: Entity) -> int:
    if entity.source in SOURCE_PRIORITY:
        return SOURCE_PRIORITY[entity.source]
    if entity.source.endswith("_fallback"):
        return 1
    if "drug" in entity.source:
        return 8
    if "test" in entity.source or "lab" in entity.source:
        return 8
    if "dictionary" in entity.source:
        return 8
    return 3


def _rank(entity: Entity) -> tuple[float, int, int]:
    support = sum(
        int(bool(entity.metadata.get(key)))
        for key in ("corroborated_by_rule", "corroborated_by_llm", "corroborated_by_anchor")
    )
    # V5 does not reward shorter spans by default. Boundary repair handles
    # trusted extensions before this merge step.
    return (float(entity.confidence) + 0.08 * support, _source_priority(entity), 0)


def _combine_evidence(winner: Entity, other: Entity) -> Entity:
    # Preserve the assertion decision of the higher-scoring baseline. The new
    # pipeline lost assertion score partly because it recomputed or unioned noisy
    # family/history labels. Other sources only provide provenance for anchors.
    if winner.source != "baseline_anchor":
        winner.assertions = list(dict.fromkeys(winner.assertions + other.assertions))
    winner.candidates = list(dict.fromkeys(winner.candidates + other.candidates))
    sources = []
    for row in (winner, other):
        prior = row.metadata.get("supporting_sources", [])
        if isinstance(prior, list):
            sources.extend(str(value) for value in prior)
        sources.append(row.source)
    winner.metadata["supporting_sources"] = list(dict.fromkeys(sources))
    return winner


def _choose_same_type(a: Entity, b: Entity, prefer_shorter_within: float) -> Entity:
    # The previous higher-scoring submission is a stability anchor. New models may
    # corroborate it, but they do not replace its span merely due to confidence.
    if a.source == "baseline_anchor" or b.source == "baseline_anchor":
        winner, other = (a, b) if a.source == "baseline_anchor" else (b, a)
        return _combine_evidence(winner, other)
    score_a, score_b = float(a.confidence), float(b.confidence)
    if abs(score_a - score_b) <= prefer_shorter_within and len(a.text) != len(b.text):
        winner, other = (a, b) if len(a.text) < len(b.text) else (b, a)
    else:
        winner, other = (a, b) if _rank(a) >= _rank(b) else (b, a)
    return _combine_evidence(winner, other)


def _choose_cross_type(group: list[Entity]) -> Entity:
    anchors = [row for row in group if row.source == "baseline_anchor"]
    if anchors:
        winner = max(anchors, key=_rank)
        for row in group:
            if row is not winner and row.type == winner.type:
                _combine_evidence(winner, row)
        return winner
    text = group[0].text.strip()
    if re.search(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|%)\b", text):
        drugs = [row for row in group if row.type == "THUỐC"]
        if drugs:
            return max(drugs, key=_rank)
    if re.search(r"(?ix)\b(?:xét\s+nghiệm|x-?quang|ct\b|mri\b|siêu\s+âm|ecg\b|nội\s+soi|sinh\s+thiết)\b", text):
        tests = [row for row in group if row.type == "TÊN_XÉT_NGHIỆM"]
        if tests:
            return max(tests, key=_rank)
    if re.match(r"(?ix)^(?:âm\s+tính|dương\s+tính|bình\s+thường|bất\s+thường|không\s+(?:ghi\s+nhận|phát\s+hiện)|\d)", text):
        results = [row for row in group if row.type == "KẾT_QUẢ_XÉT_NGHIỆM"]
        if results:
            return max(results, key=_rank)
    votes: dict[str, float] = {}
    for row in group:
        votes[row.type] = votes.get(row.type, 0.0) + 1.0 + 0.2 * _source_priority(row)
    best_type = max(votes, key=lambda typ: (votes[typ], TYPE_PRIORITY.get(typ, 0)))
    return max((row for row in group if row.type == best_type), key=_rank)


def merge_round2_entities(
    raw_text: str,
    entities: list[Entity],
    prefer_shorter_within: float = 0.08,
    assertion_mode: str = "preserve_source",
) -> list[Entity]:
    exact_type: dict[tuple[int, int, str], Entity] = {}
    for entity in entities:
        key = (entity.start, entity.end, entity.type)
        if key not in exact_type:
            exact_type[key] = entity
        else:
            exact_type[key] = _choose_same_type(exact_type[key], entity, prefer_shorter_within)

    by_span: dict[tuple[int, int], list[Entity]] = {}
    for entity in exact_type.values():
        by_span.setdefault((entity.start, entity.end), []).append(entity)
    candidates = [
        _choose_cross_type(group) if len(group) > 1 else group[0]
        for group in by_span.values()
    ]

    accepted: list[Entity] = []
    for entity in sorted(candidates, key=lambda row: (row.start, row.end, row.type)):
        conflicts = [
            row for row in accepted
            if row.type == entity.type and max(row.start, entity.start) < min(row.end, entity.end)
        ]
        if not conflicts:
            accepted.append(entity)
            continue
        winner = entity
        for conflict in conflicts:
            winner = _choose_same_type(winner, conflict, prefer_shorter_within)
        for conflict in conflicts:
            accepted.remove(conflict)
        accepted.append(winner)

    for entity in accepted:
        if assertion_mode == "rule_only":
            entity.assertions = infer_assertions(
                raw_text, entity.start, entity.end, entity.type, entity.section
            )
        elif assertion_mode == "evidence_union":
            entity.assertions = list(dict.fromkeys(
                entity.assertions
                + infer_assertions(raw_text, entity.start, entity.end, entity.type, entity.section)
            ))
        elif assertion_mode != "preserve_source":
            raise ValueError(f"Unsupported assertion mode: {assertion_mode}")
        entity.candidates = list(dict.fromkeys(entity.candidates))[:1]
        entity.validate(raw_text)
    return sorted(accepted, key=lambda row: (row.start, row.end, row.type))
