"""NER package with lazy imports to keep training modules independent."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from src.ner.predictor import ViHealthBertPredictor
__all__ = ["ViHealthBertPredictor"]
def __getattr__(name: str) -> Any:
    if name == "ViHealthBertPredictor":
        from src.ner.predictor import ViHealthBertPredictor
        return ViHealthBertPredictor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
