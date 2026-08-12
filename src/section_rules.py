from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import yaml

from src.text_utils import iter_lines_with_offsets


@dataclass(frozen=True)
class SectionMarker:
    canonical: str
    alias: str
    start: int
    end: int
    line_number: int
    raw_header: str




DYNAMIC_HEADER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FAQ_SYMPTOMS", re.compile(
        r"(?ix)^(?:triệu\s+chứng|dấu\s+hiệu|biểu\s+hiện)\b.{0,90}$"
    )),
    ("FAQ_RISK_FACTORS", re.compile(
        r"(?ix)^(?:nguyên\s+nhân|(?:các\s+)?yếu\s+tố(?:\s+nguy\s+cơ|\s+nghi\s+ngờ|\s+liên\s+quan)?)\b.{0,90}$"
    )),
    ("FAQ_TREATMENT", re.compile(
        r"(?ix)^(?:điều\s+trị|liệu\s+pháp|phương\s+pháp\s+điều\s+trị|"
        r"cách\s+điều\s+trị|hướng\s+điều\s+trị)\b.{0,90}$"
    )),
    ("FAQ_DIAGNOSIS", re.compile(
        r"(?ix)^(?:chẩn\s+đoán|cách\s+chẩn\s+đoán)\b.{0,90}$"
    )),
    ("FAQ_LAB_RESULT", re.compile(
        r"(?ix)^(?:(?:các\s+)?xét\s+nghiệm)\b.{0,90}$"
    )),
    ("PROGNOSIS", re.compile(r"(?ix)^tiên\s+lượng\b.{0,90}$")),
    ("COMPLICATIONS", re.compile(r"(?ix)^biến\s+chứng\b.{0,90}$")),
)


def _dynamic_header_candidate(line: str) -> tuple[int, int, str, str] | None:
    """Classify a full FAQ-style heading whose disease name is variable.

    Example: ``Triệu chứng bệnh Kawasaki ở trẻ em``. Static alias matching
    would stop after ``Triệu chứng`` and incorrectly treat the disease name as
    section content. Dynamic headings deliberately consume the full line.
    """
    if not line.strip() or len(line) > 120 or ":" in line:
        return None
    stripped = line.strip()
    if stripped.startswith(("-", "*", "•")) or stripped.endswith((".", ",", ";")):
        return None
    prefix = re.match(r"^(?:(?:\d+(?:\.\d+)*)\s*[.)/\-]\s*|[IVXLC]+\s+)?", stripped)
    heading = stripped[prefix.end():] if prefix else stripped
    for canonical, pattern in DYNAMIC_HEADER_PATTERNS:
        if pattern.fullmatch(heading):
            start = line.find(stripped)
            return start, start + len(stripped), canonical, heading
    return None


def load_section_aliases(path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(k): [str(x) for x in v] for k, v in data.items()}


def _valid_prefix(prefix: str) -> bool:
    if not prefix.strip():
        return True
    return bool(re.search(
        r"(?:^|[.!?])\s*(?:\d+(?:\.\d+)*|[IVXLC]+)\s*[.)/\-]?\s*$",
        prefix,
        flags=re.IGNORECASE,
    ))


def _valid_suffix(suffix: str) -> bool:
    if not suffix:
        return True
    first = suffix[0]
    return first.isspace() or first in ":;,-–—*" or first.isupper() or first.isdigit()


@lru_cache(maxsize=512)
def _build_flexible_pattern(alias: str) -> re.Pattern[str]:
    parts = re.split(r"(\s+|[-/,:;]+)", alias)
    regex_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        if re.match(r"^(?:\s+|[-/,:;]+)$", part):
            regex_parts.append(r"[\s\-/,:;]*")
        else:
            regex_parts.append(re.escape(part))
    return re.compile("".join(regex_parts), flags=re.IGNORECASE)


def _normalize_header(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*•]|\d+(?:\.\d+)*[.)/\-]?)\s*", "", text)
    text = text.strip(" \t:-–—*.;")
    text = re.sub(r"[\s\-/,:;]+", " ", text.casefold())
    return text.strip()


def _fuzzy_header_match(line: str, alias: str, threshold: float = 0.88) -> tuple[int, int] | None:
    """Cheap typo fallback for short header-like lines only.

    The old implementation ran SequenceMatcher over every sliding window of
    every line and alias, which became cubic on long Round-2 articles. Here we
    compare one normalized header candidate to one alias.
    """
    if len(line) > 180 or not line.strip():
        return None
    stripped = re.sub(r"^\s*(?:[-*•]|\d+(?:\.\d+)*[.)/\-]?)\s*", "", line)
    # A section header may be followed by ':' and short inline content. Compare
    # the left side first, then a bounded prefix.
    candidates = []
    colon = stripped.find(":")
    if colon >= 0:
        candidates.append(stripped[:colon])
    candidates.append(stripped[: max(len(alias) + 12, int(len(alias) * 1.25))])
    alias_norm = _normalize_header(alias)
    best: tuple[float, str] | None = None
    for candidate in candidates:
        candidate_norm = _normalize_header(candidate)
        if not candidate_norm:
            continue
        ratio = SequenceMatcher(None, alias_norm, candidate_norm).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, candidate)
    if best is None or best[0] < threshold:
        return None
    start = line.find(best[1])
    if start < 0:
        return None
    return start, start + len(best[1].rstrip())


def detect_section_markers(
    raw_text: str,
    aliases: dict[str, list[str]],
) -> list[SectionMarker]:
    alias_items = [
        (canonical, alias, _build_flexible_pattern(alias))
        for canonical, values in aliases.items()
        for alias in values
    ]
    alias_items.sort(key=lambda item: len(item[1]), reverse=True)
    markers: list[SectionMarker] = []

    for line_number, line_start, _, line in iter_lines_with_offsets(raw_text):
        # Long prose cannot be a section heading in the supported formats. We
        # still allow exact matching near the beginning, but skip fuzzy work.
        candidates: list[tuple[int, int, str, str]] = []
        fuzzy_allowed = len(line) <= 180
        dynamic = _dynamic_header_candidate(line)
        if dynamic is not None:
            candidates.append(dynamic)

        for canonical, alias, pattern in alias_items:
            match = pattern.search(line)
            match_span: tuple[int, int] | None = None
            if match is not None:
                match_span = (match.start(), match.end())
            elif fuzzy_allowed:
                match_span = _fuzzy_header_match(line, alias)

            if match_span is None:
                continue
            start, end = match_span
            if not _valid_prefix(line[:start]):
                continue
            if not _valid_suffix(line[end:]):
                continue
            candidates.append((start, end, canonical, alias))

        accepted: list[tuple[int, int, str, str]] = []
        for start, end, canonical, alias in sorted(
            candidates, key=lambda item: (item[0], -(item[1] - item[0]))
        ):
            if any(max(start, old_start) < min(end, old_end) for old_start, old_end, *_ in accepted):
                continue
            accepted.append((start, end, canonical, alias))

        for start, end, canonical, alias in accepted:
            markers.append(SectionMarker(
                canonical=canonical,
                alias=alias,
                start=line_start + start,
                end=line_start + end,
                line_number=line_number,
                raw_header=line[start:end],
            ))

    return sorted(markers, key=lambda item: (item.start, -(item.end - item.start)))


def section_at(markers: list[SectionMarker], offset: int) -> str:
    current = "UNKNOWN"
    for marker in markers:
        if marker.start > offset:
            break
        current = marker.canonical
    return current


def marker_overlaps(markers: list[SectionMarker], start: int, end: int) -> bool:
    return any(max(start, marker.start) < min(end, marker.end) for marker in markers)
