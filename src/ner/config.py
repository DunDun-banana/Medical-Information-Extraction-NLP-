from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class ModelConfig:
    pretrained_path: str
    fine_tuned_path: str
    fallback_repo_id: str
    local_files_only: bool = True


@dataclass(frozen=True)
class InferenceConfig:
    max_length: int = 256
    stride: int = 64
    batch_size: int = 32
    confidence_threshold: float = 0.70
    device: str = "auto"
    segment_max_chars: int = 2200


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 42
    learning_rate_stage1: float = 2e-5
    learning_rate_stage2: float = 8e-6
    synthetic_epochs: float = 2
    real_epochs: float = 4
    train_batch_size: int = 8
    eval_batch_size: int = 16
    gradient_accumulation_steps: int = 2
    weight_decay: float = 0.01
    warmup_ratio: float = 0.08
    label_smoothing: float = 0.02
    fp16: bool = True
    save_total_limit: int = 2
    early_stopping_patience: int = 2


@dataclass(frozen=True)
class NERConfig:
    model: ModelConfig
    inference: InferenceConfig
    training: TrainingConfig


def load_ner_config(path: Path) -> NERConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return NERConfig(
        model=ModelConfig(**data["model"]),
        inference=InferenceConfig(**data["inference"]),
        training=TrainingConfig(**data["training"]),
    )


def resolve_model_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path
