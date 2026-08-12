from __future__ import annotations

import re
from src.common.schema import Entity
from src.section_parser import SectionParser, SectionSpan

TEST_SECTIONS={"LAB_RESULT","LAB_RESULT_EXTRA","FAQ_LAB_RESULT","IMAGING","IMAGING_EXTRA","PHYSICAL_EXAM","HOSPITAL_ASSESSMENT","VITAL_SIGNS"}
RESULT_WORDS=re.compile(r"(?ix)^(?:âm\s+tính|dương\s+tính|bình\s+thường|bất\s+thường|chờ\s+kết\s+quả|không\s+(?:ghi\s+nhận|phát\s+hiện|thấy)|tăng|giảm|cao|thấp|\d+(?:[.,]\d+)?(?:\s*[a-zA-Z%/µμ]+)?)$")
TEST_HINT=re.compile(r"(?ix)(?:xét\s+nghiệm|ct\b|mri\b|x-?quang|siêu\s+âm|ecg\b|điện\s+tâm\s+đồ|nội\s+soi|sinh\s+thiết|spo2|bili|bilirubin|ferritin|ceruloplasmin|men\s+gan|creatinin|ure|glucose|hba1c|crp|procalcitonin)")
IMAGING=re.compile(r"(?ix)\b(?:chụp\s+)?(?:ct|mri|x-?quang|siêu\s+âm|hidi|hida|ercp|mrcp|ecg|điện\s+tâm\s+đồ)\b")
GENERIC_TEST_LABEL=re.compile(r"(?ix)^(?:các\s+xét\s+nghiệm|làm\s+thêm|thực\s+hiện|đặt\s+ống|theo\s+dõi)\b")
FIELD_LABELS={"vị trí","tính chất","lan","thời gian","mức độ","yếu tố tăng","yếu tố giảm"}
VITAL_LABELS={"mạch","huyết áp","nhịp thở","nhiệt độ","spo2","cân nặng","chiều cao"}


def _trim(raw:str,start:int,end:int)->tuple[int,int]:
 while start<end and raw[start].isspace(): start+=1
 while end>start and raw[end-1].isspace(): end-=1
 while end>start and raw[end-1] in ",;.": end-=1
 return start,end


def _top_level_colon(line:str)->int:
 depth=0
 for i,ch in enumerate(line):
  if ch in "([{" : depth+=1
  elif ch in ")]}": depth=max(0,depth-1)
  elif ch==":" and depth==0: return i
 return -1

class StructuredClinicalExtractor:
 def extract(self, raw_text:str, sections:list[SectionSpan])->list[Entity]:
  out=[]
  for sec in sections:
   content=raw_text[sec.content_start:sec.content_end]
   cursor=sec.content_start
   for line in content.splitlines(keepends=True):
    clean=line.rstrip("\r\n")
    line_start=cursor; cursor+=len(line)
    if not clean.strip(): continue
    # imaging modalities are strong test names in imaging/lab context
    if sec.canonical in {"IMAGING","IMAGING_EXTRA","FAQ_LAB_RESULT","LAB_RESULT","LAB_RESULT_EXTRA"}:
     for m in IMAGING.finditer(clean):
      s,e=_trim(raw_text,line_start+m.start(),line_start+m.end())
      text=raw_text[s:e]
      if text.casefold() in {"chẩn đoán hình ảnh","kết quả chẩn đoán hình ảnh"}: continue
      out.append(Entity(text=text,start=s,end=e,type="TÊN_XÉT_NGHIỆM",confidence=.98,source="structured_imaging_test",section=sec.canonical,metadata={"structured":True,"trusted":True}))
    colon=_top_level_colon(clean)
    if colon<0: continue
    left=clean[:colon].strip(" \t-*•0123456789.)")
    right=clean[colon+1:].strip()
    if not left or not right: continue
    lnorm=re.sub(r"\s+"," ",left.casefold()).strip()
    ls=line_start+clean.index(left); le=ls+len(left)
    rs=line_start+colon+1
    while rs<line_start+len(clean) and raw_text[rs].isspace(): rs+=1
    re_=line_start+len(clean); rs,re_=_trim(raw_text,rs,re_)
    # symptom-attribute values are proposal-only, not trusted rules
    if lnorm in FIELD_LABELS:
     out.append(Entity(text=raw_text[rs:re_],start=rs,end=re_,type="TRIỆU_CHỨNG",confidence=.62,source="structured_symptom_attribute",section=sec.canonical,metadata={"structured":True,"requires_model_support":True,"field_label":left}))
     continue
    is_vital = lnorm in VITAL_LABELS
    is_imaging = bool(IMAGING.search(left))
    is_test_hint = bool(TEST_HINT.search(left))
    if sec.canonical not in TEST_SECTIONS and not (is_vital or is_imaging): continue
    if GENERIC_TEST_LABEL.search(lnorm): continue
    if len(left.split()) > 12: continue
    if left.count("(") != left.count(")") or left.count("[") != left.count("]"): continue
    # vital/test label
    if is_test_hint or is_vital or is_imaging:
     out.append(Entity(text=raw_text[ls:le],start=ls,end=le,type="TÊN_XÉT_NGHIỆM",confidence=.98,source="structured_test_label",section=sec.canonical,metadata={"structured":True,"trusted":True}))
     # Result is conservative: short value, numeric/unit or known result phrase.
     value=raw_text[rs:re_]
     norm=re.sub(r"\s+"," ",value.casefold()).strip()
     if len(value)<=90 and (RESULT_WORDS.match(norm) or re.match(r"^\d",norm)):
      out.append(Entity(text=value,start=rs,end=re_,type="KẾT_QUẢ_XÉT_NGHIỆM",confidence=.97,source="structured_test_result",section=sec.canonical,metadata={"structured":True,"trusted":True,"test_text":left}))
  return out
