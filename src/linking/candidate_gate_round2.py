from __future__ import annotations
from src.common.schema import Entity
TRUSTED_EXACT={'icd_explicit_regex','icd_dictionary_exact','icd_alias_exact','icd_canonical_exact','icd_knowledge_exact','rxnorm_seed_exact','rxnorm_exact','rxnorm_exact_normalized','rxnorm_knowledge_exact','rxnorm_clinical_strength'}
TRUSTED_RERANKED={'icd_hybrid_reranker','rxnorm_hybrid_reranker'}
class EvidenceCandidateGate:
 def __init__(self,mode='exact_only'): self.mode=mode
 def apply(self,raw_text,e:Entity):
  if e.type not in {'CHẨN_ĐOÁN','THUỐC'} or not e.candidates:return e
  before=list(e.candidates); method=str(e.metadata.get('link_method') or '')
  keep=method in TRUSTED_EXACT; reason='trusted_exact' if keep else 'untrusted_method'
  if self.mode in {'reranker','hybrid_reranker','strict_hybrid'} and method in TRUSTED_RERANKED:
   keep=e.metadata.get('hybrid_reranker',{}).get('decision')=='keep'; reason='trusted_hybrid_reranker' if keep else 'reranker_abstained'
  elif self.mode!='exact_only' and method=='icd_bm25_calibrated':
   d=e.metadata.get('icd_bm25_debug',{}); keep=float(d.get('coverage',0))>=.72 and float(d.get('margin',0))>=.65 and float(d.get('adjusted',0))>=4.5; reason='bm25_strict_evidence' if keep else 'bm25_evidence_too_weak'
  elif self.mode!='exact_only' and method in {'icd_embedding_calibrated','rxnorm_embedding_calibrated'}:
   key='icd_embedding' if method.startswith('icd') else 'rxnorm_embedding'; d=e.metadata.get(key,{})
   keep=float(d.get('score',0))>=.86 and float(d.get('margin',0))>=.065; reason='embedding_strict_evidence' if keep else 'embedding_evidence_too_weak'
  e.candidates=e.candidates[:1] if keep else []
  e.metadata['candidate_gate']={'mode':self.mode,'decision':'keep' if keep else 'clear','reason':reason,'before':before,'after':list(e.candidates)}
  return e
