from __future__ import annotations
from src.common.schema import Entity
from src.ner.filter import _overlap_ratio

def filter_gliner_entities(rows:list[Entity],support_entities:list[Entity],thresholds:dict[str,float],uncorroborated_types:set[str],overlap_iou:float=.45,max_chars:dict[str,int]|None=None,max_words:dict[str,int]|None=None,require_exact_support:bool=False):
 max_chars=max_chars or {}; max_words=max_words or {}; accepted=[]; report=[]
 for e in rows:
  reason=None
  exact=any(s.type==e.type and s.start==e.start and s.end==e.end for s in support_entities)
  overlap=any(s.type==e.type and _overlap_ratio(e,s)>=overlap_iou for s in support_entities)
  if e.confidence<float(thresholds.get(e.type,.75)): reason="below_threshold"
  elif len(e.text)>int(max_chars.get(e.type,100)): reason="span_too_long"
  elif len(e.text.split())>int(max_words.get(e.type,16)): reason="too_many_words"
  elif e.text.count("\n"): reason="multiline"
  elif not (exact if require_exact_support else overlap) and e.type not in uncorroborated_types: reason="corroboration_required"
  ok=reason is None
  if ok:
   e.metadata["exact_support"]=exact; e.metadata["corroborated"]=overlap; accepted.append(e)
  report.append({"accepted":ok,"reason":reason or "accepted","entity":e.to_debug_dict()})
 return accepted,report
