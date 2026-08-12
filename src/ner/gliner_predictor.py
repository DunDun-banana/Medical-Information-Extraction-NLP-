from __future__ import annotations

import re
from pathlib import Path

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.ner.gliner_chunking import build_token_windows
from src.section_parser import SectionParser, SectionSpan

TOKEN_PATTERN = re.compile(r"\S+")

LABEL_MAP = {
    "triệu chứng": "TRIỆU_CHỨNG",
    "symptom": "TRIỆU_CHỨNG",
    "chẩn đoán": "CHẨN_ĐOÁN",
    "diagnosis": "CHẨN_ĐOÁN",
    "tên xét nghiệm": "TÊN_XÉT_NGHIỆM",
    "test": "TÊN_XÉT_NGHIỆM",
    "kết quả xét nghiệm": "KẾT_QUẢ_XÉT_NGHIỆM",
    "test result": "KẾT_QUẢ_XÉT_NGHIỆM",
    "thuốc": "THUỐC",
    "drug": "THUỐC",
}

DEFAULT_LABELS = [
    "triệu chứng",
    "chẩn đoán",
    "tên xét nghiệm",
    "kết quả xét nghiệm",
    "thuốc",
]


def build_section_chunks(
    raw_text: str,
    sections: list[SectionSpan],
    max_tokens: int = 220,
    overlap_tokens: int = 64,
) -> list[dict[str, int | str]]:
    """Create section-aware chunks using the real SectionSpan field names.

    Only ``content_start``/``content_end`` are used. This prevents the previous
    ``SectionSpan.start/end`` AttributeError and avoids repeatedly treating
    headings as clinical entities.
    """
    source_sections = sections or [
        SectionSpan(
            canonical="UNKNOWN",
            header="",
            header_start=0,
            header_end=0,
            content_start=0,
            content_end=len(raw_text),
            line_number=1,
        )
    ]

    chunks: list[dict[str, int | str]] = []
    seen: set[tuple[int, int, str]] = set()

    for section in source_sections:
        section_start = max(0, int(section.content_start))
        section_end = min(len(raw_text), int(section.content_end))
        if section_end <= section_start:
            continue

        matches = list(TOKEN_PATTERN.finditer(raw_text, section_start, section_end))
        if not matches:
            continue

        windows = build_token_windows(
            len(matches),
            chunk_size=max_tokens,
            overlap=overlap_tokens,
        )
        for token_left, token_right in windows:
            char_start = matches[token_left].start()
            char_end = matches[token_right - 1].end()
            key = (char_start, char_end, section.canonical)
            if key in seen:
                continue
            seen.add(key)
            chunks.append(
                {
                    "start": char_start,
                    "end": char_end,
                    "section": section.canonical,
                    "token_count": token_right - token_left,
                }
            )

    return sorted(chunks, key=lambda row: (int(row["start"]), int(row["end"])))


class GLiNERPredictor:
    def __init__(
        self,
        model_path: Path,
        threshold: float = 0.55,
        labels: list[str] | None = None,
        device: str = "auto",
        max_tokens: int = 220,
        overlap_tokens: int = 64,
        model_max_length: int = 256,
    ):
        from gliner import GLiNER

        if not model_path.exists():
            raise FileNotFoundError(f"GLiNER model not found: {model_path}")
        if not 0 <= overlap_tokens < max_tokens:
            raise ValueError("GLiNER overlap_tokens must be smaller than max_tokens")

        self.model = GLiNER.from_pretrained(
            str(model_path),
            local_files_only=True,
            max_length=int(model_max_length),
        )
        self.threshold = float(threshold)
        self.labels = labels or DEFAULT_LABELS
        self.device = device
        self.max_tokens = int(max_tokens)
        self.overlap_tokens = int(overlap_tokens)
        self.model_max_length = int(model_max_length)

        try:
            import torch

            target = (
                "cuda"
                if device == "auto" and torch.cuda.is_available()
                else ("cpu" if device == "auto" else device)
            )
            self.model.to(target)
            self.device = target
        except Exception:
            # CPU fallback remains available even if device probing fails.
            pass

    def extract(self, raw_text: str, sections: list[SectionSpan]):
        chunks = build_section_chunks(
            raw_text,
            sections,
            max_tokens=self.max_tokens,
            overlap_tokens=self.overlap_tokens,
        )

        output: list[Entity] = []
        raw_predictions = 0

        for chunk_index, chunk in enumerate(chunks):
            chunk_start = int(chunk["start"])
            chunk_end = int(chunk["end"])
            chunk_text = raw_text[chunk_start:chunk_end]
            if not chunk_text.strip():
                continue

            rows = self.model.predict_entities(
                chunk_text,
                self.labels,
                threshold=self.threshold,
            )
            raw_predictions += len(rows)

            for row in rows:
                local_start = int(row["start"])
                local_end = int(row["end"])
                start = chunk_start + local_start
                end = chunk_start + local_end
                label = str(row.get("label", "")).casefold()
                entity_type = LABEL_MAP.get(label)

                if not entity_type or not (0 <= start < end <= len(raw_text)):
                    continue

                section = SectionParser.section_at(sections, start)
                entity = Entity(
                    text=raw_text[start:end],
                    start=start,
                    end=end,
                    type=entity_type,
                    assertions=infer_assertions(
                        raw_text,
                        start,
                        end,
                        entity_type,
                        section,
                    ),
                    confidence=float(row.get("score", 0.0)),
                    source="gliner",
                    section=section,
                    metadata={
                        "gliner_label": row.get("label"),
                        "chunk_index": chunk_index,
                        "chunk_start": chunk_start,
                        "chunk_end": chunk_end,
                        "chunk_token_count": int(chunk["token_count"]),
                    },
                )
                entity.validate(raw_text)
                output.append(entity)

        # Overlap intentionally causes duplicate predictions. Keep the most
        # confident exact span/type instance after converting offsets globally.
        best: dict[tuple[int, int, str], Entity] = {}
        for entity in output:
            key = (entity.start, entity.end, entity.type)
            if key not in best or entity.confidence > best[key].confidence:
                best[key] = entity

        result = sorted(
            best.values(),
            key=lambda entity: (entity.start, entity.end, entity.type),
        )
        return result, {
            "entities": len(result),
            "raw_predictions_with_overlap": raw_predictions,
            "deduplicated_predictions": raw_predictions - len(result),
            "device": self.device,
            "threshold": self.threshold,
            "section_aware_chunking": True,
            "chunks": len(chunks),
            "chunk_max_tokens": self.max_tokens,
            "chunk_overlap_tokens": self.overlap_tokens,
            "model_max_length": self.model_max_length,
            "max_observed_chunk_tokens": max(
                (int(chunk["token_count"]) for chunk in chunks),
                default=0,
            ),
        }
