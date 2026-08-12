from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.ner.labels import LABEL2ID


def load_manifest(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_example(root: Path, row: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    text = (root / row["text_path"]).read_text(encoding="utf-8-sig")
    gold = json.loads((root / row["gold_path"]).read_text(encoding="utf-8"))
    valid = []
    for entity in gold:
        start, end = map(int, entity["position"])
        if 0 <= start < end <= len(text) and text[start:end] == entity["text"]:
            valid.append(entity)
    return text, valid


def build_features(root: Path, manifest: Path, tokenizer, max_length: int, stride: int) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for row in load_manifest(manifest):
        text, gold = read_example(root, row)
        encoded = tokenizer(
            text,
            truncation=True,
            max_length=max_length,
            stride=stride,
            return_overflowing_tokens=True,
            return_offsets_mapping=True,
        )
        for window in range(len(encoded["input_ids"])):
            offsets = encoded["offset_mapping"][window]
            labels: list[int] = []
            for start, end in offsets:
                if start == end:
                    labels.append(-100)
                    continue
                matches = [
                    e for e in gold
                    if max(start, int(e["position"][0])) < min(end, int(e["position"][1]))
                ]
                if not matches:
                    labels.append(LABEL2ID["O"])
                    continue
                # BIO cannot represent nested entities. Prefer the shortest exact
                # clinical phrase, which is also closer to the competition's WER.
                entity = min(matches, key=lambda e: int(e["position"][1]) - int(e["position"][0]))
                prefix = "B" if start <= int(entity["position"][0]) < end else "I"
                labels.append(LABEL2ID[f"{prefix}-{entity['type']}"])
            feature = {
                key: encoded[key][window]
                for key in tokenizer.model_input_names
                if key in encoded
            }
            feature["labels"] = labels
            features.append(feature)
    return features
