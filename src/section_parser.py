from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.section_rules import SectionMarker, detect_section_markers, load_section_aliases
from src.section_policy import section_policy


@dataclass(frozen=True)
class SectionSpan:
    canonical: str
    header: str
    header_start: int
    header_end: int
    content_start: int
    content_end: int
    line_number: int

    def contains(self, offset: int) -> bool:
        return self.header_start <= offset < self.content_end

    def to_dict(self, raw_text: str) -> dict:
        return {
            "canonical": self.canonical,
            "role": section_policy(self.canonical).get("role", "unknown"),
            "header": self.header,
            "header_position": [self.header_start, self.header_end],
            "content_position": [self.content_start, self.content_end],
            "content_preview": raw_text[self.content_start:self.content_end][:180],
            "line_number": self.line_number,
        }


class SectionParser:
    def __init__(self, aliases_path: Path):
        self.aliases = load_section_aliases(aliases_path)

    @staticmethod
    def _content_start(raw_text: str, marker: SectionMarker) -> int:
        cursor = marker.end
        while cursor < len(raw_text) and raw_text[cursor] in " \t:;-–—*":
            cursor += 1
        return cursor

    def parse(self, raw_text: str) -> list[SectionSpan]:
        markers = detect_section_markers(raw_text, self.aliases)
        if not markers:
            return [SectionSpan(
                canonical="UNKNOWN",
                header="",
                header_start=0,
                header_end=0,
                content_start=0,
                content_end=len(raw_text),
                line_number=1,
            )]

        sections: list[SectionSpan] = []
        if markers[0].start > 0 and raw_text[:markers[0].start].strip():
            sections.append(SectionSpan(
                canonical="UNKNOWN",
                header="",
                header_start=0,
                header_end=0,
                content_start=0,
                content_end=markers[0].start,
                line_number=1,
            ))

        for index, marker in enumerate(markers):
            next_start = markers[index + 1].start if index + 1 < len(markers) else len(raw_text)
            sections.append(SectionSpan(
                canonical=marker.canonical,
                header=raw_text[marker.start:marker.end],
                header_start=marker.start,
                header_end=marker.end,
                content_start=self._content_start(raw_text, marker),
                content_end=next_start,
                line_number=marker.line_number,
            ))
        return sections

    @staticmethod
    def section_at(sections: list[SectionSpan], offset: int) -> str:
        for section in sections:
            if section.contains(offset):
                return section.canonical
        return "UNKNOWN"
