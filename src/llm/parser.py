from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
 

ALLOWED_TYPES = {
    "TRIỆU_CHỨNG",
    "CHẨN_ĐOÁN",
    "TÊN_XÉT_NGHIỆM",
    "KẾT_QUẢ_XÉT_NGHIỆM",
    "THUỐC",
}
ALLOWED_ASSERTIONS = {"isNegated", "isHistorical", "isFamily"}


TYPE_ALIASES = {
    "TRIỆUCHỨNG": "TRIỆU_CHỨNG",
    "CHẨNĐOÁN": "CHẨN_ĐOÁN",
    "TÊNXÉTNGHIỆM": "TÊN_XÉT_NGHIỆM",
    "KẾTQUẢXÉTNGHIỆM": "KẾT_QUẢ_XÉT_NGHIỆM",
    "THUỐC": "THUỐC",
}


def normalize_entity_type(entity_type: str) -> str:
    if not entity_type:
        return ""

    # sửa một số lỗi encoding hay gặp
    entity_type = (
        entity_type
        .replace("Ü", "U")
        .replace("Ụ", "U")
    )

    entity_type = unicodedata.normalize("NFC", entity_type)
    entity_type = entity_type.upper().strip()

    entity_type = entity_type.replace(" ", "_")

    key = entity_type.replace("_", "")

    return TYPE_ALIASES.get(key, entity_type)


def strip_thinking_and_fences(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.I | re.S)
    text = re.sub(r"```(?:json)?", "", text, flags=re.I)
    return text.replace("```", "").strip()


def _balanced_json_fragment(text: str) -> str | None:
    starts = [(i, ch) for i, ch in enumerate(text) if ch in "[{"]
    for start, opener in starts:
        closer = "]" if opener == "[" else "}"
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
    return None


def _recover_complete_entity_objects(text: str) -> list[dict[str, Any]]:
    """Recover complete entity objects from a truncated JSON response.

    Qwen occasionally reaches max_tokens after writing several valid objects but
    before closing the outer array. The old parser discarded the whole chunk.
    This scanner keeps only independently valid JSON objects with text/type.
    """
    decoder = json.JSONDecoder()
    recovered: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        cursor = start + max(consumed, 1)
        if isinstance(value, dict) and "text" in value and "type" in value:
            recovered.append(value)
    return recovered


def parse_entity_payload(raw_output: str) -> list[dict[str, Any]]:
    cleaned = strip_thinking_and_fences(raw_output)
    fragment = _balanced_json_fragment(cleaned)
    payload: Any = None
    if fragment is not None:
        try:
            payload = json.loads(fragment)
        except json.JSONDecodeError:
            payload = None

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("entities"), list):
        rows = payload["entities"]
    elif isinstance(payload, dict) and "text" in payload and "type" in payload:
        # _balanced_json_fragment may find the first complete inner object of a
        # truncated outer response. Recover all complete sibling objects.
        rows = _recover_complete_entity_objects(cleaned)
    else:
        rows = _recover_complete_entity_objects(cleaned)
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text", "")).strip()
        entity_type = normalize_entity_type(str(row.get("type", "")))
        assertions = row.get("assertions", [])
        if not text or entity_type not in ALLOWED_TYPES:
            continue
        if not isinstance(assertions, list):
            assertions = []
        parsed.append({
            "text": text,
            "type": entity_type,
            "assertions": [str(x) for x in assertions if str(x) in ALLOWED_ASSERTIONS],
            "normalized": str(row.get("normalized", "")).strip()[:160],
            "context": str(row.get("context", "")).strip()[:260],
        })
    return parsed
