from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from src.common.schema import Entity


DOSE_RE = re.compile(r"(?ix)\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|mg/ml|%)\b")
RESULT_RE = re.compile(r"(?ix)^(?:âm\s+tính|dương\s+tính|bình\s+thường|bất\s+thường|không\s+(?:ghi\s+nhận|phát\s+hiện|thấy|có)|\d)")
STRONG_RESULT_RE = re.compile(r"(?ix)^(?:âm\s+tính|dương\s+tính|không\s+(?:ghi\s+nhận|phát\s+hiện|thấy|có))")
ACTION_RE = re.compile(r"(?ix)^(?:được\s+|tiếp\s+tục\s+|hẹn\s+|tái\s+khám|theo\s+dõi|điều\s+trị|chỉ\s+định|thực\s+hiện|chuyển\s+đến|nhập\s+viện)")
TEST_CUE_RE = re.compile(r"(?ix)\b(?:xét\s+nghiệm|chụp|x-?quang|ct\b|mri\b|siêu\s+âm|điện\s+tâm\s+đồ|ecg\b|holter|nội\s+soi|sinh\s+thiết|khám\s+chuyên\s+khoa|đánh\s+giá\s+lâm\s+sàng)\b")
DIAGNOSIS_CUE_RE = re.compile(r"(?ix)\b(?:bệnh|viêm|ung\s+thư|u\s+ác|suy|hẹp|hở|tắc|rung|xơ|hội\s+chứng|rối\s+loạn|nhiễm|xuất\s+huyết|nhồi\s+máu|gãy|tràn\s+dịch|thoát\s+vị|loét|áp\s+xe|đái\s+tháo|béo\s+phì|gút|liệt)\b")
GENERIC_RE = re.compile(r"(?ix)^(?:tình\s+trạng|triệu\s+chứng|bất\s+thường|kết\s+quả|đánh\s+giá|thăm\s+khám|sự\s+kiện)$")
SAMPLE_COUNT_RE = re.compile(r"(?ix)^(?:x\s*)?\d+\s*(?:mẫu|lần|lọ|viên)?$")
LIFESTYLE_EXPOSURE_RE = re.compile(
    r"(?ix)^(?:uống|ăn|hút|sử\s+dụng|tiêu\s+thụ|"
    r"\d+\s*(?:tách|ly|cốc))\b.*\b(?:cà\s+phê|caffeine|"
    r"rượu|bia|thuốc\s+lá|shisha)\b"
)
GENERIC_SYMPTOM_RE = re.compile(r"(?ix)^(?:triệu\s+chứng|tình\s+trạng|biểu\s+hiện)$")
GENERIC_TEST_RE = re.compile(r"(?ix)^(?:hình\s+ảnh|kết\s+quả|xét\s+nghiệm|đánh\s+giá)$")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _overlap_ratio(a: Entity, b: Entity) -> float:
    overlap = max(0, min(a.end, b.end) - max(a.start, b.start))
    return overlap / max(1, min(a.end - a.start, b.end - b.start))


def _same_line(raw_text: str, a: Entity, b: Entity) -> bool:
    left = min(a.start, b.start)
    right = max(a.end, b.end)
    return "\n" not in raw_text[left:right]


@dataclass
class FilterDecision:
    entity: Entity
    accepted: bool
    reason: str


class LLMEntityFilter:
    def filter(self, raw_text: str, llm_entities: list[Entity], rule_entities: list[Entity]) -> tuple[list[Entity], list[dict]]:
        accepted: list[Entity] = []
        report: list[dict] = []
        all_tests = [e for e in rule_entities + llm_entities if e.type == "TÊN_XÉT_NGHIỆM"]

        for entity in llm_entities:
            reason = self._reject_reason(raw_text, entity, rule_entities, all_tests)
            ok = reason is None
            if ok:
                accepted.append(entity)
            report.append({
                "accepted": ok,
                "reason": reason or "accepted",
                "entity": entity.to_debug_dict(),
            })
        return accepted, report

    def _reject_reason(self, raw_text: str, entity: Entity, rules: list[Entity], tests: list[Entity]) -> str | None:
        text = entity.text.strip()
        normalized = str(entity.metadata.get("normalized", "")).strip()
        norm = _norm(text)
        if entity.type == "CHẨN_ĐOÁN" and GENERIC_RE.match(norm):
            return "generic_phrase"
        if len(text) > 180 and entity.type != "KẾT_QUẢ_XÉT_NGHIỆM":
            return "span_too_long"
        if entity.type not in {"KẾT_QUẢ_XÉT_NGHIỆM", "TÊN_XÉT_NGHIỆM"} and text.count(".") + text.count(";") >= 2:
            return "multi_sentence_span"
        if entity.type != "KẾT_QUẢ_XÉT_NGHIỆM" and SAMPLE_COUNT_RE.match(norm):
            return "numeric_or_count_only"

        overlaps = [r for r in rules if _overlap_ratio(entity, r) >= 0.5]
        if entity.type == "CHẨN_ĐOÁN":
            incompatible = [r for r in overlaps if r.type in {"THUỐC", "TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"}]
            if any(r.start == entity.start and r.end == entity.end for r in incompatible):
                return "diagnosis_conflicts_with_rule_type"
            if incompatible and not (normalized or DIAGNOSIS_CUE_RE.search(text)):
                return "diagnosis_conflicts_with_rule_type"
            if DOSE_RE.search(text) or RESULT_RE.match(norm) or TEST_CUE_RE.search(text):
                return "diagnosis_looks_like_drug_test_or_result"
            if ACTION_RE.match(norm):
                return "diagnosis_is_action_or_plan"
            if not normalized and not DIAGNOSIS_CUE_RE.search(text) and entity.section not in {"PAST_HISTORY", "DIAGNOSIS", "DIAGNOSIS_EXTRA", "DOCUMENT_RECORD"}:
                return "diagnosis_without_support"

        elif entity.type == "TRIỆU_CHỨNG":
            if any(r.type in {"THUỐC", "TÊN_XÉT_NGHIỆM"} and r.start == entity.start and r.end == entity.end for r in overlaps):
                return "symptom_exact_conflict"
            if LIFESTYLE_EXPOSURE_RE.search(norm):
                return "symptom_is_lifestyle_exposure"
            if GENERIC_SYMPTOM_RE.fullmatch(norm):
                return "generic_symptom_phrase"
            contained_rule_symptom = any(
                r.type == "TRIỆU_CHỨNG"
                and entity.start <= r.start and r.end <= entity.end
                and (r.start, r.end) != (entity.start, entity.end)
                for r in overlaps
            )
            if contained_rule_symptom:
                return "symptom_broader_than_rule_span"
            if DOSE_RE.search(text) or TEST_CUE_RE.fullmatch(text.strip()):
                return "symptom_looks_like_drug_or_test"

        elif entity.type == "THUỐC":
            if norm in {"thuốc", "dùng thuốc", "đơn thuốc", "thuốc điều trị"}:
                return "generic_drug_phrase"
            if any(r.type == "TÊN_XÉT_NGHIỆM" and r.start == entity.start and r.end == entity.end for r in overlaps):
                return "drug_conflicts_with_test"
            if TEST_CUE_RE.search(text) or RESULT_RE.match(norm):
                return "drug_looks_like_test_or_result"

        elif entity.type == "TÊN_XÉT_NGHIỆM":
            if GENERIC_TEST_RE.fullmatch(norm):
                return "generic_test_phrase"
            if any(r.type == "THUỐC" for r in overlaps):
                return "test_conflicts_with_drug"
            if RESULT_RE.match(norm) or DOSE_RE.search(text):
                return "test_looks_like_result_or_dose"

        elif entity.type == "KẾT_QUẢ_XÉT_NGHIỆM":
            context = str(entity.metadata.get("llm_context", ""))
            context_support = bool(TEST_CUE_RE.search(context)) or any(
                t.text.casefold() in context.casefold() for t in tests if len(t.text) >= 3
            )
            nearby = any(
                abs(t.start - entity.start) <= 450 and (
                    _same_line(raw_text, t, entity)
                    or raw_text[min(t.start, entity.start):max(t.end, entity.end)].count("\n") <= 1
                )
                for t in tests if not (t.start == entity.start and t.end == entity.end)
            )
            if SAMPLE_COUNT_RE.match(norm) and not (nearby or context_support):
                return "sample_count_not_result"
            if not (nearby or context_support or STRONG_RESULT_RE.match(norm)):
                return "result_without_nearby_test"
        return None
