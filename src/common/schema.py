from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_TYPES = {
    "TRIỆU_CHỨNG",
    "TÊN_XÉT_NGHIỆM",
    "KẾT_QUẢ_XÉT_NGHIỆM",
    "CHẨN_ĐOÁN",
    "THUỐC",
}


@dataclass
class Entity:
    text: str
    start: int
    end: int
    type: str
    assertions: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "rule"
    section: str = "UNKNOWN"
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, raw_text: str) -> None:
        if self.type not in ALLOWED_TYPES:
            raise ValueError(f"Unsupported entity type: {self.type}")
        if not (0 <= self.start < self.end <= len(raw_text)):
            raise ValueError(
                f"Invalid offset [{self.start}, {self.end}) for length {len(raw_text)}"
            )
        actual = raw_text[self.start:self.end]
        if actual != self.text:
            raise ValueError(
                f"Offset mismatch [{self.start}, {self.end}): "
                f"expected {self.text!r}, got {actual!r}"
            )

    def to_submission_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "text": self.text,
            "position": [self.start, self.end],
            "type": self.type,
            "assertions": list(dict.fromkeys(self.assertions)),
        }
        if self.type in {"CHẨN_ĐOÁN", "THUỐC"}:
            row["candidates"] = list(dict.fromkeys(self.candidates))
        return row

    def to_debug_dict(self) -> dict[str, Any]:
        row = self.to_submission_dict()
        row.update({
            "confidence": self.confidence,
            "source": self.source,
            "section": self.section,
            "metadata": self.metadata,
        })
        return row
