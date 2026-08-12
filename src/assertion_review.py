from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Clause:
    start: int
    end: int
    text: str


BOUNDARY = re.compile(r"\n+|(?<=[.!?;])\s+|(?<=[.!?;])(?=[A-ZÀ-Ỹ])")
CONTRAST = re.compile(r"(?i)\b(?:nhưng|tuy nhiên|song)\b")

REPORTER_PATTERNS = [
    re.compile(r"(?i)\btheo lời(?: kể)? (?:của )?(?:người nhà|gia đình|mẹ|bố|cha|vợ|chồng|con)\b"),
    re.compile(r"(?i)\b(?:người nhà|gia đình|mẹ|bố|cha|vợ|chồng|con(?: trai| gái)?)(?:\s+bệnh nhân)?\s+(?:nhận thấy|cho biết|kể lại|báo cáo|thấy|phát hiện)\b"),
    re.compile(r"(?i)\b(?:được|do)\s+(?:người nhà|gia đình|mẹ|bố|cha|vợ|chồng|con(?: trai| gái)?)\s+(?:nhận thấy|phát hiện|đưa đến)\b"),
    re.compile(r"(?i)\b(?:lời kể|câu hỏi|theo lời kể)\s+(?:của\s+)?(?:người nhà|gia đình|mẹ|bố|cha|vợ|chồng|con)\b"),
    re.compile(r"(?i)\b(?:người nhà|gia đình|vợ|chồng|con(?: trai| gái)?)\s+(?:lo ngại|yêu cầu)\b"),
]

TARGET_PATTERNS = [
    re.compile(
        r"(?i)\b(?:mẹ|bố|cha|vợ|chồng|con(?: trai| gái)?|anh|chị|em|"
        r"nhiều thành viên trong gia đình|thành viên trong gia đình|người nhà)"
        r"(?:\s+bệnh nhân)?\s+(?:có|bị|mắc|xuất hiện|được chẩn đoán|"
        r"đang điều trị|đã từng mắc|có các triệu chứng)\b"
    ),
]

UNCERTAINTY = re.compile(r"(?i)\bkhông\s+(?:thể\s+)?loại trừ\b")
NEGATION_CUES = [
    re.compile(r"(?i)\bkhông ghi nhận\b"),
    re.compile(r"(?i)\bkhông xác nhận\b"),
    re.compile(r"(?i)\bchưa phát hiện\b"),
    re.compile(r"(?i)\bkhông phát hiện\b"),
    re.compile(r"(?i)\bkhông cho thấy\b"),
    re.compile(r"(?i)\bkhông còn\b"),
    re.compile(r"(?i)\bkhông có\b"),
    re.compile(r"(?i)\bkhông thấy\b"),
    re.compile(r"(?i)\bchưa thấy\b"),
    re.compile(r"(?i)\bphủ nhận\b"),
    re.compile(r"(?i)\bloại trừ\b"),
    re.compile(r"(?i)\bâm tính\b"),
    re.compile(r"(?i)\bkhông\b"),
]

NON_NEGATING_KHONG = re.compile(
    r"(?i)^(?:đặc hiệu|xác định|rõ(?:\s|$)|thể(?:\s|$)|tuân thủ|"
    r"dung nạp|kiểm soát(?: được)?|tự chủ|ổn định|do(?:\s|$)|"
    r"tế bào nhỏ|biệt định|xâm nhập|hodgkin|thành(?:\s|$)|"
    r"giảm(?: đau)?|đỡ(?:\s|$)|cải thiện|đáp ứng|liên quan|"
    r"nặng hơn|thay đổi|thuốc cản quang|được chỉ định|muốn|"
    r"sử dụng thuốc|ngon miệng|biến chứng(?:\s|$)|vững(?:\s|$)|"
    r"hồi phục|kiểm tra|nhớ(?:\s|$)|kịp(?:\s|$)|ghi rõ|dùng đều|"
    r"làm giảm|thực hiện|chênh(?:\s|$)|thủng(?:\s|$)|hiểu(?:\s|$)|"
    r"mang tính chất|tan máu|tác dụng(?:\s|$)|tái khám|"
    r"được chăm sóc|sử dụng băng vệ sinh|sử dụng băng vệ tinh)"
)
GENERIC_SCOPE = re.compile(
    r"(?i)^(?:lý do khám bệnh|thay đổi(?: đáng kể)? (?:các )?triệu chứng|"
    r"bệnh lý khác|bệnh lý bất thường|biểu hiện bất thường khác|"
    r"(?:các )?triệu chứng(?: trước đó)?(?:\.|$)|triệu chứng nào khác|"
    r"bất thường(?: trên phim)?|diễn tiến xấu|caffeine(?:\.|$)|"
    r"ngực(?:\.|$)|các bệnh nền lớn)"
 )
NON_NEGATING_SCOPE = re.compile(
    r"(?i)^(?:giảm đau|cải thiện|đỡ(?:\s|$)|hồi phục|làm giảm|"
    r"thay đổi(?: đáng kể)?|ghi rõ|vững(?:\s|$)|kiểm tra|nhớ(?:\s|$)|"
    r"kịp(?:\s|$)|dùng đều|thực hiện|chênh(?:\s|$)|hiểu(?:\s|$)|"
    r"mang tính chất|tan máu|tác dụng(?:\s|$)|tái khám|"
    r"được chăm sóc|sử dụng băng vệ sinh|sử dụng băng vệ tinh)"
)
TEST_CONTEXT = re.compile(
    r"(?i)\b(?:xét nghiệm|thử nghiệm|ct|mri|chụp|cấy|pcr|"
    r"siêu âm|nitrite|huyết thanh|điện tâm đồ)\b"
)

HISTORICAL_CUES = [
    re.compile(r"(?i)\bcó tiền sử\b"),
    re.compile(r"(?i)\btiền sử\b"),
    re.compile(r"(?i)\bđã từng\b"),
    re.compile(r"(?i)\btrước đây\b"),
    re.compile(r"(?i)\btrong quá khứ\b"),
]


def iter_clauses(raw_text: str):
    cursor = 0
    for boundary in BOUNDARY.finditer(raw_text):
        end = boundary.start()
        text = raw_text[cursor:end].strip()
        if text:
            left = cursor + len(raw_text[cursor:end]) - len(raw_text[cursor:end].lstrip())
            yield Clause(left, left + len(text), text)
        cursor = boundary.end()
    tail = raw_text[cursor:]
    text = tail.strip()
    if text:
        left = cursor + len(tail) - len(tail.lstrip())
        yield Clause(left, left + len(text), text)


def classify_family_clause(text: str) -> tuple[str, str] | None:
    for pattern in REPORTER_PATTERNS:
        match = pattern.search(text)
        if match:
            return "family_reporter", match.group(0)
    for pattern in TARGET_PATTERNS:
        match = pattern.search(text)
        if match:
            tail = text[match.end():].casefold()
            if "các triệu chứng tương tự" in text.casefold() and not re.search(
                r"(?i)chẩn đoán(?: là)?\s+[^,.;]+", text
            ):
                return "family_no_explicit_entity", match.group(0)
            return "family_target", match.group(0)
    return None


def negation_candidates(clause: Clause):
    if UNCERTAINTY.search(clause.text):
        match = UNCERTAINTY.search(clause.text)
        yield {
            "kind": "uncertainty",
            "cue_text": match.group(0),
            "cue_start": clause.start + match.start(),
            "cue_end": clause.start + match.end(),
            "scope_start": clause.start,
            "scope_end": clause.end,
            "scope_text": clause.text,
            "direction": "whole_clause",
        }
        return

    occupied = []
    for pattern in NEGATION_CUES:
        for match in pattern.finditer(clause.text):
            if any(max(match.start(), a) < min(match.end(), b) for a, b in occupied):
                continue
            occupied.append((match.start(), match.end()))
            cue = match.group(0)

            # `không` is often part of a positive clinical phrase rather than
            # an assertion: không đặc hiệu, không tự chủ, không dung nạp...
            if cue.casefold() == "không":
                after = clause.text[match.end():].lstrip(" ,:-")
                if NON_NEGATING_KHONG.search(after):
                    yield {
                        "kind": "non_negation",
                        "cue_text": cue,
                        "cue_start": clause.start + match.start(),
                        "cue_end": clause.start + match.end(),
                        "scope_start": clause.start,
                        "scope_end": clause.end,
                        "scope_text": clause.text,
                        "direction": "whole_clause",
                    }
                    continue

            if cue.casefold() == "âm tính":
                left = clause.text[:match.start()]
                if TEST_CONTEXT.search(left):
                    scope_local_start, scope_local_end = 0, match.start()
                    direction = "left"
                else:
                    scope_local_start, scope_local_end = match.end(), len(clause.text)
                    direction = "right"
            else:
                scope_local_start, scope_local_end = match.end(), len(clause.text)
                contrast = CONTRAST.search(clause.text, scope_local_start)
                if contrast:
                    scope_local_end = contrast.start()
                next_cue_positions = []
                for later_pattern in NEGATION_CUES:
                    later = later_pattern.search(clause.text, scope_local_start)
                    if later and later.start() > match.start():
                        next_cue_positions.append(later.start())
                if next_cue_positions:
                    scope_local_end = min(scope_local_end, min(next_cue_positions))
                direction = "right"

            raw_scope = clause.text[scope_local_start:scope_local_end]
            scope = raw_scope.strip(" ,:-")
            if not scope:
                continue
            stripped_prefix = len(raw_scope) - len(raw_scope.lstrip(" ,:-"))
            global_scope_start = clause.start + scope_local_start + stripped_prefix
            if cue.casefold() == "âm tính" and TEST_CONTEXT.search(clause.text):
                kind = "test_result"
            elif NON_NEGATING_SCOPE.search(scope):
                kind = "non_negation"
            else:
                kind = "no_explicit_entity" if GENERIC_SCOPE.search(scope) else "negation"
            yield {
                "kind": kind,
                "cue_text": cue,
                "cue_start": clause.start + match.start(),
                "cue_end": clause.start + match.end(),
                "scope_start": global_scope_start,
                "scope_end": global_scope_start + len(scope),
                "scope_text": scope,
                "direction": direction,
            }

