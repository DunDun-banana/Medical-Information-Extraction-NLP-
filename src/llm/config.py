from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServerConfig:
    endpoint: str
    model: str
    timeout_seconds: int = 180
    use_json_mode: bool = False


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 512
    presence_penalty: float = 0.0
    seed: int = 42


@dataclass(frozen=True)
class ExtractionConfig:
    mode: str = "verify_fallback"
    chunk_strategy: str = "auto"
    document_fallback_min_coverage: float = 0.65
    record_header_pattern: str = ""
    rule_trust_threshold: float = 0.8
    max_chunk_chars: int = 2600
    chunk_overlap_chars: int = 120
    min_entity_chars: int = 2
    max_entity_chars: int = 180
    max_occurrences_per_row: int = 3
    assertion_mode: str = "llm_plus_historical_rule"
    allowed_types: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    split_prompt_by_type: bool = True
    prompt_groups: tuple[str, ...] = ("clinical", "labs")


@dataclass(frozen=True)
class LinkingConfig:

    enable_icd_embedding: bool = True
    enable_rxnorm_embedding: bool = True

    embedding_model: str = "BAAI/bge-small-en-v1.5"

    icd_embedding_index: str = ""
    rxnorm_embedding_index: str = ""

    embedding_top_k: int = 10
    embedding_threshold: float = 0.75

    enable_icd_bm25: bool = True

    icd_top_k: int = 10
    icd_min_score: float = 3.4
    icd_min_margin: float = 0.45

    enable_rxnorm_exact: bool = True

    enable_candidate_gate: bool = True
    candidate_gate_mode: str = "balanced"


@dataclass(frozen=True)
class LLMConfig:
    server: ServerConfig
    generation: GenerationConfig
    extraction: ExtractionConfig
    linking: LinkingConfig


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_llm_config(path: Path) -> LLMConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    server = _require_dict(payload.get("server", {}), "server")
    generation = _require_dict(payload.get("generation", {}), "generation")
    extraction = _require_dict(payload.get("extraction", {}), "extraction")
    linking = _require_dict(payload.get("linking", {}), "linking")

    endpoint = str(server.get("endpoint", "")).strip()
    model = str(server.get("model", "")).strip()
    if not endpoint or not model:
        raise ValueError("server.endpoint and server.model are required")

    mode = str(extraction.get("mode", "verify_fallback"))
    if mode not in {"augment", "verify_fallback", "llm_all_types"}:
        raise ValueError(f"Unsupported extraction.mode: {mode}")

    chunk_strategy = str(extraction.get("chunk_strategy", "auto"))
    if chunk_strategy not in {"auto", "sections", "records", "document"}:
        raise ValueError(f"Unsupported chunk_strategy: {chunk_strategy}")

    assertion_mode = str(extraction.get("assertion_mode", "llm_plus_historical_rule"))
    if assertion_mode not in {"llm_only", "rule_only", "union", "llm_plus_historical_rule"}:
        raise ValueError(f"Unsupported assertion_mode: {assertion_mode}")

    return LLMConfig(
        server=ServerConfig(
            endpoint=endpoint,
            model=model,
            timeout_seconds=int(server.get("timeout_seconds", 180)),
            use_json_mode=bool(server.get("use_json_mode", False)),
        ),
        generation=GenerationConfig(
            temperature=float(generation.get("temperature", 0.0)),
            top_p=float(generation.get("top_p", 1.0)),
            max_tokens=int(generation.get("max_tokens", 512)),
            presence_penalty=float(generation.get("presence_penalty", 0.0)),
            seed=int(generation.get("seed", 42)),
        ),
        extraction=ExtractionConfig(
            mode=mode,
            chunk_strategy=chunk_strategy,
            document_fallback_min_coverage=float(
                extraction.get("document_fallback_min_coverage", 0.65)
            ),
            record_header_pattern=str(extraction.get("record_header_pattern", "")),
            rule_trust_threshold=float(extraction.get("rule_trust_threshold", 0.8)),
            max_chunk_chars=int(extraction.get("max_chunk_chars", 2600)),
            chunk_overlap_chars=int(extraction.get("chunk_overlap_chars", 120)),
            min_entity_chars=int(extraction.get("min_entity_chars", 2)),
            max_entity_chars=int(extraction.get("max_entity_chars", 180)),
            max_occurrences_per_row=int(extraction.get("max_occurrences_per_row", 3)),
            assertion_mode=assertion_mode,
            allowed_types=tuple(str(x) for x in extraction.get("allowed_types", [])),
            sections=tuple(str(x) for x in extraction.get("sections", [])),
            split_prompt_by_type=bool(extraction.get("split_prompt_by_type", True)),
            prompt_groups=tuple(
                str(x) for x in extraction.get("prompt_groups", ["clinical", "labs"])
                if str(x) in {"clinical", "labs", "all"}
            ),
        ),
        
        # linking=LinkingConfig(
        #     enable_icd_bm25=bool(linking.get("enable_icd_bm25", True)),
        #     icd_top_k=int(linking.get("icd_top_k", 10)),
        #     icd_min_score=float(linking.get("icd_min_score", 3.4)),
        #     icd_min_margin=float(linking.get("icd_min_margin", 0.45)),
        #     enable_rxnorm_exact=bool(linking.get("enable_rxnorm_exact", True)),
        # ),
        
        linking=LinkingConfig(

            # ---------- Embedding ----------
            enable_icd_embedding=bool(
                linking.get("enable_icd_embedding", True)
            ),

            enable_rxnorm_embedding=bool(
                linking.get("enable_rxnorm_embedding", True)
            ),

            embedding_model=str(
                linking.get(
                    "embedding_model",
                    "BAAI/bge-small-en-v1.5",
                )
            ),

            icd_embedding_index=str(
                linking.get(
                    "icd_embedding_index",
                    "",
                )
            ),

            rxnorm_embedding_index=str(
                linking.get(
                    "rxnorm_embedding_index",
                    "",
                )
            ),

            embedding_top_k=int(
                linking.get("embedding_top_k", 10)
            ),

            embedding_threshold=float(
                linking.get("embedding_threshold", 0.75)
            ),

            # ---------- BM25 ----------
            enable_icd_bm25=bool(
                linking.get("enable_icd_bm25", True)
            ),

            icd_top_k=int(
                linking.get("icd_top_k", 10)
            ),

            icd_min_score=float(
                linking.get("icd_min_score", 3.4)
            ),

            icd_min_margin=float(
                linking.get("icd_min_margin", 0.45)
            ),

            # ---------- Exact ----------
            enable_rxnorm_exact=bool(
                linking.get("enable_rxnorm_exact", True)
            ),

            enable_candidate_gate=bool(
                linking.get("enable_candidate_gate", True)
            ),

            candidate_gate_mode=str(
                linking.get("candidate_gate_mode", "balanced")
            ),
        ),
    )
