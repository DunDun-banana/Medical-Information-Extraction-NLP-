from __future__ import annotations

from dataclasses import dataclass
import re


HEADING_RE = re.compile(
    r"(?im)^\s*(?:(?:\d+(?:\.\d+)*)[\.)]?|(?:phần|mục)\s+[ivx\d]+)\s*[:.\-]?\s*\S.*$"
)
BULLET_RE = re.compile(r"(?m)^\s*(?:[•\-*]|\d+[\.)])\s+")
MEDICAL_CUE_RE = re.compile(
    r"(?ix)\b(?:bệnh|triệu\s+chứng|dấu\s+hiệu|chẩn\s+đoán|xét\s+nghiệm|"
    r"thuốc|điều\s+trị|đau|sốt|ho|khó\s+thở|vàng\s+da|thiếu\s+máu|"
    r"viêm|suy|ung\s+thư|hội\s+chứng|máu|nước\s+tiểu|x-?quang|ct\b|"
    r"mri\b|siêu\s+âm|nội\s+soi|sinh\s+thiết|mg\b|ml\b|âm\s+tính|"
    r"dương\s+tính|bình\s+thường|bất\s+thường)\b"
)
ADMIN_RE = re.compile(
    r"(?ix)^(?:chữ\s+ký|người\s+bàn\s+giao|người\s+nhận|ca\s+trực|"
    r"xác\s+nhận\s+số\s+lượng|nguồn\s+tham\s+khảo)"
)


@dataclass(frozen=True)
class SemanticChunk:
    start: int
    end: int
    text: str
    heading: str
    signal: int


def _signal(text: str) -> int:
    return len(MEDICAL_CUE_RE.findall(text))


def _line_spans(raw_text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in raw_text.splitlines(keepends=True):
        end = cursor + len(line)
        spans.append((cursor, end, line))
        cursor = end
    if cursor < len(raw_text):
        spans.append((cursor, len(raw_text), raw_text[cursor:]))
    return spans


def semantic_chunks(
    raw_text: str,
    max_chars: int = 1800,
    overlap_chars: int = 120,
    min_medical_signal: int = 1,
    sections: list | None = None,
) -> list[SemanticChunk]:
    """Split articles/FAQ/notes without changing character offsets.

    Headings start a new logical block. Oversized blocks are cut at line or
    sentence boundaries with a small overlap. Pure administrative blocks are
    skipped unless they contain a medical cue.
    """
    lines = _line_spans(raw_text)
    blocks: list[tuple[int, int, str]] = []

    # Prefer the canonical section parser when available. In V2/V3 the YAML
    # aliases were used only as entity metadata while LLM/NER chunking relied on
    # a separate numbered-heading regex. This made headings such as "Bệnh sử",
    # "Câu trả lời của bác sĩ" and "XN máu" invisible to both models.
    if sections:
        for section in sections:
            start = int(section.header_start if section.header else section.content_start)
            end = int(section.content_end)
            if end <= start:
                continue
            raw_header = str(section.header).strip()
            heading = str(section.canonical)
            if raw_header:
                heading = f"{heading}: {raw_header}"
            blocks.append((start, end, heading[:180]))
    else:
        block_start = 0
        current_heading = "DOCUMENT"
        seen_content = False
        for start, _end, line in lines:
            stripped = line.strip()
            is_heading = bool(stripped and HEADING_RE.match(line))
            if is_heading and seen_content and start > block_start:
                blocks.append((block_start, start, current_heading))
                block_start = start
                current_heading = stripped[:180]
            elif is_heading:
                current_heading = stripped[:180]
            if stripped:
                seen_content = True
        if block_start < len(raw_text):
            blocks.append((block_start, len(raw_text), current_heading))

    # Consecutive short headings are common in health articles. Sending each
    # bullet subsection as a separate request wastes context and GPU time, so
    # coalesce adjacent blocks up to max_chars while preserving the raw text.
    filtered_blocks: list[tuple[int, int, str]] = []
    for start, end, heading in blocks:
        block = raw_text[start:end]
        if _signal(block) < min_medical_signal and ADMIN_RE.match(block.strip()):
            continue
        if (
            filtered_blocks
            and end - filtered_blocks[-1][0] <= max_chars
            and start == filtered_blocks[-1][1]
            and (not sections or heading == filtered_blocks[-1][2])
        ):
            old_start, _old_end, old_heading = filtered_blocks[-1]
            filtered_blocks[-1] = (old_start, end, old_heading)
        else:
            filtered_blocks.append((start, end, heading))

    output: list[SemanticChunk] = []
    for start, end, heading in filtered_blocks:
        cursor = start
        while cursor < end:
            tentative = min(cursor + max_chars, end)
            if tentative < end:
                # Prefer a paragraph, line, or sentence boundary.
                candidates = [
                    raw_text.rfind("\n\n", cursor + max_chars // 2, tentative),
                    raw_text.rfind("\n", cursor + max_chars // 2, tentative),
                    raw_text.rfind(". ", cursor + max_chars // 2, tentative),
                ]
                cut = max(candidates)
                if cut > cursor:
                    tentative = cut + (2 if raw_text[cut:cut + 2] == ". " else 1)
            if tentative <= cursor:
                tentative = min(cursor + max_chars, end)
            text = raw_text[cursor:tentative]
            signal = _signal(text)
            if signal >= min_medical_signal:
                output.append(SemanticChunk(cursor, tentative, text, heading, signal))
            if tentative >= end:
                break
            cursor = max(cursor + 1, tentative - max(0, overlap_chars))

    if output:
        return output
    return [SemanticChunk(0, len(raw_text), raw_text, "DOCUMENT", _signal(raw_text))]
