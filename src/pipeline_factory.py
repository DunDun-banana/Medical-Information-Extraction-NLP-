from __future__ import annotations

from pathlib import Path

from src.hybrid_pipeline import HybridPipeline
from src.linking import ICDLinker, RxNormLinker
from src.linking.candidate_gate import ContextualCandidateGate
from src.llm.client import LocalChatClient
from src.llm.config import load_llm_config
from src.llm.entity_extractor import LLMEntityExtractor
from src.rule_pipeline import RulePipeline


def build_hybrid_pipeline(root: Path) -> HybridPipeline:
    cfg = load_llm_config(root / "configs/llm.yaml")
    rule = RulePipeline(
        section_aliases_path=root / "data/mappings/section_aliases.yaml",
        test_aliases_path=root / "data/mappings/test_aliases_rule.csv",
        drug_terms_path=root / "data/processed/rule_drug_terms.csv",
        symptom_aliases_path=root / "data/mappings/symptom_aliases_rule.csv",
        diagnosis_aliases_path=root / "data/mappings/diagnosis_aliases_rule.csv",
    )
    llm = LLMEntityExtractor(LocalChatClient(cfg.server, cfg.generation), cfg.extraction)
    icd = ICDLinker(
        icd_csv=root / "data/processed/icd10_codes.csv",
        aliases_csv=root / "data/mappings/diagnosis_aliases_rule.csv",

        top_k=cfg.linking.icd_top_k,
        min_score=cfg.linking.icd_min_score,
        min_margin=cfg.linking.icd_min_margin,

        enable_embedding=cfg.linking.enable_icd_embedding,
        embedding_model=cfg.linking.embedding_model,
        embedding_index=root / cfg.linking.icd_embedding_index,
        embedding_top_k=cfg.linking.embedding_top_k,
        embedding_threshold=max(cfg.linking.embedding_threshold, 0.78),
        knowledge_mapping_path=root / "knowledge/icd_mapping_final.json",
    ) if cfg.linking.enable_icd_bm25 else None
    # rxnorm = RxNormLinker(root / "data/processed/rxnorm_terms.parquet") if cfg.linking.enable_rxnorm_exact else None
    rxnorm = RxNormLinker(
        parquet_path=root / "data/processed/rxnorm_terms.parquet",

        enable_embedding=cfg.linking.enable_rxnorm_embedding,
        embedding_model=cfg.linking.embedding_model,
        embedding_index=root / cfg.linking.rxnorm_embedding_index,
        embedding_top_k=cfg.linking.embedding_top_k,
        embedding_threshold=max(cfg.linking.embedding_threshold, 0.78),
        knowledge_mapping_path=root / "knowledge/drug_mapping_final.json",
    )
    candidate_gate = ContextualCandidateGate(
        mode=cfg.linking.candidate_gate_mode
    ) if cfg.linking.enable_candidate_gate else None
    return HybridPipeline(
        rule_pipeline=rule,
        llm_extractor=llm,
        mode=cfg.extraction.mode,
        rule_trust_threshold=cfg.extraction.rule_trust_threshold,
        icd_linker=icd,
        rxnorm_linker=rxnorm,
        candidate_gate=candidate_gate,
    )
