from __future__ import annotations

from collections.abc import Iterable


def build_token_windows(
    length: int,
    chunk_size: int = 220,
    overlap: int = 64,
    required_spans: Iterable[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Build half-open token windows and guarantee required inclusive spans fit.

    ``required_spans`` uses inclusive ``(start, end)`` token indices, matching
    GLiNER training data. Extra windows are added when a labelled entity would
    otherwise straddle two regular windows.
    """
    if length < 0:
        raise ValueError("length must be non-negative")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
    if length == 0:
        return []

    if length <= chunk_size:
        windows: set[tuple[int, int]] = {(0, length)}
    else:
        step = chunk_size - overlap
        starts = list(range(0, max(length - chunk_size + 1, 1), step))
        starts.append(max(0, length - chunk_size))
        windows = {
            (start, min(start + chunk_size, length))
            for start in starts
        }

    for raw_start, raw_end in required_spans:
        start, end = int(raw_start), int(raw_end)
        if not 0 <= start <= end < length:
            raise ValueError(
                f"Invalid required span {(start, end)} for token length {length}"
            )
        entity_length = end - start + 1
        if entity_length > chunk_size:
            raise ValueError(
                f"Entity span {(start, end)} has {entity_length} tokens, "
                f"larger than chunk_size={chunk_size}"
            )
        if any(start >= left and end < right for left, right in windows):
            continue

        # Add a deterministic rescue window containing the full entity.
        preferred_left = max(0, end - chunk_size + 1)
        left = min(start, preferred_left)
        left = min(left, max(0, length - chunk_size))
        right = min(left + chunk_size, length)
        if not (start >= left and end < right):
            left = max(0, min(start, length - chunk_size))
            right = min(left + chunk_size, length)
        windows.add((left, right))

    return sorted(windows)
