from __future__ import annotations

from pathlib import Path
from statistics import mean

from src.assertion_rules import infer_assertions
from src.common.schema import Entity
from src.ner.config import load_ner_config, resolve_model_path
from src.ner.phobert_offset_adapter import PhoBertOffsetAdapter
from src.ner.training_safety import resolve_safe_window
from src.round2.semantic_chunks import semantic_chunks
from src.section_parser import SectionParser, SectionSpan


class ViHealthBertPredictor:
    def __init__(self, root: Path, config_path: Path | None = None, source_name: str = "vihealthbert"):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self.root = root
        self.source_name = source_name
        self.cfg = load_ner_config(config_path or root / "configs/ner_round2.yaml")
        fine = resolve_model_path(root, self.cfg.model.fine_tuned_path)
        if not fine.exists():
            raise FileNotFoundError(
                f"Fine-tuned ViHealthBERT not found: {fine}. "
                "Run scripts/63_train_vihealthbert_hybrid.py first."
            )
        raw_tokenizer = AutoTokenizer.from_pretrained(
            fine, use_fast=False, local_files_only=True
        )
        self.tokenizer = PhoBertOffsetAdapter(raw_tokenizer)
        self.model = AutoModelForTokenClassification.from_pretrained(
            fine, local_files_only=True
        )
        self.max_length, self.stride, self.window_preflight = resolve_safe_window(
            self.cfg.inference.max_length,
            self.cfg.inference.stride,
            self.model.config,
            raw_tokenizer,
        )
        wanted = self.cfg.inference.device
        self.device = torch.device(
            "cuda" if wanted == "auto" and torch.cuda.is_available()
            else ("cpu" if wanted == "auto" else wanted)
        )
        self.model.to(self.device).eval()
        self.torch = torch

    def _extract_segment(
        self,
        raw_text: str,
        segment_start: int,
        segment_end: int,
        sections: list[SectionSpan],
    ) -> tuple[list[Entity], dict]:
        torch = self.torch
        text = raw_text[segment_start:segment_end]
        enc = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            stride=self.stride,
            return_overflowing_tokens=True,
            return_offsets_mapping=True,
            padding=True,
            return_tensors="pt",
        )
        offsets = enc.pop("offset_mapping").tolist()
        enc.pop("overflow_to_sample_mapping", None)
        model_inputs = {k: v.to(self.device) for k, v in enc.items()}
        token_votes: dict[tuple[int, int], tuple[str, float]] = {}
        with torch.inference_mode():
            for left in range(0, model_inputs["input_ids"].shape[0], self.cfg.inference.batch_size):
                right = left + self.cfg.inference.batch_size
                batch = {k: v[left:right] for k, v in model_inputs.items()}
                probs = self.model(**batch).logits.softmax(-1).cpu()
                scores, ids = probs.max(-1)
                for local, window in enumerate(range(left, min(right, len(offsets)))):
                    for (rel_start, rel_end), label_id, score in zip(
                        offsets[window], ids[local].tolist(), scores[local].tolist()
                    ):
                        if rel_start == rel_end:
                            continue
                        start = segment_start + rel_start
                        end = segment_start + rel_end
                        label = self.model.config.id2label.get(int(label_id), "O")
                        old = token_votes.get((start, end))
                        if old is None or score > old[1]:
                            token_votes[(start, end)] = (label, float(score))

        tokens = sorted(
            (start, end, label, confidence)
            for (start, end), (label, confidence) in token_votes.items()
        )
        entities: list[Entity] = []
        current: tuple[int, int, str, list[float]] | None = None
        threshold = self.cfg.inference.confidence_threshold

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            start, end, typ, confidences = current
            section = SectionParser.section_at(sections, start)
            entity = Entity(
                text=raw_text[start:end],
                start=start,
                end=end,
                type=typ,
                assertions=infer_assertions(raw_text, start, end, typ, section),
                confidence=mean(confidences),
                source=self.source_name,
                section=section,
                metadata={"token_confidences": confidences},
            )
            entity.validate(raw_text)
            entities.append(entity)
            current = None

        for start, end, label, confidence in tokens:
            if label == "O" or confidence < threshold or "-" not in label:
                flush()
                continue
            prefix, typ = label.split("-", 1)
            can_join = (
                current is not None
                and typ == current[2]
                and prefix == "I"
                and not raw_text[current[1]:start].strip()
            )
            if can_join:
                current = (current[0], end, typ, current[3] + [confidence])
            else:
                flush()
                current = (start, end, typ, [confidence])
        flush()
        return entities, {"windows": len(offsets), "tokens": len(tokens)}

    def extract(
        self, raw_text: str, sections: list[SectionSpan]
    ) -> tuple[list[Entity], dict]:
        chunks = semantic_chunks(
            raw_text,
            max_chars=self.cfg.inference.segment_max_chars,
            overlap_chars=80,
            min_medical_signal=1,
            sections=sections,
        )
        entities: list[Entity] = []
        debug_segments: list[dict] = []
        for chunk in chunks:
            rows, debug = self._extract_segment(
                raw_text, chunk.start, chunk.end, sections
            )
            entities.extend(rows)
            debug_segments.append({
                "start": chunk.start,
                "end": chunk.end,
                "heading": chunk.heading,
                "signal": chunk.signal,
                **debug,
                "entities": len(rows),
            })
        # Deduplicate overlapping windows without resolving types here.
        best: dict[tuple[int, int, str], Entity] = {}
        for entity in entities:
            key = (entity.start, entity.end, entity.type)
            if key not in best or entity.confidence > best[key].confidence:
                best[key] = entity
        output = sorted(best.values(), key=lambda e: (e.start, e.end, e.type))
        return output, {
            "segments": debug_segments,
            "entities_before_dedup": len(entities),
            "entities": len(output),
            "device": str(self.device),
            "window_preflight": self.window_preflight,
        }


class PhoBertPredictor(ViHealthBertPredictor):
    def __init__(self, root: Path, config_path: Path | None = None):
        super().__init__(root, config_path, source_name="phobert")
