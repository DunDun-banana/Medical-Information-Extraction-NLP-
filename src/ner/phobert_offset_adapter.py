from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


class OffsetAlignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenWindow:
    model_inputs: dict[str, list[int]]
    offsets: list[tuple[int, int]]


def _piece_surface(piece: str) -> str:
    if piece.endswith("@@"):
        piece = piece[:-2]
    return piece.lstrip("▁")


def tokenize_with_offsets(tokenizer, raw_text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Map the original slow PhoBERT/fastBPE pieces to exact raw offsets.

    The previous notebook adapter searched character-by-character on a miss,
    which made long Round-2 articles extremely slow. Vietnamese case folding
    is length-preserving for normal clinical text, so a precomputed case-folded
    string gives a much faster linear scan while retaining exact offsets.
    """
    pieces = list(tokenizer.tokenize(raw_text))
    folded_text = raw_text.casefold()
    offsets: list[tuple[int, int]] = []
    cursor = 0
    special_tokens = set(getattr(tokenizer, "all_special_tokens", []))
    unknown_token = getattr(tokenizer, "unk_token", "<unk>")

    for index, piece in enumerate(pieces):
        if piece in special_tokens and piece != unknown_token:
            offsets.append((0, 0))
            continue
        if piece == unknown_token:
            match = re.search(r"\S+", raw_text[cursor:])
            if match is None:
                raise OffsetAlignmentError(
                    f"Cannot align <unk> at piece={index}, cursor={cursor}"
                )
            start = cursor + match.start()
            end = cursor + match.end()
            offsets.append((start, end))
            cursor = end
            continue

        surface = _piece_surface(piece)
        if not surface:
            offsets.append((0, 0))
            continue
        start = raw_text.find(surface, cursor)
        if start < 0:
            start = folded_text.find(surface.casefold(), cursor)
        if start < 0:
            # Punctuation/whitespace normalization fallback in a small window.
            window_end = min(len(raw_text), cursor + 256)
            compact_surface = re.sub(r"\s+", r"\\s+", re.escape(surface))
            match = re.search(compact_surface, raw_text[cursor:window_end], re.I)
            start = cursor + match.start() if match else -1
        if start < 0:
            left = max(0, cursor - 40)
            right = min(len(raw_text), cursor + 160)
            raise OffsetAlignmentError(
                f"Cannot align piece={piece!r}, surface={surface!r}, "
                f"cursor={cursor}, context={raw_text[left:right]!r}"
            )
        end = start + len(surface)
        offsets.append((start, end))
        cursor = end
    return pieces, offsets


def build_windows(tokenizer, raw_text: str, max_length: int, stride: int) -> list[TokenWindow]:
    pieces, piece_offsets = tokenize_with_offsets(tokenizer, raw_text)
    piece_ids = tokenizer.convert_tokens_to_ids(pieces)
    special_count = tokenizer.num_special_tokens_to_add(pair=False)
    capacity = max_length - special_count
    if capacity <= 0 or not 0 <= stride < capacity:
        raise ValueError(f"Invalid max_length={max_length}, stride={stride}")

    step = capacity - stride
    windows: list[TokenWindow] = []
    starts = range(0, len(piece_ids), step) if piece_ids else [0]
    for left in starts:
        ids = piece_ids[left:left + capacity]
        offsets = piece_offsets[left:left + capacity]
        input_ids = tokenizer.build_inputs_with_special_tokens(ids)
        special_mask = tokenizer.get_special_tokens_mask(ids, already_has_special_tokens=False)
        aligned_offsets: list[tuple[int, int]] = []
        source_index = 0
        for is_special in special_mask:
            if is_special:
                aligned_offsets.append((0, 0))
            else:
                aligned_offsets.append(offsets[source_index])
                source_index += 1
        model_inputs = {
            "input_ids": list(input_ids),
            "attention_mask": [1] * len(input_ids),
        }
        if "token_type_ids" in getattr(tokenizer, "model_input_names", []):
            model_inputs["token_type_ids"] = [0] * len(input_ids)
        windows.append(TokenWindow(model_inputs=model_inputs, offsets=aligned_offsets))
        if left + capacity >= len(piece_ids):
            break
    return windows


class PhoBertOffsetAdapter:
    supports_offsets = True

    def __init__(self, tokenizer):
        self.base_tokenizer = tokenizer

    @property
    def is_fast(self) -> bool:
        return True

    @property
    def underlying_is_fast(self) -> bool:
        return bool(getattr(self.base_tokenizer, "is_fast", False))

    def __getattr__(self, name: str):
        return getattr(self.base_tokenizer, name)

    def __call__(
        self,
        text: str,
        *,
        truncation: bool = True,
        max_length: int = 256,
        stride: int = 64,
        return_overflowing_tokens: bool = False,
        return_offsets_mapping: bool = False,
        padding: bool | str = False,
        return_tensors: str | None = None,
        **kwargs: Any,
    ):
        if not return_offsets_mapping:
            return self.base_tokenizer(
                text,
                truncation=truncation,
                max_length=max_length,
                stride=stride,
                return_overflowing_tokens=return_overflowing_tokens,
                padding=padding,
                return_tensors=return_tensors,
                **kwargs,
            )
        windows = build_windows(self.base_tokenizer, text, max_length, stride)
        rows = [window.model_inputs for window in windows]
        if padding or return_tensors:
            encoded = self.base_tokenizer.pad(
                rows,
                padding=padding if padding else True,
                return_tensors=return_tensors,
            )
            width = max(len(window.offsets) for window in windows)
            padded_offsets = [
                window.offsets + [(0, 0)] * (width - len(window.offsets))
                for window in windows
            ]
            if return_tensors == "pt":
                import torch
                encoded["offset_mapping"] = torch.tensor(padded_offsets, dtype=torch.long)
                encoded["overflow_to_sample_mapping"] = torch.zeros(len(windows), dtype=torch.long)
            else:
                encoded["offset_mapping"] = padded_offsets
                encoded["overflow_to_sample_mapping"] = [0] * len(windows)
            return encoded
        result: dict[str, list] = {}
        keys = set().union(*(row.keys() for row in rows))
        for key in keys:
            result[key] = [row[key] for row in rows]
        result["offset_mapping"] = [window.offsets for window in windows]
        result["overflow_to_sample_mapping"] = [0] * len(windows)
        return result
