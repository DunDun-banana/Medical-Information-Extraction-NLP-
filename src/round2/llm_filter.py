from __future__ import annotations

import re
import unicodedata

from src.common.schema import Entity


DOSE_RE = re.compile(r"(?ix)\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|mg/ml|%)\b")
TEST_RE = re.compile(r"(?ix)\b(?:xét\s+nghiệm|x-?quang|chụp\s+ct|ct\b|mri\b|siêu\s+âm|ecg\b|điện\s+tâm\s+đồ|nội\s+soi|sinh\s+thiết|sàng\s+lọc)\b")
RESULT_RE = re.compile(r"(?ix)^(?:âm\s+tính|dương\s+tính|bình\s+thường|bất\s+thường|không\s+(?:ghi\s+nhận|phát\s+hiện|thấy|có)|\d)")
ACTION_RE = re.compile(r"(?ix)^(?:được\s+|cần\s+|nên\s+|không\s+nên|hẹn|theo\s+dõi|điều\s+trị|chỉ\s+định|thực\s+hiện|phòng\s+ngừa|tránh\s+|khuyến\s+cáo)")
GENERIC_RE = re.compile(r"(?ix)^(?:bệnh|triệu\s+chứng|dấu\s+hiệu|thuốc|xét\s+nghiệm|kết\s+quả|tình\s+trạng|biểu\s+hiện|chẩn\s+đoán)$")
LIFESTYLE_RE = re.compile(r"(?ix)^(?:uống|ăn|hút|sử\s+dụng|tiếp\s+xúc).*(?:cà\s+phê|rượu|bia|thuốc\s+lá|shisha|đậu\s+tằm|băng\s+phiến)")
PROCEDURE_RE = re.compile(r"(?ix)\b(?:phẫu\s+thuật|thủ\s+thuật|đặt\s+ống|cắt\s+bỏ|can\s+thiệp|điều\s+trị|tiêm\s+chủng)\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _overlap(a: Entity, b: Entity) -> float:
    intersection = max(0, min(a.end, b.end) - max(a.start, b.start))
    return intersection / max(1, min(a.end - a.start, b.end - b.start))


def filter_llm_entities(
    raw_text: str,
    llm_entities: list[Entity],
    rule_entities: list[Entity],
    support_entities: list[Entity] | None = None,
    min_confidence: float = 0.74,
    allow_uncorroborated_types: set[str] | None = None,
    overlap_iou: float = 0.55,
    max_chars: dict[str, int] | None = None,
    max_words: dict[str, int] | None = None,
    require_exact_support: bool = False,
) -> tuple[list[Entity], list[dict]]:
    support_entities = support_entities or []
    allow_uncorroborated_types = allow_uncorroborated_types or set()
    max_chars = max_chars or {
        "TRIỆU_CHỨNG": 60,
        "CHẨN_ĐOÁN": 70,
        "TÊN_XÉT_NGHIỆM": 70,
        "KẾT_QUẢ_XÉT_NGHIỆM": 110,
        "THUỐC": 80,
    }
    max_words = max_words or {
        "TRIỆU_CHỨNG": 8,
        "CHẨN_ĐOÁN": 9,
        "TÊN_XÉT_NGHIỆM": 10,
        "KẾT_QUẢ_XÉT_NGHIỆM": 16,
        "THUỐC": 12,
    }
    accepted: list[Entity] = []
    report: list[dict] = []
    tests = [e for e in rule_entities + support_entities + llm_entities if e.type == "TÊN_XÉT_NGHIỆM"]
    for entity in llm_entities:
        text = entity.text.strip()
        norm = _norm(text)
        same_support = any(
            row.type == entity.type and _overlap(entity, row) >= overlap_iou
            for row in rule_entities + support_entities
        )
        exact_support = any(
            row.type == entity.type and row.start == entity.start and row.end == entity.end
            for row in rule_entities + support_entities
        )
        reason = None
        if entity.confidence < min_confidence:
            reason = "below_confidence"
        elif GENERIC_RE.fullmatch(norm):
            reason = "generic_phrase"
        elif len(text) > int(max_chars.get(entity.type, 80)):
            reason = "span_too_long"
        elif len(text.split()) > int(max_words.get(entity.type, 10)):
            reason = "too_many_words"
        elif text.count("\n") > 0:
            reason = "multiline_span"
        elif entity.type in {"CHẨN_ĐOÁN", "TRIỆU_CHỨNG"} and len(re.findall(r"[,;:]", text)) >= 2:
            reason = "clause_like_span"
        elif entity.type in {"CHẨN_ĐOÁN", "TRIỆU_CHỨNG"} and ACTION_RE.match(norm):
            reason = "action_or_advice"
        elif entity.type == "TRIỆU_CHỨNG" and LIFESTYLE_RE.match(norm):
            reason = "lifestyle_or_trigger"
        elif entity.type == "CHẨN_ĐOÁN" and PROCEDURE_RE.search(norm):
            reason = "procedure_not_diagnosis"
        elif entity.type == "CHẨN_ĐOÁN" and (DOSE_RE.search(text) or TEST_RE.search(text) or RESULT_RE.match(norm)):
            reason = "diagnosis_looks_like_other_type"
        elif entity.type == "THUỐC" and (TEST_RE.search(text) or RESULT_RE.match(norm)):
            reason = "drug_looks_like_test_or_result"
        elif entity.type == "TÊN_XÉT_NGHIỆM" and (DOSE_RE.search(text) or RESULT_RE.match(norm)):
            reason = "test_looks_like_result_or_dose"
        elif entity.type == "KẾT_QUẢ_XÉT_NGHIỆM":
            nearby = any(abs(test.start - entity.start) <= 300 for test in tests if test is not entity)
            context = str(entity.metadata.get("llm_context", ""))
            if not nearby and not TEST_RE.search(context):
                reason = "result_without_test_context"
        if reason is None:
            conflicts = [row for row in rule_entities + support_entities if _overlap(entity, row) >= 0.7]
            if any(
                row.type != entity.type and row.start == entity.start and row.end == entity.end
                for row in conflicts
            ):
                reason = "same_span_supported_type_conflict"
        support_ok = exact_support if require_exact_support else same_support
        if reason is None and not support_ok and entity.type not in allow_uncorroborated_types:
            reason = "exact_corroboration_required" if require_exact_support else "corroboration_required"

        ok = reason is None
        if ok:
            entity.metadata["corroborated"] = same_support
            entity.metadata["exact_support"] = exact_support
            accepted.append(entity)
        report.append({
            "accepted": ok,
            "reason": reason or ("exactly_corroborated" if exact_support else "accepted"),
            "entity": entity.to_debug_dict(),
        })
    return accepted, report
