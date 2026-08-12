from __future__ import annotations
from pathlib import Path
import warnings
from src.assertion_scope import AssertionScopeClassifier,AssertionScopeConfig
from src.linking import ICDLinker,RxNormLinker
from src.linking.candidate_gate_round2 import EvidenceCandidateGate
from src.linking.hybrid_reranker import HybridCandidateReranker
from src.llm.cached_client import CachedChatClient
from src.llm.client import LocalChatClient
from src.llm.config import GenerationConfig,ServerConfig
from src.ner.gliner_predictor import GLiNERPredictor
from src.ner.predictor import ViHealthBertPredictor, PhoBertPredictor
from src.round2.config import load_hybrid_round2_config
from src.round2.concept_normalizer import ConceptNormalizer
from src.round2.llm_extractor import LLMExtractionOptions,Round2LLMExtractor
from src.round2.llm_verifier import QwenCandidateVerifier
from src.round2.pipeline import Round2HybridPipeline
from src.round2.semantic_gate import SemanticAdmissionGate
from src.postprocessing.span_repair_v5 import SpanBoundaryRepairV5, SpanRepairConfig
from src.postprocessing.precision_cleanup_v53 import PrecisionCleanupV53, PrecisionCleanupV53Config
from src.rule_pipeline import RulePipeline

def _resolve(root,value):
 p=Path(str(value));return p if p.is_absolute() else root/p

def build_round2_hybrid_pipeline(root:Path,config_path:Path|None=None,disable_llm=False,disable_vihealthbert=False,disable_gliner=False,disable_phobert=False):
 cfg=load_hybrid_round2_config(config_path or root/'configs/hybrid_round2_v5_context.yaml')
 rule=RulePipeline(root/'data/mappings/section_aliases.yaml',root/'data/mappings/test_aliases_rule.csv',root/'data/processed/rule_drug_terms.csv',root/'data/mappings/symptom_aliases_rule.csv',root/'data/mappings/diagnosis_aliases_rule.csv',enable_structured=bool(cfg.pipeline.get('use_structured',True)),enable_contextual=bool(cfg.pipeline.get('use_contextual_clinical',True)))
 cached=None;llm=None
 if bool(cfg.pipeline.get('use_llm',True)) and not disable_llm:
  raw=LocalChatClient(ServerConfig(**cfg.server),GenerationConfig(**cfg.generation));cached=CachedChatClient(raw,_resolve(root,cfg.llm.get('cache_dir','data/cache/llm/round2_v4_proposer')))
  llm=Round2LLMExtractor(cached,LLMExtractionOptions(max_chars=int(cfg.chunking.get('max_chars',1800)),overlap_chars=int(cfg.chunking.get('overlap_chars',120)),min_medical_signal=int(cfg.chunking.get('min_medical_signal',1)),min_entity_chars=int(cfg.llm.get('min_entity_chars',2)),max_entity_chars=int(cfg.llm.get('max_entity_chars',180)),max_occurrences_per_row=int(cfg.llm.get('max_occurrences_per_row',4)),allowed_types=tuple(cfg.llm.get('allowed_types',[]))))
 vh=None
 if bool(cfg.pipeline.get('use_vihealthbert',True)) and not disable_vihealthbert:
  try: vh=ViHealthBertPredictor(root,_resolve(root,cfg.vihealthbert.get('config_path','configs/ner_round2_v4_curriculum.yaml')))
  except (FileNotFoundError,ImportError) as exc:warnings.warn(str(exc)+' ViHealthBERT disabled.')
 ph=None
 if bool(cfg.pipeline.get('use_phobert',False)) and not disable_phobert:
  try: ph=PhoBertPredictor(root,_resolve(root,cfg.phobert.get('config_path','configs/ner_round2_v5_4_phobert.yaml')))
  except (FileNotFoundError,ImportError) as exc:warnings.warn(str(exc)+' PhoBERT disabled.')
 gl=None
 if bool(cfg.pipeline.get('use_gliner',True)) and not disable_gliner:
  gc=cfg.gliner
  try: gl=GLiNERPredictor(
   _resolve(root,gc.get('model_path','models/gliner/medical-ie-v4')),
   float(gc.get('raw_threshold',.45)),
   gc.get('labels'),
   str(gc.get('device','auto')),
   int(gc.get('inference_max_tokens',220)),
   int(gc.get('inference_overlap_tokens',64)),
   int(gc.get('model_max_length',256)),
  )
  except (FileNotFoundError,ImportError) as exc:warnings.warn(str(exc)+' GLiNER disabled.')
 semantic=SemanticAdmissionGate(cfg.semantic_gate) if bool(cfg.pipeline.get('use_semantic_gate',True)) else None
 verifier=None
 if cached and bool(cfg.pipeline.get('use_qwen_verifier',True)) and bool(cfg.verifier.get('enabled',True)):
  verifier=QwenCandidateVerifier(cached,True,int(cfg.verifier.get('batch_size',12)),cfg.verifier.get('risky_sources'))
 ac=None
 if bool(cfg.pipeline.get('use_assertion_scope',True)):
  a=cfg.assertion_scope;ac=AssertionScopeClassifier(AssertionScopeConfig(_resolve(root,a.get('model_path','models/assertion_scope/v4_scope.joblib')),bool(a.get('preserve_baseline',True)),float(a.get('threshold',.5)),bool(a.get('rules_union',True))))
 normalizer=None
 if bool(cfg.pipeline.get('use_concept_normalizer',False)) and cached:
  n=cfg.normalization;normalizer=ConceptNormalizer(cached,_resolve(root,n.get('cache_path','data/cache/llm/concept_normalization_v4.json')),int(n.get('batch_size',24)),n.get('types',['CHẨN_ĐOÁN','THUỐC']))
 lc=cfg.linking;model=str(_resolve(root,lc.get('embedding_model','models/embeddings/bge-m3')).resolve());ii=_resolve(root,lc.get('icd_embedding_index','data/processed/icd_embeddings_bge_m3.npz'));ri=_resolve(root,lc.get('rxnorm_embedding_index','data/processed/rxnorm_embeddings_bge_m3.npz'))
 icd_catalog=_resolve(root,lc.get('icd_catalog','data/processed/icd10_codes.csv'))
 icd_byt=_resolve(root,lc.get('icd_byt_catalog','data/processed/icd10_byt_v5.csv'))
 rx_catalog=_resolve(root,lc.get('rxnorm_catalog','data/external/rxnorm_lookup_flat.json'))
 icd=ICDLinker(icd_catalog,root/'data/mappings/diagnosis_aliases_rule.csv',top_k=int(lc.get('icd_top_k',20)),min_score=float(lc.get('icd_min_score',3.4)),min_margin=float(lc.get('icd_min_margin',.55)),enable_embedding=bool(lc.get('enable_icd_embedding',True) and ii.exists()),embedding_model=model,embedding_index=ii,embedding_top_k=int(lc.get('embedding_top_k',20)),embedding_threshold=float(lc.get('embedding_threshold',.78)),knowledge_mapping_path=root/'knowledge/icd_mapping_final.json',byt_csv=icd_byt) if bool(lc.get('enable_icd_bm25',True)) else None
 rx=RxNormLinker(rx_catalog,enable_embedding=bool(lc.get('enable_rxnorm_embedding',True) and ri.exists()),embedding_model=model,embedding_index=ri,embedding_top_k=int(lc.get('embedding_top_k',20)),embedding_threshold=float(lc.get('embedding_threshold',.78)),knowledge_mapping_path=root/'knowledge/drug_mapping_final.json') if bool(lc.get('enable_rxnorm_exact',True)) else None
 rr=None;r=cfg.reranker
 if bool(r.get('enabled',True)): rr=HybridCandidateReranker(_resolve(root,r.get('model_path','models/rerankers/bge-reranker-v2-m3')),float(r.get('threshold',.55)),float(r.get('margin',.08)),True)
 sr_cfg=cfg.pipeline.get('span_repair',{})
 span_repair=SpanBoundaryRepairV5(SpanRepairConfig(
  enabled=bool(sr_cfg.get('enabled',True)),
  remove_midword_anchors=bool(sr_cfg.get('remove_midword_anchors',True)),
  repair_exact_type=bool(sr_cfg.get('repair_exact_type',True)),
  repair_cross_type_exact=bool(sr_cfg.get('repair_cross_type_exact',True)),
  contextual_test_overrides_diagnosis=bool(sr_cfg.get('contextual_test_overrides_diagnosis',True)),
 ))
 cleanup_cfg=cfg.raw.get('precision_cleanup_v53',{})
 cleanup=PrecisionCleanupV53(PrecisionCleanupV53Config(
  enabled=bool(cleanup_cfg.get('enabled',False)),
  repair_types=bool(cleanup_cfg.get('repair_types',True)),
  drop_generic_labels=bool(cleanup_cfg.get('drop_generic_labels',True)),
  drop_category_conflicts=bool(cleanup_cfg.get('drop_category_conflicts',True)),
 ))
 return Round2HybridPipeline(rule_pipeline=rule,llm_extractor=llm,vihealth_predictor=vh,phobert_predictor=ph,gliner_predictor=gl,qwen_verifier=verifier,semantic_gate=semantic,assertion_classifier=ac,concept_normalizer=normalizer,icd_linker=icd,rxnorm_linker=rx,hybrid_reranker=rr,candidate_gate=EvidenceCandidateGate(str(lc.get('candidate_gate_mode','hybrid_reranker'))),span_repair=span_repair,config=cfg).set_precision_cleanup(cleanup)
