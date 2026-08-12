"""Round-2 package with lazy imports."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from src.round2.factory import build_round2_hybrid_pipeline
__all__ = ["build_round2_hybrid_pipeline"]
def __getattr__(name: str) -> Any:
    if name == "build_round2_hybrid_pipeline":
        from src.round2.factory import build_round2_hybrid_pipeline
        return build_round2_hybrid_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
