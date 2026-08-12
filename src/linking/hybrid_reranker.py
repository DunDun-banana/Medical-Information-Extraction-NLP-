from __future__ import annotations
from pathlib import Path
from src.common.schema import Entity

class HybridCandidateReranker:
 def __init__(self,model_path:Path|None=None,threshold=.55,margin=.08,enabled=True):
  self.threshold=threshold; self.margin=margin; self.model=None
  if enabled and model_path and model_path.exists():
   try:
    from FlagEmbedding import FlagReranker
    self.model=FlagReranker(str(model_path),use_fp16=True)
   except Exception:
    try:
     from sentence_transformers import CrossEncoder
     self.model=CrossEncoder(str(model_path),local_files_only=True)
    except Exception: self.model=None
 def apply(self,raw_text:str,e:Entity):
  pool=e.metadata.get("candidate_pool") or []
  if e.type not in {"CHẨN_ĐOÁN","THUỐC"} or not pool: return e
  left=max(0,e.start-120); right=min(len(raw_text),e.end+120)
  query=f"MENTION: {e.text} | TYPE: {e.type} | SECTION: {e.section} | CONTEXT: {raw_text[left:right]}"
  pairs=[[query,f"{r.get('code','')} {r.get('title','')} {r.get('synonym','')}"] for r in pool]
  if self.model is None:
   e.metadata["hybrid_reranker"]={"decision":"abstain","reason":"model_unavailable"}; return e
  try:
   if hasattr(self.model,"compute_score"): scores=self.model.compute_score(pairs,normalize=True)
   else: scores=self.model.predict(pairs)
   ranked=sorted(zip(pool,[float(x) for x in scores]),key=lambda x:x[1],reverse=True)
   top=ranked[0]; second=ranked[1][1] if len(ranked)>1 else 0.0; keep=top[1]>=self.threshold and top[1]-second>=self.margin
   if keep:
    e.candidates=[str(top[0]["code"])]; e.metadata["link_method"]="icd_hybrid_reranker" if e.type=="CHẨN_ĐOÁN" else "rxnorm_hybrid_reranker"
   else: e.candidates=[]
   e.metadata["hybrid_reranker"]={"decision":"keep" if keep else "abstain","top_score":top[1],"margin":top[1]-second,"top":top[0]}
  except Exception as exc: e.metadata["hybrid_reranker"]={"decision":"abstain","reason":str(exc)}
  return e
