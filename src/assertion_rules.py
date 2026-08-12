from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import yaml

from src.section_policy import sections_with_flag


DEFAULT_CUES_PATH = Path(__file__).resolve().parents[1] / "data/mappings/assertion_cues.yaml"
HISTORICAL_SECTIONS = sections_with_flag("assertion_history")
FAMILY_SECTIONS = sections_with_flag("assertion_family")
FAMILY_TERMS = (
    "mẹ", "bố", "cha", "vợ", "chồng", "anh", "chị", "em", "con", "ông", "bà",
    "người thân", "người nhà", "gia đình",
)
REPORTER_PATTERNS = (
    r"(?:người nhà|mẹ|bố|cha|vợ|chồng|anh|chị|em|con|ông|bà)\s+"
    r"(?:cho biết|kể|nhận thấy|báo|mô tả).*?bệnh nhân",
    r"theo\s+lời\s+(?:người nhà|mẹ|bố|cha|vợ|chồng|anh|chị|em|con).*?bệnh nhân",
)
NON_NEGATION_AFTER_CUE = re.compile(
    r"(?ix)^\s*(?:"
    r"đặc\s+hiệu|xác\s+định|rõ|biến\s+chứng|tự\s+chủ|"
    r"dung\s+nạp|đáp\s+ứng|do\s+chấn\s+thương|hodgkin|"
    r"được\s+(?:phát\s+hiện|chẩn\s+đoán|điều\s+trị|theo\s+dõi)|"
    r"phải\s+(?:mọi|tất\s+cả|ai)"
    r")\b"
)


def _compile_cues(values: list[str]) -> re.Pattern[str]:
    cleaned = sorted(
        {str(value).strip() for value in values if str(value).strip()},
        key=len,
        reverse=True,
    )
    if not cleaned:
        return re.compile(r"(?!x)x")
    # Phrase boundaries are applied only at the outer edges. This still permits
    # punctuation and spaces inside multi-word Vietnamese cues.
    pattern = "|".join(re.escape(value) for value in cleaned)
    return re.compile(rf"(?ix)(?<![\wÀ-ỹ])(?:{pattern})(?![\wÀ-ỹ])")


@lru_cache(maxsize=4)
def load_assertion_cues(path: str | None = None) -> dict[str, list[str]]:
    cue_path = Path(path) if path else DEFAULT_CUES_PATH
    payload = yaml.safe_load(cue_path.read_text(encoding="utf-8")) or {}
    return {
        str(name): [str(value) for value in values or []]
        for name, values in payload.items()
    }


_CUES = load_assertion_cues()
NEGATION_CUES = _compile_cues(_CUES.get("isNegated", []))
HISTORICAL_CUES = _compile_cues(_CUES.get("isHistorical", []))
FAMILY_CUES = _compile_cues(_CUES.get("isFamily", []))


def _clause_start(raw_text: str, start: int, max_chars: int = 120) -> int:
    left = max(0, start - max_chars)
    segment = raw_text[left:start]
    positions = [segment.rfind(token) for token in ("\n", ".", ";", ":")]
    boundary = max(positions)
    return left + boundary + 1 if boundary >= 0 else left


def _family_mentions(prefix: str) -> list[tuple[int, int, str]]:
    mentions: list[tuple[int, int, str]] = []
    for term in FAMILY_TERMS:
        pattern = re.compile(rf"(?<![\wÀ-ỹ]){re.escape(term)}(?![\wÀ-ỹ])", re.IGNORECASE)
        mentions.extend((m.start(), m.end(), term) for m in pattern.finditer(prefix))
    return sorted(mentions)


def _is_family(raw_text: str, start: int, section: str) -> bool:
    if section in FAMILY_SECTIONS:
        return True
    left = _clause_start(raw_text, start, 180)
    prefix = raw_text[left:start].casefold()
    if FAMILY_CUES.search(prefix):
        return True
    mentions = _family_mentions(prefix)
    if not mentions:
        return False
    if any(re.search(pattern, prefix, flags=re.IGNORECASE | re.DOTALL) for pattern in REPORTER_PATTERNS):
        return False

    last_family = mentions[-1][0]
    patient_mentions = [
        m.start() for m in re.finditer(
            r"(?<![\wÀ-ỹ])(?:bệnh\s+nhân|người\s+bệnh)(?![\wÀ-ỹ])", prefix
        )
    ]
    last_patient = patient_mentions[-1] if patient_mentions else -1
    return last_family > last_patient


def _is_historical(raw_text: str, start: int, section: str) -> bool:
    if section in HISTORICAL_SECTIONS:
        return True
    left = _clause_start(raw_text, start, 140)
    prefix = raw_text[left:start]
    matches = list(HISTORICAL_CUES.finditer(prefix))
    if not matches:
        return False
    cue = matches[-1]
    between = prefix[cue.end():]
    # A contrast/new-time boundary prevents a remote historical cue from
    # leaking into a current finding later in the clause.
    if re.search(r"(?ix)\b(?:nhưng|tuy\s+nhiên|hiện\s+tại|lần\s+này|nay)\b", between):
        return False
    return len(between) <= 90


def _is_negated(raw_text: str, start: int, end: int) -> bool:
    left = _clause_start(raw_text, start, 120)
    prefix = raw_text[left:start]
    matches = list(NEGATION_CUES.finditer(prefix))
    if not matches:
        return False

    cue = matches[-1]
    between = prefix[cue.end():]
    if re.search(r"(?ix)\b(?:nhưng|tuy\s+nhiên|song)\b", between):
        return False
    if NON_NEGATION_AFTER_CUE.match(between):
        return False

    entity_text = raw_text[start:end].casefold()
    if re.search(r"\bkhông\s+(?:tự\s+chủ|đặc\s+hiệu|xác\s+định|biến\s+chứng)\b", entity_text):
        return False
    return True


def infer_assertions(
    raw_text: str,
    start: int,
    end: int,
    entity_type: str,
    section: str,
) -> list[str]:
    if entity_type not in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN", "THUỐC"}:
        return []

    assertions: list[str] = []
    if _is_historical(raw_text, start, section):
        assertions.append("isHistorical")
    if entity_type != "THUỐC" and _is_family(raw_text, start, section):
        assertions.append("isFamily")
    if entity_type != "THUỐC" and _is_negated(raw_text, start, end):
        assertions.append("isNegated")
    return assertions
