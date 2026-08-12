from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.assertion_rules import infer_assertions
from src.extraction.dictionary_matcher import AhoDictionaryMatcher
from src.common.schema import Entity
from src.section_parser import SectionParser, SectionSpan
from src.section_policy import sections_with_flag
from src.text_utils import iter_lines_with_offsets


@dataclass(frozen=True)
class DiagnosisAlias:
    surface_form: str
    canonical_name: str
    icd_codes: tuple[str, ...]
    priority: int


STRUCTURAL_SECTIONS = sections_with_flag("diagnosis_structural")
REJECT_RE = re.compile(
    r"(?ix)^(?:"
    r"chưa\s+phát\s+hiện\s+bệnh\s+lý\s+bất\s+thường|"
    r"không\s+có\s+bệnh\s+lý\s+đáng\s+kể|"
    r"điều\s+trị\s+chống\s+đông|"
    r"thở\s+oxy|"
    r"đã\s+thực\s+hiện|"
    r"đến\s+khám|"
    r"các\s+chỉ\s+số|"
    r"bị\s+|"
    r"hr\b|bp\b|vs\b|điện\s+tâm\s+đồ|ecg\b|ekg\b|holter|chụp|siêu\s+âm|xét\s+nghiệm"
    r")"
)
DIAGNOSIS_CUE_RE = re.compile(
    r"(?ix)\b(?:bệnh|ung\s+thư|u\s+ác|u\s+tuyến|viêm|suy|tăng|giảm|hẹp|tắc|"
    r"rung|xơ|khối\s+u|hội\s+chứng|rối\s+loạn|nhiễm|xuất\s+huyết|"
    r"nhồi\s+máu|gãy|tràn\s+dịch|thoát\s+vị|loét|áp\s+xe|bóc\s+tách|"
    r"đái\s+tháo|béo\s+phì|gút|graves|blốc|sarcoid|u\s+sacoit)\b"
)
GENERIC_PREFIX_RE = re.compile(
    r"(?ix)^(?:các\s+bệnh(?:\s+lý)?\s+(?:mạn|mãn)\s+tính|"
    r"bệnh(?:\s+lý)?\s+(?:mạn|mãn)\s+tính|"
    r"các\s+bệnh\s+đã\s+điều\s+trị\s+trước\s+đây|"
    r"chẩn\s+đoán(?:\s+sơ\s+bộ)?|"
    r"các\s+(?:phát\s+hiện|kết\s+quả)\s+chẩn\s+đoán\s+khác)\s*:\s*"
)


class DiagnosisExtractor:
    def __init__(self, aliases_path: Path):
        self.aliases = self._load_aliases(aliases_path)
        self._matcher = AhoDictionaryMatcher((alias.surface_form, alias) for alias in self.aliases)

    @staticmethod
    def _load_aliases(path: Path) -> list[DiagnosisAlias]:
        rows: list[DiagnosisAlias] = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                surface = row.get("surface_form", "").strip()
                if len(surface) < 3:
                    continue
                codes = tuple(
                    code.strip() for code in row.get("icd_codes", "").split("|") if code.strip()
                )
                rows.append(DiagnosisAlias(
                    surface_form=surface,
                    canonical_name=row.get("canonical_name", "").strip() or surface,
                    icd_codes=codes,
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

    def _alias_entities(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        hits: list[tuple[int, int, DiagnosisAlias]] = [
            (start, end, alias)
            for start, end, alias in self._matcher.finditer(raw_text)
        ]

        accepted: list[tuple[int, int, DiagnosisAlias]] = []
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
                type="CHẨN_ĐOÁN",
                assertions=infer_assertions(raw_text, start, end, "CHẨN_ĐOÁN", section),
                candidates=list(alias.icd_codes),
                confidence=0.95,
                source="diagnosis_dictionary",
                section=section,
                metadata={"canonical_name": alias.canonical_name},
            ))
        return entities

    @staticmethod
    def _content_spans(line_start: int, line: str) -> list[tuple[int, int]]:
        match = re.match(r"^\s*(?P<bullet>[-*•])?\s*(?P<body>.*?)\s*$", line)
        if not match or not match.group("body"):
            return []
        body_start = line_start + match.start("body")
        body = match.group("body")
        prefix = GENERIC_PREFIX_RE.match(body)
        if not match.group("bullet") and not prefix:
            return []
        if prefix:
            body_start += prefix.end()
            body = body[prefix.end():]

        spans: list[tuple[int, int]] = []
        cursor = 0
        for part in re.finditer(r"[^;]+", body):
            value = part.group(0)
            left_trim = len(value) - len(value.lstrip())
            right = len(value.rstrip())
            start = body_start + part.start() + left_trim
            end = body_start + part.start() + right
            if start < end:
                spans.append((start, end))
            cursor = part.end()
        return spans

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
            nonspace = line_start + len(line) - len(line.lstrip())
            section = SectionParser.section_at(sections, nonspace)
            if section not in STRUCTURAL_SECTIONS:
                continue
            for start, end in self._content_spans(line_start, line):
                original = raw_text[start:end]
                value = original.strip()
                start += len(original) - len(original.lstrip())
                end = start + len(value)
                while end > start and raw_text[end - 1] in ".;:,":
                    end -= 1
                while end > start and raw_text[end - 1].isspace():
                    end -= 1
                value = raw_text[start:end]
                tokens = [
                    token.casefold().strip(".,;:()[]{}")
                    for token in re.findall(r"\S+", value)
                ]
                if any(left and left == right for left, right in zip(tokens, tokens[1:])):
                    continue
                if len(value) < 4 or len(value) > 120:
                    continue
                if REJECT_RE.search(value):
                    continue
                if re.search(
                    r"(?ix)\b(?:cải\s+thiện|nặng\s+hơn|giảm\s+khi|tăng\s+khi|"
                    r"nằm\s+ngửa|đứng\s+dậy|khi\s+vận\s+động)\b",
                    value,
                ):
                    continue
                if re.search(r"(?ix)\b(?:mg|mcg|g|ml|po|iv|bid|tid|thuốc|điều\s+trị|phẫu\s+thuật)\b", value):
                    continue
                if not DIAGNOSIS_CUE_RE.search(value):
                    continue
                if self._overlap(start, end, aliases) or self._overlap(start, end, entities):
                    continue
                entities.append(Entity(
                    text=raw_text[start:end],
                    start=start,
                    end=end,
                    type="CHẨN_ĐOÁN",
                    assertions=infer_assertions(raw_text, start, end, "CHẨN_ĐOÁN", section),
                    candidates=[],
                    confidence=0.52,
                    source="diagnosis_section_fallback",
                    section=section,
                    metadata={
                        "requires_independent_support": True,
                        "rule_trust": "model_support",
                    },
                ))
        return entities

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        aliases = self._alias_entities(raw_text, sections)
        return aliases + self._structural_entities(raw_text, sections, aliases)
