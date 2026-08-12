from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.assertion_rules import infer_assertions
from src.extraction.dictionary_matcher import AhoDictionaryMatcher
from src.common.schema import Entity
from src.section_parser import SectionParser, SectionSpan
from src.section_policy import section_policy, sections_with_flag
from src.text_utils import iter_lines_with_offsets


@dataclass(frozen=True)
class SymptomAlias:
    surface_form: str
    canonical_name: str
    priority: int


STRUCTURAL_SECTIONS = sections_with_flag("symptom_structural")
SYMPTOM_CUE_RE = re.compile(
    r"(?ix)\b(?:đau|khó|mệt|ho|sốt|fever|chảy|sưng|ngứa|yếu|liệt|mờ|"
    r"ngất|máu|nôn|tiêu\s+chảy|tê|choáng|phù|hạ\s+huyết\s+áp|"
    r"nhịp\s+nhanh|suy\s+nhược|vụng\s+về|mất\s+ý\s+thức|"
    r"không\s+tự\s+chủ|hồi\s+hộp|đánh\s+trống|thở|đờm|ban\s+đỏ)\b"
)
GENERIC_LABELS = {
    "vị trí", "mức độ", "mức độ nghiêm trọng", "thời gian", "khởi phát",
    "các triệu chứng liên quan", "triệu chứng liên quan",
}
GENERIC_MEASUREMENT_LABELS = {
    "nhịp thở", "nhịp tim", "mạch", "huyết áp", "nhiệt độ", "spo2",
    "độ bão hòa oxy", "cân nặng", "chiều cao",
}
REJECT_LINE_RE = re.compile(
    r"(?ix)\b(?:được\s+chỉ\s+định|được\s+thăm\s+khám|bác\s+sĩ|"
    r"xét\s+nghiệm|chụp|siêu\s+âm|điện\s+tâm\s+đồ|ecg|holter|"
    r"điều\s+trị|dùng\s+thuốc|mg\b|g\b|ml\b|không\s+liên\s+quan\s+đến)"
)


class SymptomExtractor:
    def __init__(self, aliases_path: Path):
        self.aliases = self._load_aliases(aliases_path)
        self._matcher = AhoDictionaryMatcher((alias.surface_form, alias) for alias in self.aliases)

    @staticmethod
    def _load_aliases(path: Path) -> list[SymptomAlias]:
        rows: list[SymptomAlias] = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                surface = row.get("surface_form", "").strip()
                if len(surface) < 2:
                    continue
                rows.append(SymptomAlias(
                    surface_form=surface,
                    canonical_name=row.get("canonical_name", "").strip() or surface,
                    priority=int(row.get("priority", "50") or 50),
                ))
        return sorted(rows, key=lambda x: (len(x.surface_form), x.priority), reverse=True)

    @staticmethod
    def _compile(surface: str) -> re.Pattern[str]:
        return re.compile(
            rf"(?<![\wÀ-ỹ]){re.escape(surface)}(?![\wÀ-ỹ])",
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _overlap(start: int, end: int, entities: list[Entity]) -> bool:
        return any(max(start, e.start) < min(end, e.end) for e in entities)

    @staticmethod
    def _line_content_span(line_start: int, line: str) -> tuple[int, int, str] | None:
        match = re.match(r"^\s*(?:[-*•]\s*)?(?P<body>.*?)\s*$", line)
        if not match:
            return None
        body = match.group("body")
        if not body:
            return None
        start = line_start + match.start("body")
        end = line_start + match.end("body")
        return start, end, body

    @staticmethod
    def _clean_structural(raw_text: str, start: int, end: int, section: str) -> tuple[int, int] | None:
        text = raw_text[start:end]

        # In characteristic sections, the phrase before ':' is normally the
        # symptom name. Generic labels use the phrase after ':'.
        colon = text.find(":")
        if colon >= 0:
            left = text[:colon].strip()
            right = text[colon + 1:].strip()
            if left.casefold() in GENERIC_LABELS and right:
                relative = text.find(right, colon + 1)
                start += relative
                text = right
            elif left:
                text = left
                end = start + len(text)

        # Remove common narrative prefixes but preserve the exact raw substring.
        prefix = re.match(
            r"(?ix)^(?:bệnh\s+nhân|người\s+bệnh|hiện\s+tại|hiện|tiếp\s+tục|"
            r"cảm\s+thấy|xuất\s+hiện|có\s+biểu\s+hiện|có)\s+",
            text,
        )
        if prefix:
            start += prefix.end()
            text = raw_text[start:end]

        # Remove a trailing temporal parenthesis such as "(khởi phát lúc 17 giờ)".
        trailing = re.search(r"\s*\((?:khởi\s+phát|bắt\s+đầu|từ)\b[^)]*\)\s*$", text, re.I)
        if trailing:
            end = start + trailing.start()
            text = raw_text[start:end]

        while start < end and raw_text[start].isspace():
            start += 1
        while end > start and raw_text[end - 1].isspace():
            end -= 1
        # Terminal punctuation is not part of a medical mention and harms
        # exact-span agreement with the anchor submission.
        while end > start and raw_text[end - 1] in ".;:,":
            end -= 1
        while end > start and raw_text[end - 1].isspace():
            end -= 1
        if end <= start:
            return None

        value = raw_text[start:end]
        if len(value) < 2 or len(value) > 90:
            return None
        if value.casefold() in GENERIC_LABELS | GENERIC_MEASUREMENT_LABELS:
            return None
        # Reject obvious duplicated OCR/template tokens such as "phù phù".
        tokens = [
            token.casefold().strip(".,;:()[]{}")
            for token in re.findall(r"\S+", value)
        ]
        if any(left and left == right for left, right in zip(tokens, tokens[1:])):
            return None
        if REJECT_LINE_RE.search(value):
            return None
        if re.match(r"(?ix)^(?:không|chưa|n/a\b|không\s+rõ\b)", value):
            return None
        if re.search(r"(?ix)\b(?:bình\s+thường|đến\s+bệnh\s+viện|sonde|nhịn\s+ăn|lovenox)\b", value):
            return None
        if not SYMPTOM_CUE_RE.search(value):
            return None
        # Long multi-clause prose is unsafe as a single symptom span.
        if value.count(",") >= 2 or "." in value:
            return None
        return start, end

    def _alias_entities(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        hits: list[tuple[int, int, SymptomAlias]] = []
        for start, end, alias in self._matcher.finditer(raw_text):
            section = SectionParser.section_at(sections, start)
            # Section aliases now act as a precision policy, not merely
            # metadata. In clinical symptom sections all curated aliases are
            # allowed. In FAQ answers/unknown prose only specific, high-priority
            # aliases are retained so generic words such as "đau" or "yếu" do
            # not create large numbers of false positives.
            min_priority = int(
                section_policy(section).get("symptom_alias_min_priority", 110)
            )
            if alias.priority < min_priority:
                continue
            hits.append((start, end, alias))

        accepted: list[tuple[int, int, SymptomAlias]] = []
        for hit in sorted(hits, key=lambda x: (x[0], -(x[1] - x[0]), -x[2].priority)):
            start, end, _ = hit
            if any(max(start, a) < min(end, b) for a, b, _ in accepted):
                continue
            accepted.append(hit)

        entities: list[Entity] = []
        for start, end, alias in sorted(accepted, key=lambda x: x[0]):
            section = SectionParser.section_at(sections, start)
            entities.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="TRIỆU_CHỨNG",
                assertions=infer_assertions(raw_text, start, end, "TRIỆU_CHỨNG", section),
                confidence=0.94,
                source="symptom_dictionary",
                section=section,
                metadata={
                    "canonical_name": alias.canonical_name,
                    "section_role": section_policy(section).get("role", "unknown"),
                    "rule_trust": section_policy(section).get("symptom_rule_trust", "model_support"),
                    "requires_independent_support": (
                        section_policy(section).get("symptom_rule_trust", "model_support")
                        != "trusted"
                    ),
                },
            ))
        return entities

    def _structural_entities(
        self,
        raw_text: str,
        sections: list[SectionSpan],
        aliases: list[Entity],
    ) -> list[Entity]:
        entities: list[Entity] = []
        for _, line_start, line_end, line in iter_lines_with_offsets(raw_text):
            if any(max(line_start, item.header_start) < min(line_end, item.header_end) for item in sections):
                continue
            section = SectionParser.section_at(sections, line_start + len(line) - len(line.lstrip()))
            if section not in STRUCTURAL_SECTIONS:
                continue
            span = self._line_content_span(line_start, line)
            if span is None:
                continue
            start, end, _ = span
            cleaned = self._clean_structural(raw_text, start, end, section)
            if cleaned is None:
                continue
            start, end = cleaned
            if self._overlap(start, end, aliases) or self._overlap(start, end, entities):
                continue
            entities.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="TRIỆU_CHỨNG",
                assertions=infer_assertions(raw_text, start, end, "TRIỆU_CHỨNG", section),
                confidence=0.55,
                source="symptom_section_fallback",
                section=section,
                metadata={
                    "section_role": section_policy(section).get("role", "unknown"),
                    "rule_trust": "model_support",
                    "requires_independent_support": True,
                },
            ))
        return entities

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        aliases = self._alias_entities(raw_text, sections)
        return aliases + self._structural_entities(raw_text, sections, aliases)
