from __future__ import annotations

import re
import unicodedata

from src.common.schema import Entity


GENERIC_SYMPTOM_RE = re.compile(
    r"(?ix)^(?:"
    r"cấu\s+trúc|chức\s+năng|tình\s+trạng|biểu\s+hiện|triệu\s+chứng|"
    r"sử\s+dụng|tiếp\s+xúc|ăn|uống|hút|nguy\s+cơ|nguyên\s+nhân|"
    r"bệnh\s+nhân|người\s+bệnh|các\s+chất|thói\s+quen"
    r")\b"
)
ACTION_RE = re.compile(
    r"(?ix)^(?:được|cần|nên|không\s+nên|hẹn|theo\s+dõi|điều\s+trị|"
    r"chỉ\s+định|thực\s+hiện|phòng\s+ngừa|tránh|khuyến\s+cáo)\b"
)
SENTENCE_PUNCT_RE = re.compile(r"[.!?;:]|")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _overlap_ratio(a: Entity, b: Entity) -> float:
    overlap = max(0, min(a.end, b.end) - max(a.start, b.start))
    shorter = min(a.end - a.start, b.end - b.start)
    return overlap / max(1, shorter)


def _shape_rejection(entity: Entity, max_chars: dict[str, int], max_words: dict[str, int]) -> str | None:
    text = entity.text.strip()
    norm = _norm(text)
    if len(text) < 2:
        return "span_too_short"
    if len(text) > int(max_chars.get(entity.type, 80)):
        return "span_too_long"
    if len(text.split()) > int(max_words.get(entity.type, 10)):
        return "too_many_words"
    if entity.type in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"} and ACTION_RE.match(norm):
        return "action_or_advice"
    if entity.type == "TRIỆU_CHỨNG" and GENERIC_SYMPTOM_RE.match(norm):
        return "generic_or_nonclinical_symptom"
    if entity.type in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"} and text.count("\n") > 0:
        return "multiline_span"
    if entity.type in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN"} and len(re.findall(r"[,;:]", text)) >= 2:
        return "clause_like_span"
    return None


def filter_vihealth_entities(
    entities: list[Entity],
    rule_entities: list[Entity],
    llm_entities: list[Entity],
    thresholds: dict[str, float],
    uncorroborated_types: set[str],
    overlap_iou: float,
    anchor_entities: list[Entity] | None = None,
    max_chars: dict[str, int] | None = None,
    max_words: dict[str, int] | None = None,
    require_exact_support: bool = False,
) -> tuple[list[Entity], list[dict]]:
    anchor_entities = anchor_entities or []
    max_chars = max_chars or {
        "TRIỆU_CHỨNG": 64,
        "CHẨN_ĐOÁN": 72,
        "TÊN_XÉT_NGHIỆM": 72,
        "KẾT_QUẢ_XÉT_NGHIỆM": 100,
        "THUỐC": 80,
    }
    max_words = max_words or {
        "TRIỆU_CHỨNG": 9,
        "CHẨN_ĐOÁN": 10,
        "TÊN_XÉT_NGHIỆM": 10,
        "KẾT_QUẢ_XÉT_NGHIỆM": 16,
        "THUỐC": 12,
    }
    accepted: list[Entity] = []
    report: list[dict] = []
    support_pool = rule_entities + llm_entities + anchor_entities
    for entity in entities:
        threshold = float(thresholds.get(entity.type, 0.95))
        same_rule = any(
            row.type == entity.type and _overlap_ratio(row, entity) >= overlap_iou
            for row in rule_entities
        )
        same_llm = any(
            row.type == entity.type and _overlap_ratio(row, entity) >= overlap_iou
            for row in llm_entities
        )
        same_anchor = any(
            row.type == entity.type and _overlap_ratio(row, entity) >= overlap_iou
            for row in anchor_entities
        )
        exact_support = any(
            row.type == entity.type and row.start == entity.start and row.end == entity.end
            for row in support_pool
        )
        cross_conflict = any(
            row.type != entity.type
            and row.start == entity.start
            and row.end == entity.end
            for row in support_pool
        )
        shape_reason = _shape_rejection(entity, max_chars, max_words)
        if cross_conflict:
            ok, reason = False, "same_span_cross_type_conflict"
        elif shape_reason:
            ok, reason = False, shape_reason
        elif entity.confidence < threshold:
            ok, reason = False, "below_type_threshold"
        elif require_exact_support and exact_support:
            ok, reason = True, "exactly_corroborated"
        elif not require_exact_support and (same_rule or same_llm or same_anchor):
            ok, reason = True, "corroborated"
        elif entity.type in uncorroborated_types:
            ok, reason = True, "high_confidence_uncorroborated_allowed"
        else:
            ok, reason = False, "corroboration_required"
        if ok:
            entity.metadata["corroborated_by_rule"] = same_rule
            entity.metadata["corroborated_by_llm"] = same_llm
            entity.metadata["corroborated_by_anchor"] = same_anchor
            accepted.append(entity)
        report.append({
            "accepted": ok,
            "reason": reason,
            "entity": entity.to_debug_dict(),
        })
    return accepted, report
