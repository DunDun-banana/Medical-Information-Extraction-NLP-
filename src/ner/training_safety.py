from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class FeatureIdStats:
    feature_count: int
    token_count: int
    min_input_id: int
    max_input_id: int
    min_label_id: int | None
    max_label_id: int | None


def tokenizer_required_vocab_size(tokenizer: Any) -> int:
    """Return the smallest embedding table that can index every tokenizer ID.

    ``len(tokenizer)`` is usually sufficient, but some restored/converted slow
    tokenizers can contain sparse IDs or special-token IDs at/above that value.
    Computing the maximum actual ID makes the training preflight robust to those
    checkpoints.
    """
    ids: list[int] = []
    try:
        ids.extend(int(value) for value in tokenizer.get_vocab().values())
    except Exception:
        pass

    for value in getattr(tokenizer, "all_special_ids", []) or []:
        if value is not None:
            ids.append(int(value))

    for name in (
        "pad_token_id",
        "unk_token_id",
        "bos_token_id",
        "eos_token_id",
        "cls_token_id",
        "sep_token_id",
        "mask_token_id",
    ):
        value = getattr(tokenizer, name, None)
        if value is not None:
            ids.append(int(value))

    try:
        length = int(len(tokenizer))
    except Exception:
        length = 0
    return max([length, *(value + 1 for value in ids)], default=length)


def validate_feature_ids(
    features: Iterable[Mapping[str, Sequence[int]]],
    *,
    num_labels: int,
) -> FeatureIdStats:
    """Validate token-classification features before any CUDA kernel runs."""
    feature_count = 0
    token_count = 0
    min_input_id: int | None = None
    max_input_id: int | None = None
    min_label_id: int | None = None
    max_label_id: int | None = None

    for feature_index, feature in enumerate(features):
        input_ids = [int(value) for value in feature.get("input_ids", [])]
        labels = [int(value) for value in feature.get("labels", [])]
        if not input_ids:
            raise ValueError(f"Feature {feature_index} has no input_ids")
        if len(input_ids) != len(labels):
            raise ValueError(
                f"Feature {feature_index} has len(input_ids)={len(input_ids)} "
                f"but len(labels)={len(labels)}"
            )

        local_min = min(input_ids)
        local_max = max(input_ids)
        if local_min < 0:
            raise ValueError(
                f"Feature {feature_index} contains a negative input ID: {local_min}"
            )
        min_input_id = local_min if min_input_id is None else min(min_input_id, local_min)
        max_input_id = local_max if max_input_id is None else max(max_input_id, local_max)

        valid_labels = [value for value in labels if value != -100]
        for label in valid_labels:
            if label < 0 or label >= num_labels:
                raise ValueError(
                    f"Feature {feature_index} contains invalid label ID {label}; "
                    f"expected -100 or [0, {num_labels - 1}]"
                )
        if valid_labels:
            local_label_min = min(valid_labels)
            local_label_max = max(valid_labels)
            min_label_id = (
                local_label_min
                if min_label_id is None
                else min(min_label_id, local_label_min)
            )
            max_label_id = (
                local_label_max
                if max_label_id is None
                else max(max_label_id, local_label_max)
            )

        feature_count += 1
        token_count += len(input_ids)

    if feature_count == 0 or min_input_id is None or max_input_id is None:
        raise ValueError("No tokenized training features were produced")

    return FeatureIdStats(
        feature_count=feature_count,
        token_count=token_count,
        min_input_id=min_input_id,
        max_input_id=max_input_id,
        min_label_id=min_label_id,
        max_label_id=max_label_id,
    )



def model_usable_max_length(model_config: Any, tokenizer: Any) -> int | None:
    """Return the largest safe padded sequence length for absolute positions.

    RoBERTa creates non-padding position IDs starting at ``pad_token_id + 1``.
    Therefore a table with ``max_position_embeddings`` rows supports at most
    ``max_position_embeddings - pad_token_id - 1`` non-padding positions.
    ViHealthBERT uses 258 rows and pad ID 1, so its safe length is 256.
    """
    max_positions = getattr(model_config, "max_position_embeddings", None)
    if max_positions is None:
        return None
    max_positions = int(max_positions)
    if max_positions <= 0:
        return None

    model_type = str(getattr(model_config, "model_type", "")).lower()
    position_type = str(
        getattr(model_config, "position_embedding_type", "absolute")
    ).lower()
    if model_type in {"roberta", "xlm-roberta", "camembert", "bart"} and position_type == "absolute":
        padding_idx = getattr(model_config, "pad_token_id", None)
        if padding_idx is None:
            padding_idx = getattr(tokenizer, "pad_token_id", 0)
        padding_idx = int(padding_idx or 0)
        return max_positions - padding_idx - 1
    return max_positions


def resolve_safe_window(
    requested_max_length: int,
    requested_stride: int,
    model_config: Any,
    tokenizer: Any,
) -> tuple[int, int, dict[str, int | bool | None]]:
    model_limit = model_usable_max_length(model_config, tokenizer)
    effective_max = int(requested_max_length)
    capped = False
    if model_limit is not None and effective_max > model_limit:
        effective_max = model_limit
        capped = True
    special_count = int(tokenizer.num_special_tokens_to_add(pair=False))
    capacity = effective_max - special_count
    if capacity <= 0:
        raise ValueError(
            f"Invalid effective max length {effective_max} with {special_count} special tokens"
        )
    effective_stride = min(int(requested_stride), max(0, capacity - 1))
    return effective_max, effective_stride, {
        "requested_max_length": int(requested_max_length),
        "requested_stride": int(requested_stride),
        "model_position_limit": model_limit,
        "effective_max_length": effective_max,
        "effective_stride": effective_stride,
        "capped": capped,
    }


def ensure_model_token_embeddings(
    model: Any,
    tokenizer: Any,
    *,
    max_feature_input_id: int,
) -> dict[str, int | bool]:
    """Resize model embeddings when tokenizer/features use larger token IDs."""
    embedding = model.get_input_embeddings()
    before = int(embedding.num_embeddings)
    tokenizer_required = tokenizer_required_vocab_size(tokenizer)
    required = max(tokenizer_required, int(max_feature_input_id) + 1)
    resized = required > before
    if resized:
        # Avoid depending on a particular transformers signature. The one-arg
        # form works across old and new versions and updates config.vocab_size.
        model.resize_token_embeddings(required)
    after = int(model.get_input_embeddings().num_embeddings)
    if max_feature_input_id >= after:
        raise ValueError(
            f"Embedding resize failed: max_input_id={max_feature_input_id}, "
            f"embedding_rows={after}"
        )
    return {
        "tokenizer_required_vocab_size": tokenizer_required,
        "embedding_rows_before": before,
        "embedding_rows_after": after,
        "resized": resized,
    }
