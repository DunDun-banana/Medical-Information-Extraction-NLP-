from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.extraction.dictionary_matcher import AhoDictionaryMatcher
from src.section_parser import SectionParser, SectionSpan


@dataclass(frozen=True)
class DrugTerm:
    term: str
    canonical_name: str
    rxcui: str
    source: str


STRENGTH_RE = re.compile(
    r"(?ix)^\s*\d+(?:[.,]\d+)?\s*"
    r"(?:mg/ml|mcg/ml|mg|mcg|g|ml|mEq|đơn\s+vị|%)"
)
ROUTE_FREQUENCY_RE = re.compile(
    r"(?ix)^\s*(?:"
    r"po\b|iv\b|im\b|sc\b|bid\b|tid\b|qid\b|daily\b|prn\b|"
    r"mỗi\s+ngày|hằng\s+ngày|/?\s*ngày|"
    r"đường\s+uống|uống|oral\b|ngậm\s+dưới\s+lưỡi|"
    r"truyền\s+tĩnh\s+mạch|tiêm\s+tĩnh\s+mạch|tiêm\s+bắp|"
    r"khí\s+dung|dạng\s+bôi|dạng\s+dán"
    r")"
)
LEFT_GLUE_CUES = ("dùng", "uống", "sửdụng", "điềutrị", "đãdùng")
RIGHT_GLUE_CUES = ("đã", "kéo", "trong", "nhưng", "và", "để", "oral")


class DrugExtractor:
    def __init__(self, terms_path: Path):
        self.terms = self._load_terms(terms_path)
        self._matcher = AhoDictionaryMatcher((term.term, term) for term in self.terms)

    @staticmethod
    def _load_terms(path: Path) -> list[DrugTerm]:
        terms: list[DrugTerm] = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                term = row.get("term", "").strip()
                if len(term) < 3:
                    continue
                terms.append(DrugTerm(
                    term=term,
                    canonical_name=row.get("canonical_name", "").strip() or term,
                    rxcui=row.get("rxcui", "").strip(),
                    source=row.get("source", "").strip() or "dictionary",
                ))
        # Prefer longer terms such as "insulin glargine" over "insulin".
        unique: dict[str, DrugTerm] = {}
        for item in sorted(terms, key=lambda x: len(x.term), reverse=True):
            unique.setdefault(item.term.casefold(), item)
        return list(unique.values())

    @staticmethod
    def _is_boundary_or_allowed_glue(raw_text: str, start: int, end: int) -> bool:
        left_ok = start == 0 or not raw_text[start - 1].isalnum()
        right_ok = end == len(raw_text) or not raw_text[end].isalnum()

        if not left_ok:
            left_context = raw_text[max(0, start - 12):start].casefold().replace(" ", "")
            left_ok = any(left_context.endswith(cue) for cue in LEFT_GLUE_CUES)
        if not right_ok:
            right_context = raw_text[end:min(len(raw_text), end + 8)].casefold().replace(" ", "")
            right_ok = any(right_context.startswith(cue) for cue in RIGHT_GLUE_CUES)
            if end < len(raw_text) and raw_text[end].isdigit():
                right_ok = True
        return left_ok and right_ok

    @staticmethod
    def _expand_right(raw_text: str, end: int) -> int:
        cursor = end
        hard_end = min(len(raw_text), end + 55)
        # Do not cross a sentence, semicolon, comma separating another medicine,
        # or newline. Commas are allowed only before a route/frequency token.
        segment = raw_text[end:hard_end]
        # Do not split on a period because strengths can contain decimals.
        stop_positions = [pos for token in ["\n", ";"] if (pos := segment.find(token)) >= 0]
        if stop_positions:
            hard_end = end + min(stop_positions)

        strength = STRENGTH_RE.match(raw_text[cursor:hard_end])
        if strength:
            cursor += strength.end()

        # Permit multiple descriptors: "25 mg đường uống", "0.4 MG/ML bid".
        for _ in range(3):
            route = ROUTE_FREQUENCY_RE.match(raw_text[cursor:hard_end])
            if not route:
                break
            cursor += route.end()

        while cursor > end and raw_text[cursor - 1].isspace():
            cursor -= 1
        return cursor

    @staticmethod
    def _historical(section: str, raw_text: str, start: int) -> bool:
        if section == "PAST_MEDICATION":
            return True
        local = raw_text[max(0, start - 45):start].casefold()
        return bool(re.search(
            r"(?:trước\s+khi\s+nhập\s+viện|đã\s+dùng|đã\s+sử\s+dụng|"
            r"đã\s+dừng|dừng\s+.*cách\s+đây|thuốc\s+trước)",
            local,
        ))

    def extract(self, raw_text: str, sections: list[SectionSpan]) -> list[Entity]:
        hits: list[tuple[int, int, DrugTerm]] = []
        for start, end, term in self._matcher.finditer(
            raw_text, require_word_boundary=False
        ):
            if self._is_boundary_or_allowed_glue(raw_text, start, end):
                hits.append((start, end, term))

        # Longest term wins. Also drop contained duplicates from aliases.
        accepted: list[tuple[int, int, DrugTerm]] = []
        for hit in sorted(hits, key=lambda x: (x[0], -(x[1] - x[0]))):
            start, end, _ = hit
            if any(max(start, a) < min(end, b) for a, b, _ in accepted):
                continue
            accepted.append(hit)

        entities: list[Entity] = []
        for start, base_end, term in sorted(accepted, key=lambda x: x[0]):
            end = self._expand_right(raw_text, base_end)
            section = SectionParser.section_at(sections, start)
            assertions = infer_assertions(raw_text, start, end, "THUỐC", section)
            candidates = [term.rxcui] if term.rxcui else []
            entities.append(Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type="THUỐC",
                assertions=assertions,
                candidates=candidates,
                confidence=0.97,
                source="drug_dictionary",
                section=section,
                metadata={
                    "matched_term": term.term,
                    "canonical_name": term.canonical_name,
                    "dictionary_source": term.source,
                },
            ))
        return entities
