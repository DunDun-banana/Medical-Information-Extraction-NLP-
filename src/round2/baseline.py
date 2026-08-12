from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common.schema import Entity
from src.section_parser import SectionParser, SectionSpan


def load_baseline_entities(
    prediction_path: Path,
    raw_text: str,
    sections: list[SectionSpan],
    confidence: float = 0.97,
) -> tuple[list[Entity], list[dict[str, Any]]]:
    """Load a prior submission as a conservative anchor.

    The prior prediction is model output, not ground truth. Invalid rows are skipped and
    reported instead of being silently repaired.
    """
    if not prediction_path.exists():
        return [], [{"accepted": False, "reason": "missing_file", "path": str(prediction_path)}]

    payload = json.loads(prediction_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"Baseline prediction must be a JSON list: {prediction_path}")

    entities: list[Entity] = []
    report: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for index, row in enumerate(payload):
        try:
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            start, end = map(int, row["position"])
            text = str(row["text"])
            entity_type = str(row["type"])
            if raw_text[start:end] != text:
                raise ValueError("offset/text mismatch")
            key = (start, end, entity_type)
            if key in seen:
                report.append({"accepted": False, "reason": "duplicate", "index": index})
                continue
            seen.add(key)
            section = SectionParser.section_at(sections, start)
            entity = Entity(
                text=text,
                start=start,
                end=end,
                type=entity_type,
                assertions=[str(value) for value in row.get("assertions", [])],
                candidates=[str(value) for value in row.get("candidates", [])],
                confidence=confidence,
                source="baseline_anchor",
                section=section,
                metadata={
                    "baseline_anchor": True,
                    "baseline_index": index,
                    "baseline_candidates": [str(value) for value in row.get("candidates", [])],
                    "baseline_assertions": [str(value) for value in row.get("assertions", [])],
                },
            )
            entity.validate(raw_text)
            entities.append(entity)
            report.append({"accepted": True, "reason": "loaded", "index": index})
        except Exception as exc:  # the report is more useful than aborting a 100-file run
            report.append({
                "accepted": False,
                "reason": "invalid_row",
                "index": index,
                "error": str(exc),
            })
    return entities, report
