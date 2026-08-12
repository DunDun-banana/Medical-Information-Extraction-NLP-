from __future__ import annotations

import re
import unicodedata

from src.common.schema import Entity
from src.section_parser import SectionSpan

FIELD_LABELS={"vị trí","tính chất","lan","thời gian","mức độ","yếu tố tăng","yếu tố giảm"}
RISK_TERMS={"tuổi cao","nam giới","nữ giới","hút thuốc lá","stress kéo dài","béo phì","ít vận động"}
HEADING_TERMS={"chẩn đoán hình ảnh","kết quả chẩn đoán hình ảnh","kết quả xét nghiệm","triệu chứng hiện tại","đánh giá tại bệnh viện","khám lâm sàng"}
FRAGMENTS={
    "định","cấp","chặt","t2","t1","vị trí","tính chất","lan","thời gian",
    "bị","thể","tử","dễ","nhiều","hại","răng","biểu hiện","biến chứng",
    "đột biến","tại chỗ","quan sát thấy","thể hệ 1","men","6pd","huyết","da",
}
GENERIC_CLINICAL={
    "bệnh","triệu chứng","dấu hiệu","tình trạng","biểu hiện","chẩn đoán","kết quả",
    "thuốc","xét nghiệm","bẩm sinh","nguyên nhân","nguy cơ","cơ chế","bệnh lý",
}
ONE_TOKEN_SYMPTOM_ALLOW={
    "ho","sốt","đau","nôn","ngất","phù","yếu","mệt","chóng mặt","tiêu chảy",
    "táo bón","co giật","khó thở","vàng da","vàng mắt",
}
MODALITY=re.compile(r"(?ix)^(?:chụp\s+)?(?:ct|mri|x-?quang|siêu\s+âm|hida|hidi|ercp|mrcp|ecg|điện\s+tâm\s+đồ)$")
ACTION=re.compile(r"(?ix)^(?:cần|nên|không nên|khuyến cáo|theo dõi|điều trị|chỉ định|thực hiện|tránh|phòng ngừa)\b")
CONJ=re.compile(r"(?ix)\b(?:nhưng|mặc dù|tuy nhiên|sau đó|đồng thời)\b")
PROCEDURE=re.compile(r"(?ix)\b(?:phẫu\s+thuật|thủ\s+thuật|đặt\s+ống|ghép\s+mô|bào\s+láng|cắt\s+bỏ|can\s+thiệp|tái\s+tạo\s+mô|ổn\s+định\s+chân\s+răng)\b")
MECHANISM=re.compile(r"(?ix)\b(?:giảm\s+sản\s+xuất|giải\s+phóng\s+các\s+chất|sừng\s+hóa|phá\s+hủy\s+các\s+trung\s+khu|đột\s+biến\s+gen|cơ\s+chế\s+bệnh\s+sinh)\b")
PROTECTED_BIOCHEMICAL=re.compile(r"(?ix)\bglucose[-\s]*6[-\s]*phosphate\s+dehydrogenase\b")
LAB_FINDING=re.compile(r"(?ix)^hồng\s+cầu\s+bị\s+phá\s+hủy(?:\s+hàng\s+loạt)?$")
NEURAL_PREFIXES=("gliner","vihealthbert","self_host_llm")
TRUSTED_PREFIXES=("diagnosis_dictionary","symptom_dictionary","test_dictionary","structured_","contextual_")


def norm(s):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",s).casefold()).strip(" :;,.\t\n")


def in_header(e,sections):
    return any(s.header_start<=e.start and e.end<=s.header_end for s in sections)


def exact_support(e,rows):
    return any(r.type==e.type and r.start==e.start and r.end==e.end and r is not e for r in rows)


def overlap(a:Entity,b:Entity)->float:
    inter=max(0,min(a.end,b.end)-max(a.start,b.start))
    return inter/max(1,min(a.end-a.start,b.end-b.start))


def trusted_support(e,rows):
    return any(
        r is not e and r.type==e.type and overlap(e,r)>=.8
        and (r.source.startswith(TRUSTED_PREFIXES) or bool(r.metadata.get("trusted")))
        for r in rows
    )


def inside_protected_biochemical(raw_text:str,e:Entity)->bool:
    if e.type not in {"TÊN_XÉT_NGHIỆM","KẾT_QUẢ_XÉT_NGHIỆM"}:
        return False
    left=max(0,e.start-40); right=min(len(raw_text),e.end+80)
    for m in PROTECTED_BIOCHEMICAL.finditer(raw_text[left:right]):
        a=left+m.start(); b=left+m.end()
        if a<=e.start and e.end<=b:
            return True
    return False


def in_explicit_test_span(e:Entity,rows:list[Entity])->bool:
    return any(
        r.source=="contextual_test_cue_v5" and r.start<=e.start and e.end<=r.end
        for r in rows if r is not e
    )


def in_explicit_result_span(e:Entity,rows:list[Entity])->bool:
    return any(
        r.source in {"contextual_result_cue_v5","contextual_lab_finding_v5"}
        and r.start<=e.start and e.end<=r.end
        for r in rows if r is not e
    )


def test_shows_context(raw_text:str,e:Entity)->bool:
    left=max(0,e.start-140)
    context=raw_text[left:e.start]
    # Keep scope inside the same sentence/line.
    context=re.split(r"[.\n]",context)[-1]
    return bool(re.search(r"(?ix)xét\s+nghiệm\s+máu\s+(?:thường\s+)?(?:cho\s+thấy|ghi\s+nhận|phát\s+hiện|là)[^.;\n]*$",context))


def nonclinical_lexical_context(raw_text:str,e:Entity)->str|None:
    n=norm(e.text)
    left=raw_text[max(0,e.start-18):e.start].casefold()
    right=raw_text[e.end:min(len(raw_text),e.end+18)].casefold()
    if n=="yếu" and re.match(r"\s*tố\b",right):
        return "weakness_inside_risk_factor_phrase"
    if n=="đau" and re.search(r"giảm\s*$",left):
        return "pain_inside_analgesic_phrase"
    if n=="sốt" and (re.search(r"(?:hạ|kháng)\s*$",left) or re.match(r"\s*rét\b",right)):
        return "fever_inside_drug_or_malaria_phrase"
    if n=="phù" and re.match(r"\s*hợp\b",right):
        return "edema_inside_suitable_phrase"
    return None


class SemanticAdmissionGate:
    def __init__(self,config:dict|None=None):
        self.config=config or {}

    def filter(self,raw_text,entities,sections,accepted_context=None):
        accepted_context=accepted_context or []
        out=[]; report=[]
        all_rows=accepted_context+entities

        for e in entities:
            n=norm(e.text); reason=None; repaired=False
            if e.source=="baseline_anchor":
                out.append(e)
                continue

            is_neural=e.source.startswith(NEURAL_PREFIXES)
            has_trusted=trusted_support(e,all_rows)
            has_exact=exact_support(e,all_rows)

            lexical_reason=nonclinical_lexical_context(raw_text,e)
            if lexical_reason:
                reason=lexical_reason
            elif in_header(e,sections) or n in HEADING_TERMS:
                reason="heading_or_task_label"
            elif inside_protected_biochemical(raw_text,e):
                reason="component_of_protected_biochemical_name"
            elif in_explicit_test_span(e,all_rows) and e.type=="CHẨN_ĐOÁN":
                reason="diagnosis_nested_inside_explicit_test"
            elif in_explicit_result_span(e,all_rows) and e.type=="CHẨN_ĐOÁN":
                reason="diagnosis_nested_inside_explicit_result"
            elif n in FIELD_LABELS and e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"}:
                reason="field_label"
            elif e.section in {"FAQ_RISK_FACTORS","RISK_FACTORS"} and (n in RISK_TERMS or e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"}) and not has_trusted:
                reason="risk_factor_not_target_entity"
            elif MODALITY.match(n) and e.type!="TÊN_XÉT_NGHIỆM":
                e.type="TÊN_XÉT_NGHIỆM"; e.source=e.source+"_type_repaired"; e.metadata["semantic_repair"]="imaging_modality_to_test"; repaired=True
            elif LAB_FINDING.fullmatch(n) and e.type=="TRIỆU_CHỨNG" and test_shows_context(raw_text,e):
                e.type="KẾT_QUẢ_XÉT_NGHIỆM"; e.source=e.source+"_type_repaired"; e.metadata["semantic_repair"]="lab_finding_to_result"; e.assertions=[]; repaired=True
            elif n in FRAGMENTS and not has_trusted:
                reason="known_boundary_fragment"
            elif n in GENERIC_CLINICAL and not has_trusted:
                reason="generic_clinical_term"
            elif e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"} and (len(e.text.split())>14 or CONJ.search(e.text)):
                reason="long_clause_like_span"
            elif e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"} and ACTION.match(n):
                reason="action_or_advice"
            elif e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"} and PROCEDURE.search(n):
                reason="procedure_not_target_entity"
            elif e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"} and MECHANISM.search(n):
                reason="mechanism_not_target_entity"
            elif e.source=="structured_symptom_attribute" and not has_trusted:
                reason="symptom_attribute_needs_model_support"
            elif e.type=="TÊN_XÉT_NGHIỆM" and n.startswith("thuốc "):
                reason="drug_not_test"
            elif e.type=="CHẨN_ĐOÁN" and n in {"chẩn đoán hình ảnh","xét nghiệm","kết quả"}:
                reason="generic_task_not_diagnosis"
            elif e.type=="KẾT_QUẢ_XÉT_NGHIỆM" and re.match(r"(?ix)^không\s+được\s+phát\s+hiện\b",n):
                reason="missed_detection_not_test_result"
            elif is_neural and e.type=="TRIỆU_CHỨNG" and len(n.split())==1 and n not in ONE_TOKEN_SYMPTOM_ALLOW and not has_trusted:
                reason="unsupported_one_token_symptom"
            elif is_neural and e.type in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN"} and len(n)<4 and not has_trusted:
                reason="unsupported_short_neural_span"
            elif is_neural and not (has_trusted or has_exact or e.metadata.get("corroborated")):
                min_conf=float(self.config.get("uncorroborated_neural_min_confidence",.88))
                if float(e.confidence)<min_conf:
                    reason="uncorroborated_neural_below_gate"

            if reason is None:
                out.append(e)
            report.append({"accepted":reason is None,"reason":reason or ("type_repaired" if repaired else "accepted"),"entity":e.to_debug_dict()})
        return out,report
