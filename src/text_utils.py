import re
import unicodedata
from dataclasses import dataclass

@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    text: str

    def validate(self, raw_text):
        if not (0 <= self.start < self.end <= len(raw_text)):
            raise ValueError(f"Invalid span [{self.start}, {self.end})")
        actual = raw_text[self.start:self.end]
        if actual != self.text:
            raise ValueError(f"Offset mismatch: {self.text!r} != {actual!r}")

def normalize_for_matching(text):
    value = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"\s+", " ", value).strip()

def iter_lines_with_offsets(raw_text):
    cursor = 0
    for number, line_break in enumerate(raw_text.splitlines(keepends=True), 1):
        line = line_break.rstrip("\r\n")
        yield number, cursor, cursor + len(line), line
        cursor += len(line_break)

def context_window(raw_text, start, end, window=120):
    return raw_text[max(0,start-window):min(len(raw_text),end+window)].replace("\n"," ")
