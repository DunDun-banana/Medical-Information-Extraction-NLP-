from __future__ import annotations
from src.assertion_scope import AssertionScopeClassifier
from src.common.schema import Entity
from src.linking.candidate_gate_round2 import EvidenceCandidateGate
from src.linking.hybrid_reranker import HybridCandidateReranker
from src.ner.filter import filter_vihealth_entities
from src.ner.gliner_filter import filter_gliner_entities
from src.postprocessing.merge_round2 import merge_round2_entities
from src.postprocessing.span_repair_v5 import SpanBoundaryRepairV5
from src.postprocessing.precision_cleanup_v53 import PrecisionCleanupV53
from src.round2.concept_normalizer import ConceptNormalizer
from src.round2.llm_filter import filter_llm_entities
from src.round2.llm_verifier import QwenCandidateVerifier
from src.round2.rule_filter import filter_rule_entities, requires_independent_support
from src.round2.semantic_gate import SemanticAdmissionGate

def _select(mode,base,exact,rules,vh,gl):
    if mode=='anchor_exact_rules': return base+exact
    if mode=='anchor_all_rules': return base+rules
    if mode in {'anchor_rules_models','all'}: return base+rules+vh+gl
    raise ValueError(f'Unsupported LLM proposal mode: {mode}')
def _high(rows,thresholds): return [e for e in rows if e.confidence>=float(thresholds.get(e.type,.95))]
def _promote(llm,support,conf):
    by={(e.start,e.end,e.type):e for e in support}
    for e in llm:
        s=by.get((e.start,e.end,e.type))
        if s:
            e.confidence=max(e.confidence,conf); e.metadata['independent_model_consensus']=True
            e.metadata['independent_support_source']=s.source


# Backward-compatible V3.1 helpers retained for tests and external scripts.
def _select_llm_proposals(mode, baseline_entities, exact_rules, rules_raw, ner_raw):
    return _select(mode, baseline_entities, exact_rules, rules_raw, ner_raw, [])

def _high_confidence_ner_support(ner_raw, thresholds):
    return _high(ner_raw, thresholds)

def _promote_independent_consensus(llm_raw, ner_support, promoted_confidence):
    support_map={(e.start,e.end,e.type):e for e in ner_support}
    for entity in llm_raw:
        support=support_map.get((entity.start,entity.end,entity.type))
        if support is None: continue
        entity.confidence=max(float(entity.confidence),float(promoted_confidence))
        entity.metadata["independent_vihealth_consensus"]=True
        entity.metadata["vihealth_consensus_confidence"]=float(support.confidence)

def _pool_from_linker(linker,entity,top_k=20):
    if linker is None: return []
    query=str(entity.metadata.get('normalized') or entity.metadata.get('canonical_name') or entity.text)
    try: rows=linker.search(query,top_k=top_k,method='hybrid')
    except Exception: return []
    pool=[]
    for r in rows:
        if entity.type=='CHẨN_ĐOÁN':
            code=r.get('code'); title=r.get('title','')
        else:
            code=r.get('rxcui') or r.get('RXCUI'); title=r.get('name') or r.get('STR','')
        if code: pool.append({'code':str(code),'title':str(title),'score':float(r.get('score',0.0))})
    return pool

class Round2HybridPipeline:
    def __init__(self,rule_pipeline,llm_extractor=None,vihealth_predictor=None,phobert_predictor=None,gliner_predictor=None,
                 qwen_verifier:QwenCandidateVerifier|None=None,semantic_gate:SemanticAdmissionGate|None=None,
                 assertion_classifier:AssertionScopeClassifier|None=None,concept_normalizer:ConceptNormalizer|None=None,
                 icd_linker=None,rxnorm_linker=None,hybrid_reranker:HybridCandidateReranker|None=None,
                 candidate_gate:EvidenceCandidateGate|None=None,span_repair:SpanBoundaryRepairV5|None=None,config=None):
        self.rule_pipeline=rule_pipeline; self.llm_extractor=llm_extractor
        self.vihealth_predictor=vihealth_predictor; self.phobert_predictor=phobert_predictor; self.gliner_predictor=gliner_predictor
        self.qwen_verifier=qwen_verifier; self.semantic_gate=semantic_gate
        self.assertion_classifier=assertion_classifier; self.concept_normalizer=concept_normalizer
        self.icd_linker=icd_linker; self.rxnorm_linker=rxnorm_linker
        self.hybrid_reranker=hybrid_reranker; self.candidate_gate=candidate_gate
        self.span_repair=span_repair; self.config=config
        self.precision_cleanup=None

    def set_precision_cleanup(self, cleanup:PrecisionCleanupV53|None):
        self.precision_cleanup=cleanup
        return self
    def extract(self,raw_text:str,baseline_entities:list[Entity]|None=None):
        baseline_entities=baseline_entities or []
        sections,rules_raw=self.rule_pipeline.extract(raw_text)
        exact_rules=[e for e in rules_raw if not requires_independent_support(e)]
        if self.vihealth_predictor: vh_raw,vh_debug=self.vihealth_predictor.extract(raw_text,sections)
        else: vh_raw,vh_debug=[],{'disabled':True}
        if self.phobert_predictor: ph_raw,ph_debug=self.phobert_predictor.extract(raw_text,sections)
        else: ph_raw,ph_debug=[],{'disabled':True}
        if self.gliner_predictor: gl_raw,gl_debug=self.gliner_predictor.extract(raw_text,sections)
        else: gl_raw,gl_debug=[],{'disabled':True}
        if self.llm_extractor:
            lc=self.config.llm; hints=_select(str(lc.get('proposal_mode','anchor_all_rules')),baseline_entities,exact_rules,rules_raw,vh_raw,gl_raw)
            llm_raw,llm_traces=self.llm_extractor.extract(raw_text,hints,sections=sections)
            support=[]
            if bool(lc.get('allow_model_consensus',True)):
                support+=_high(vh_raw,self.config.vihealthbert.get('thresholds',{}))
                support+=_high(ph_raw,self.config.phobert.get('thresholds',{}))
                support+=_high(gl_raw,self.config.gliner.get('thresholds',{}))
                _promote(llm_raw,support,float(lc.get('consensus_llm_confidence',.84)))
            llm,llm_report=filter_llm_entities(raw_text,llm_raw,exact_rules,
                support_entities=baseline_entities+support,
                min_confidence=float(self.config.ensemble.get('llm_min_confidence',.68)),
                allow_uncorroborated_types=set(lc.get('allow_uncorroborated_types',[])),
                overlap_iou=float(lc.get('support_overlap_iou',.4)),
                max_chars={k:int(v) for k,v in lc.get('max_chars_by_type',{}).items()},
                max_words={k:int(v) for k,v in lc.get('max_words_by_type',{}).items()},
                require_exact_support=bool(lc.get('require_exact_support',False)))
        else: llm_raw,llm,llm_traces,llm_report=[],[],[],[]
        if self.phobert_predictor:
            pc=self.config.phobert
            ph,ph_report=filter_vihealth_entities(ph_raw,exact_rules+gl_raw+vh_raw,llm,
                thresholds={k:float(v) for k,v in pc.get('thresholds',{}).items()},
                uncorroborated_types=set(pc.get('uncorroborated_types',[])),overlap_iou=float(pc.get('overlap_iou',.4)),
                anchor_entities=baseline_entities,max_chars={k:int(v) for k,v in pc.get('max_chars_by_type',{}).items()},
                max_words={k:int(v) for k,v in pc.get('max_words_by_type',{}).items()},require_exact_support=bool(pc.get('require_exact_support',True)))
        else: ph,ph_report=[],[]
        if self.vihealth_predictor:
            vc=self.config.vihealthbert
            vh,vh_report=filter_vihealth_entities(vh_raw,exact_rules+gl_raw+ph_raw,llm,
                thresholds={k:float(v) for k,v in vc.get('thresholds',{}).items()},
                uncorroborated_types=set(vc.get('uncorroborated_types',[])),
                overlap_iou=float(vc.get('overlap_iou',.45)),anchor_entities=baseline_entities,
                max_chars={k:int(v) for k,v in vc.get('max_chars_by_type',{}).items()},
                max_words={k:int(v) for k,v in vc.get('max_words_by_type',{}).items()},
                require_exact_support=bool(vc.get('require_exact_support',False)))
        else: vh,vh_report=[],[]
        if self.gliner_predictor:
            gc=self.config.gliner
            gl,gl_report=filter_gliner_entities(gl_raw,baseline_entities+exact_rules+llm+vh_raw+ph_raw,
                thresholds={k:float(v) for k,v in gc.get('thresholds',{}).items()},
                uncorroborated_types=set(gc.get('uncorroborated_types',[])),
                overlap_iou=float(gc.get('overlap_iou',.45)),
                max_chars={k:int(v) for k,v in gc.get('max_chars_by_type',{}).items()},
                max_words={k:int(v) for k,v in gc.get('max_words_by_type',{}).items()},
                require_exact_support=bool(gc.get('require_exact_support',False)))
        else: gl,gl_report=[],[]
        rules,rule_report=filter_rule_entities(rules_raw,baseline_entities=baseline_entities,
            ner_raw_entities=vh_raw+ph_raw+gl_raw,
            thresholds={k:float(v) for k,v in self.config.pipeline.get('rule_support_thresholds',self.config.vihealthbert.get('thresholds',{})).items()},
            mode=str(self.config.pipeline.get('rule_addition_mode','all')))
        additions=[]
        if bool(self.config.pipeline.get('use_rules',True)): additions+=rules
        additions+=llm+vh+ph+gl
        semantic_report=[]
        if self.semantic_gate: additions,semantic_report=self.semantic_gate.filter(raw_text,additions,sections,accepted_context=baseline_entities)
        verifier_report=[]
        if self.qwen_verifier: additions,verifier_report=self.qwen_verifier.verify(raw_text,additions)
        precision_cleanup_report=[]
        if self.precision_cleanup:
            additions,precision_cleanup_report=self.precision_cleanup.apply(additions)
        span_repair_report=[]
        if self.span_repair:
            baseline_entities,additions,span_repair_report=self.span_repair.apply(
                raw_text,baseline_entities,additions,repair_candidates=additions+rules_raw
            )
        merged=merge_round2_entities(raw_text,baseline_entities+additions,
            prefer_shorter_within=float(self.config.ensemble.get('prefer_shorter_span_within',.05)),
            assertion_mode=str(self.config.pipeline.get('assertion_mode','preserve_source')))
        assertion_report=[]
        if self.assertion_classifier:
            self.assertion_classifier.apply_many(raw_text,merged)
            assertion_report=[{'entity':e.to_debug_dict(),'assertion_scope':e.metadata['assertion_scope']} for e in merged if 'assertion_scope' in e.metadata]
        norm_debug={'disabled':True}
        if self.concept_normalizer: norm_debug=self.concept_normalizer.normalize(merged)
        preserve=bool(self.config.linking.get('preserve_baseline_candidates',True)); rerank_report=[]
        for e in merged:
            prior=list(e.candidates); anchor=e.source=='baseline_anchor' or bool(e.metadata.get('baseline_anchor'))
            if anchor and preserve:
                e.candidates=prior
                e.metadata['candidate_preserved_from_baseline']=True
                e.validate(raw_text)
                continue
            if self.icd_linker: self.icd_linker.link(e)
            if self.rxnorm_linker: self.rxnorm_linker.link(e)
            if self.hybrid_reranker and not e.candidates and e.type in {'CHẨN_ĐOÁN','THUỐC'}:
                linker=self.icd_linker if e.type=='CHẨN_ĐOÁN' else self.rxnorm_linker
                e.metadata['candidate_pool']=_pool_from_linker(linker,e,int(self.config.linking.get('reranker_top_k',20)))
                self.hybrid_reranker.apply(raw_text,e)
                if 'hybrid_reranker' in e.metadata: rerank_report.append({'entity':e.to_debug_dict(),'reranker':e.metadata['hybrid_reranker']})
            if self.candidate_gate: self.candidate_gate.apply(raw_text,e)
            if preserve and anchor and prior: e.candidates=prior[:1]; e.metadata['candidate_preserved_from_baseline']=True
            e.validate(raw_text)
        return {'sections':sections,'baseline_entities':baseline_entities,
          'rule_entities_raw':rules_raw,'rule_entities':rules,'rule_filter_report':rule_report,
          'vihealth_raw_entities':vh_raw,'vihealth_entities':vh,'vihealth_debug':vh_debug,'vihealth_filter_report':vh_report,
          'phobert_raw_entities':ph_raw,'phobert_entities':ph,'phobert_debug':ph_debug,'phobert_filter_report':ph_report,
          'gliner_raw_entities':gl_raw,'gliner_entities':gl,'gliner_debug':gl_debug,'gliner_filter_report':gl_report,
          'llm_raw_entities':llm_raw,'llm_entities':llm,'llm_traces':llm_traces,'llm_filter_report':llm_report,
          'semantic_gate_report':semantic_report,'qwen_verifier_report':verifier_report,
          'precision_cleanup_v53_report':precision_cleanup_report,
          'span_repair_report':span_repair_report,
          'assertion_scope_report':assertion_report,'normalization_debug':norm_debug,
          'hybrid_reranker_report':rerank_report,'merged_entities':merged}
