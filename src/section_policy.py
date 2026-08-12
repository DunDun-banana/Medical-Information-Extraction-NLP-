from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SECTION_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "data/mappings/section_policy.yaml"
)


@lru_cache(maxsize=4)
def load_section_policy(path: str | None = None) -> dict[str, dict[str, Any]]:
    policy_path = Path(path) if path else DEFAULT_SECTION_POLICY_PATH
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    return {
        str(section): dict(values or {})
        for section, values in payload.items()
    }


def section_policy(section: str) -> dict[str, Any]:
    payload = load_section_policy()
    return payload.get(section, payload.get("UNKNOWN", {}))


def sections_with_flag(flag: str) -> set[str]:
    return {
        section
        for section, values in load_section_policy().items()
        if bool(values.get(flag, False))
    }
