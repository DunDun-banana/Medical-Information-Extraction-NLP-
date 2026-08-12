from __future__ import annotations
import json
from src.common.schema import Entity
from src.llm.cached_client import CachedChatClient

SYSTEM="""Bạn là bộ kiểm định NER y khoa tiếng Việt. Trả JSON duy nhất: {\"decisions\":[{\"index\":0,\"keep\":true,\"type\":\"TRIỆU_CHỨNG\",\"reason\":\"...\"}]}. Loại heading, nhãn trường, yếu tố nguy cơ, lời khuyên, thủ thuật và span vỡ. Giữ test/result có cấu trúc và entity bệnh/triệu chứng/thuốc thật."""

class QwenCandidateVerifier:
 def __init__(self,client:CachedChatClient,enabled=True,batch_size=12,risky_sources=None):
  self.client=client; self.enabled=enabled; self.batch_size=batch_size; self.risky_sources=set(risky_sources or ["self_host_llm","vihealthbert","gliner"])
 def verify(self,raw_text:str,entities:list[Entity]):
  if not self.enabled: return entities,[]
  # Neural-model agreement is not enough to bypass verification: two models
  # can share the same boundary/type error. Only non-risky rule/structured
  # sources are considered safe without a second Qwen decision.
  safe=[e for e in entities if e.source not in self.risky_sources and not any(e.source.startswith(src) for src in self.risky_sources)]
  risky=[e for e in entities if e not in safe]
  report=[]; kept=list(safe)
  for left in range(0,len(risky),self.batch_size):
   batch=risky[left:left+self.batch_size]
   rows=[]
   for i,e in enumerate(batch):
    a=max(0,e.start-100); b=min(len(raw_text),e.end+100)
    rows.append({"index":i,"text":e.text,"type":e.type,"section":e.section,"context":raw_text[a:b]})
   try:
    raw=self.client.chat(SYSTEM,json.dumps({"candidates":rows},ensure_ascii=False))
    payload=json.loads(raw[raw.find('{'):raw.rfind('}')+1]); decisions={int(d["index"]):d for d in payload.get("decisions",[])}
   except Exception as exc:
    decisions={}; raw=str(exc)
   for i,e in enumerate(batch):
    d=decisions.get(i)
    # Missing verifier output is fail-closed for ordinary neural proposals.
    # A very-high-confidence multiword entity can survive transient JSON errors,
    # but single-token/generic spans cannot.
    fallback_ok=e.confidence>=.96 and len(e.text.strip().split())>=2
    keep=bool(d.get("keep")) if d else fallback_ok
    if d and d.get("type") in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN","TÊN_XÉT_NGHIỆM","KẾT_QUẢ_XÉT_NGHIỆM","THUỐC"}: e.type=d["type"]
    if keep: kept.append(e)
    report.append({"accepted":keep,"decision":d or {"fallback":True},"entity":e.to_debug_dict(),"raw":raw[:500]})
  return kept,report
