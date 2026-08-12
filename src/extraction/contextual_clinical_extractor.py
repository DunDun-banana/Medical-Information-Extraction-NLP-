from __future__ import annotations

import re

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.section_parser import SectionParser, SectionSpan

# V5 context rules are intentionally narrow. They target recurring Round-2
# constructions and do not attempt unrestricted clause-level NER.
G6PD = r"(?:thiếu|thiếu\s+hụt)\s+men\s+G6PD|G6PD\s+deficiency"
TEST_CUE_RE = re.compile(
    rf"(?ix)\b(?P<test>"
    rf"xét\s+nghiệm\s+(?:sàng\s+lọc\s+)?(?:{G6PD}|máu|nước\s+tiểu|dịch\s+não\s+tủy)"
    rf")\b"
)
RESULT_AFTER_LABEL_RE = re.compile(
    rf"(?ix)\bkết\s+quả\s+(?!xét\s+nghiệm\b)(?P<result>"
    rf"(?:nghi\s+ngờ|dương\s+tính|âm\s+tính|bất\s+thường|bình\s+thường|"
    rf"không\s+(?:phát\s+hiện|ghi\s+nhận|thấy))"
    rf"(?:\s+(?:{G6PD}|[^,.;\n]{{1,70}}))?"
    rf")"
)
TEST_SHOWS_RE = re.compile(
    r"(?ix)\b(?P<test>xét\s+nghiệm\s+máu)\s+(?:thường\s+)?"
    r"(?:cho\s+thấy|ghi\s+nhận|phát\s+hiện|là)\s+(?P<tail>[^.\n]{2,240})"
)

# Explicit conditions remain diagnoses even when they occur after a test cue.
DIAGNOSIS_LIKE_RESULT_RE = re.compile(
    r"(?ix)^(?:thiếu\s+máu(?:\s+do\s+tan\s+huyết)?|suy\s+thận\s+cấp|"
    r"viêm\b|nhiễm\b|bại\s+não\b|rối\s+loạn\b)"
)
LAB_FINDING_RE = re.compile(
    r"(?ix)^(?:hồng\s+cầu\s+bị\s+phá\s+hủy(?:\s+hàng\s+loạt)?|"
    r"bạch\s+cầu\s+(?:tăng|giảm)|tiểu\s+cầu\s+(?:tăng|giảm)|"
    r"men\s+gan\s+(?:tăng|giảm)|bilirubin\s+(?:tăng|giảm)|"
    r"(?:âm|dương)\s+tính|không\s+(?:phát\s+hiện|ghi\s+nhận|thấy))"
)
STOP_RESULT_RE = re.compile(r"(?ix)^(?:dẫn\s+đến|gây\s+ra|và\s+thậm\s+chí)\b")


def _trim(raw: str, start: int, end: int) -> tuple[int, int]:
    while start < end and raw[start].isspace():
        start += 1
    while end > start and (raw[end - 1].isspace() or raw[end - 1] in ",;."):
        end -= 1
    return start, end


class ContextualClinicalExtractor:
    """Context-sensitive test/result extraction for recurring article prose.

    Examples handled:
      * ``xét nghiệm thiếu men G6PD`` -> test name
      * ``kết quả nghi ngờ thiếu men G6PD`` -> test result
      * ``xét nghiệm máu thường cho thấy ...`` -> test + conservative findings

    The extractor deliberately does *not* treat ``không được phát hiện bệnh X``
    as a laboratory result because that construction can describe missed or
    conditional diagnosis rather than an observed test result.
    """

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        output: list[Entity] = []

        for match in TEST_CUE_RE.finditer(raw_text):
            start, end = _trim(raw_text, *match.span("test"))
            output.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="TÊN_XÉT_NGHIỆM",
                confidence=0.99,
                source="contextual_test_cue_v5",
                section=SectionParser.section_at(sections, start),
                metadata={"structured": True, "trusted": True, "context_rule": "explicit_test_cue"},
            ))

        for match in RESULT_AFTER_LABEL_RE.finditer(raw_text):
            start, end = _trim(raw_text, *match.span("result"))
            if start >= end:
                continue
            output.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="KẾT_QUẢ_XÉT_NGHIỆM",
                confidence=0.96,
                source="contextual_result_cue_v5",
                section=SectionParser.section_at(sections, start),
                metadata={"structured": True, "trusted": True, "context_rule": "result_label"},
            ))

        for match in TEST_SHOWS_RE.finditer(raw_text):
            test_start, test_end = _trim(raw_text, *match.span("test"))
            output.append(Entity(
                text=raw_text[test_start:test_end],
                start=test_start,
                end=test_end,
                type="TÊN_XÉT_NGHIỆM",
                confidence=0.99,
                source="contextual_test_cue_v5",
                section=SectionParser.section_at(sections, test_start),
                metadata={"structured": True, "trusted": True, "context_rule": "test_shows"},
            ))

            tail_start = match.start("tail")
            tail = match.group("tail")
            cursor = 0
            for part_match in re.finditer(r"[^,]+", tail):
                part = part_match.group(0)
                stripped = part.strip()
                if not stripped:
                    continue
                if STOP_RESULT_RE.match(stripped):
                    break
                start = tail_start + part_match.start() + (len(part) - len(part.lstrip()))
                end = tail_start + part_match.start() + len(part.rstrip())
                start, end = _trim(raw_text, start, end)
                value = raw_text[start:end]
                # Explicit disease names are handled by diagnosis extraction.
                if DIAGNOSIS_LIKE_RESULT_RE.match(value):
                    continue
                if not LAB_FINDING_RE.match(value):
                    continue
                output.append(Entity(
                    text=value,
                    start=start,
                    end=end,
                    type="KẾT_QUẢ_XÉT_NGHIỆM",
                    confidence=0.94,
                    source="contextual_lab_finding_v5",
                    section=SectionParser.section_at(sections, start),
                    metadata={
                        "structured": True,
                        "trusted": True,
                        "context_rule": "test_shows_finding",
                        "linked_test_start": test_start,
                        "linked_test_text": raw_text[test_start:test_end],
                    },
                ))

        # Exact deduplication; cross-type conflicts are resolved later.
        best: dict[tuple[int, int, str], Entity] = {}
        for entity in output:
            key = (entity.start, entity.end, entity.type)
            if key not in best or entity.confidence > best[key].confidence:
                best[key] = entity
        return sorted(best.values(), key=lambda row: (row.start, row.end, row.type))
