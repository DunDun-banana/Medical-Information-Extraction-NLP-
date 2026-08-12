from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.common.schema import Entity
from src.extraction.dictionary_matcher import AhoDictionaryMatcher
from src.section_parser import SectionParser, SectionSpan


@dataclass(frozen=True)
class TestAlias:
    surface_form: str
    canonical_name: str
    category: str
    priority: int


UNIT_PATTERN = (
    r"(?:G/L|T/L|g/L|g/dL|mg/dL|mg/L|mmol/L|µmol/L|umol/L|mEq/L|"
    r"ng/mL|pg/mL|U/L|IU/L|%|mmHg|10\^9/L|10\^12/L)"
)
NUMERIC_RESULT_RE = re.compile(
    rf"(?ix)^(?:tăng\s+từ|giảm\s+từ|từ)?\s*"
    rf"(?P<start>[+-]?\d+(?:[.,]\d+)?)"
    rf"(?:\s*(?:--?>|→|đến|tới|lên|xuống|-)\s*"
    rf"(?P<end>[+-]?\d+(?:[.,]\d+)?))?"
    rf"(?:\s*(?P<unit>{UNIT_PATTERN}))?"
)
QUALITATIVE_RESULT_RE = re.compile(
    r"(?ix)^(?:"
    r"không\s+ghi\s+nhận\s+(?:gì\s+)?bất\s+thường|"
    r"không\s+phát\s+hiện\s+(?:gì\s+)?bất\s+thường|"
    r"không\s+thấy\s+(?:gì\s+)?bất\s+thường|"
    r"không\s+có\s+gì\s+(?:đáng\s+chú\s+ý|bất\s+thường)|"
    r"dương\s+tính(?:\s+với)?(?:\s+[A-Za-zÀ-ỹ0-9%/+.-]+){0,4}|"
    r"âm\s+tính|"
    r"dưới\s+ngưỡng\s+điều\s+trị|"
    r"trên\s+ngưỡng\s+điều\s+trị|"
    r"tăng\s+cao|tăng|giảm|bình\s+thường|bất\s+thường"
    r")\b"
)
DESCRIPTIVE_RESULT_RE = re.compile(
    r"(?ix)^(?:"
    r"nhịp\s+xoang\s+chiếm\s+ưu\s+thế|"
    r"nhịp\s+xoang|"
    r"không\s+chẩn\s+đoán\s+được|"
    r"hình\s+ảnh\s+[^.;\n]{2,80}"
    r")"
)

PROTECTED_BIOCHEMICAL_EXPANSION_RE = re.compile(
    r"(?ix)\\b(?:glucose[-\\s]*6[-\\s]*phosphate\\s+dehydrogenase|"
    r"glucose[-\\s]*6[-\\s]*phosphat(?:e|ase)\\s+dehydrogenase)\\b"
)
CHEMICAL_COMPONENT_ALIASES = {"glucose", "phosphate", "phosphat", "phospho"}


def _inside_protected_biochemical_name(raw_text: str, start: int, end: int, surface: str) -> bool:
    """Reject dictionary hits that are only components of an enzyme/protein name."""
    if surface.casefold().strip() not in CHEMICAL_COMPONENT_ALIASES:
        return False
    left = max(0, start - 40)
    right = min(len(raw_text), end + 80)
    for match in PROTECTED_BIOCHEMICAL_EXPANSION_RE.finditer(raw_text[left:right]):
        absolute_start = left + match.start()
        absolute_end = left + match.end()
        if absolute_start <= start and end <= absolute_end:
            return True
    # A conservative fallback for hyphenated biochemical compounds.
    return raw_text[max(0, start - 1):start] == "-" or raw_text[end:end + 1] == "-"

CONNECTOR_RE = re.compile(
    r"(?ix)^\s*(?P<connector>"
    r":|=|là\b|kết\s+quả\s+là\b|cho\s+thấy\b|ghi\s+nhận\b|"
    r"có\s+kết\s+quả\b"
    r")?\s*"
)


class LabExtractor:
    """Extract laboratory tests and diagnostic investigations.

    The competition uses one entity type for test names, so laboratory assays,
    ECG/Holter and imaging procedure names share this extractor.
    """

    def __init__(self, aliases_path: Path):
        self.aliases = self._load_aliases(aliases_path)
        self._matcher = AhoDictionaryMatcher((alias.surface_form, alias) for alias in self.aliases)

    @staticmethod
    def _load_aliases(path: Path) -> list[TestAlias]:
        rows: list[TestAlias] = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                surface = row.get("surface_form", "").strip()
                if not surface:
                    continue
                rows.append(TestAlias(
                    surface_form=surface,
                    canonical_name=row.get("canonical_name", "").strip() or surface,
                    category=row.get("category", "laboratory").strip() or "laboratory",
                    priority=int(row.get("priority", "50") or 50),
                ))
        return sorted(rows, key=lambda x: (len(x.surface_form), x.priority), reverse=True)

    @staticmethod
    def _compile_alias(alias: str) -> re.Pattern[str]:
        return re.compile(
            rf"(?<![\wÀ-ỹ]){re.escape(alias)}(?![\wÀ-ỹ])",
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _same_line_window(raw_text: str, start: int, max_chars: int = 130) -> tuple[int, str]:
        line_end = raw_text.find("\n", start)
        if line_end < 0:
            line_end = len(raw_text)
        end = min(line_end, start + max_chars)
        return end, raw_text[start:end]

    @staticmethod
    def _trim(raw_text: str, start: int, end: int) -> tuple[int, int] | None:
        while start < end and raw_text[start].isspace():
            start += 1
        while end > start and raw_text[end - 1].isspace():
            end -= 1
        return (start, end) if start < end else None

    def _find_result(
        self,
        raw_text: str,
        name_end: int,
        next_name_start: int | None,
        alias: TestAlias,
    ) -> tuple[int, int] | None:
        window_end, window = self._same_line_window(raw_text, name_end)
        if next_name_start is not None:
            window_end = min(window_end, next_name_start)
            window = raw_text[name_end:window_end]

        # Stop before a new semicolon pair. A full stop remains because decimals
        # use periods; descriptive patterns themselves stop before sentence dots.
        semicolon = window.find(";")
        if semicolon >= 0:
            window = window[:semicolon]
        if not window.strip():
            return None

        connector = CONNECTOR_RE.match(window)
        offset = connector.end() if connector else 0
        connector_text = connector.group("connector") if connector else None
        tail = window[offset:]
        if not tail:
            return None

        # "cấy máu x2" is a test count, not a result.
        if re.match(r"(?ix)^x\s*\d+\b", tail):
            return None

        candidates: list[re.Match[str]] = []
        qualitative = QUALITATIVE_RESULT_RE.match(tail)
        descriptive = DESCRIPTIVE_RESULT_RE.match(tail)
        if qualitative:
            candidates.append(qualitative)
        if descriptive:
            candidates.append(descriptive)

        if alias.category == "laboratory":
            numeric = NUMERIC_RESULT_RE.match(tail)
            if numeric:
                has_unit = bool(numeric.group("unit"))
                # Unqualified integers after culture/test names are usually a
                # count. For laboratory names, allow no-unit decimals such as INR 1.7.
                value = numeric.group("start")
                if connector_text or has_unit or "." in value or "," in value or len(value) >= 2:
                    candidates.append(numeric)

        if not candidates:
            return None
        match = min(candidates, key=lambda item: (item.start(), -(item.end() - item.start())))
        result_start = name_end + offset + match.start()
        result_end = name_end + offset + match.end()

        result_text = raw_text[result_start:result_end]
        prefix = re.match(r"(?ix)^(?:tăng\s+từ|giảm\s+từ|từ)\s*", result_text)
        if prefix:
            result_start += prefix.end()
        return self._trim(raw_text, result_start, result_end)

    @staticmethod
    def _expand_parenthetical_name(raw_text: str, end: int) -> int:
        cursor = end
        while cursor < len(raw_text) and raw_text[cursor].isspace():
            cursor += 1
        if cursor >= len(raw_text) or raw_text[cursor] != "(":
            return end
        close = raw_text.find(")", cursor + 1, min(len(raw_text), cursor + 100))
        return close + 1 if close >= 0 else end

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        name_hits: list[tuple[int, int, TestAlias]] = []
        for start, end, alias in self._matcher.finditer(raw_text):
            if _inside_protected_biochemical_name(raw_text, start, end, alias.surface_form):
                continue
            expanded_end = self._expand_parenthetical_name(raw_text, end)
            name_hits.append((start, expanded_end, alias))

        accepted: list[tuple[int, int, TestAlias]] = []
        for hit in sorted(name_hits, key=lambda x: (x[0], -(x[1] - x[0]), -x[2].priority)):
            start, end, _ = hit
            if any(max(start, a) < min(end, b) for a, b, _ in accepted):
                continue
            accepted.append(hit)
        accepted.sort(key=lambda x: x[0])

        entities: list[Entity] = []
        for index, (start, end, alias) in enumerate(accepted):
            section = SectionParser.section_at(sections, start)
            entities.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="TÊN_XÉT_NGHIỆM",
                confidence=0.98,
                source="test_dictionary",
                section=section,
                metadata={
                    "canonical_name": alias.canonical_name,
                    "category": alias.category,
                },
            ))

            next_start = accepted[index + 1][0] if index + 1 < len(accepted) else None
            result_span = self._find_result(raw_text, end, next_start, alias)
            if result_span:
                result_start, result_end = result_span
                entities.append(Entity(
                    text=raw_text[result_start:result_end],
                    start=result_start,
                    end=result_end,
                    type="KẾT_QUẢ_XÉT_NGHIỆM",
                    confidence=0.93,
                    source="test_result_rule",
                    section=section,
                    metadata={
                        "linked_test_start": start,
                        "linked_test_text": raw_text[start:end],
                        "test_category": alias.category,
                    },
                ))
        return entities
