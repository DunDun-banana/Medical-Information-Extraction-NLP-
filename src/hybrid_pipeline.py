from __future__ import annotations

from src.common.schema import Entity
from src.linking import ICDLinker, RxNormLinker
from src.linking.candidate_gate import ContextualCandidateGate
from src.llm.entity_extractor import LLMEntityExtractor
from src.llm.filters import LLMEntityFilter
from src.postprocessing.merge import merge_entities
from src.rule_pipeline import RulePipeline


class HybridPipeline:
    def __init__(self, rule_pipeline: RulePipeline, llm_extractor: LLMEntityExtractor,
                 mode: str = "verify_fallback", rule_trust_threshold: float = 0.8,
                 icd_linker: ICDLinker | None = None, rxnorm_linker: RxNormLinker | None = None,
                 candidate_gate: ContextualCandidateGate | None = None):
        self.rule_pipeline = rule_pipeline
        self.llm_extractor = llm_extractor
        self.mode = mode
        self.rule_trust_threshold = rule_trust_threshold
        self.filter = LLMEntityFilter()
        self.icd_linker = icd_linker
        self.rxnorm_linker = rxnorm_linker
        self.candidate_gate = candidate_gate

    def extract(self, raw_text: str):
        sections, rule_entities = self.rule_pipeline.extract(raw_text)
        raw_llm_entities, traces = self.llm_extractor.extract(raw_text, sections)
        llm_entities, filter_report = self.filter.filter(raw_text, raw_llm_entities, rule_entities)

        if self.mode in {"augment", "llm_all_types"}:
            combined = rule_entities + llm_entities
        else:
            trusted: list[Entity] = []
            low_confidence: list[Entity] = []
            for entity in rule_entities:
                if entity.confidence < self.rule_trust_threshold or entity.source.endswith("_fallback"):
                    low_confidence.append(entity)
                else:
                    trusted.append(entity)
            supported = [
                rule for rule in low_confidence
                if any(rule.type == llm.type and max(rule.start, llm.start) < min(rule.end, llm.end) for llm in llm_entities)
            ]
            combined = trusted + supported + llm_entities

        merged = merge_entities(combined)
        for entity in merged:
            if self.icd_linker:
                self.icd_linker.link(entity)
            if self.rxnorm_linker:
                self.rxnorm_linker.link(entity)
            if self.candidate_gate:
                self.candidate_gate.apply(raw_text, entity)
            entity.validate(raw_text)
        return sections, rule_entities, raw_llm_entities, merged, traces, filter_report
