from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from src.assertion_rules import infer_assertions
from src.common.schema import Entity

OFFICIAL=["isNegated","isHistorical","isFamily"]
REPORTER=re.compile(r"(?i)(?:người\s+nhà|mẹ|bố|cha|vợ|chồng)\s+(?:cho biết|kể|nhận thấy|báo|mô tả).*?(?:bệnh\s+nhân|người\s+bệnh|trẻ)")
UNCERTAIN=re.compile(r"(?i)\b(?:nghi|nghi\s+ngờ|có thể|khả năng|chưa loại trừ|theo dõi)\b")
CONDITIONAL=re.compile(r"(?i)(?:^|[.;\n]\s*)(?:nếu|trong\s+trường\s+hợp|khi)\b")
FAMILY_POSSESSIVE=re.compile(r"(?i)\b(?:mẹ|bố|cha|anh|chị|em|ông|bà|vợ|chồng)\s+(?:bệnh\s+nhân|người\s+bệnh|trẻ)?\s*(?:bị|mắc|có|được\s+chẩn\s+đoán)\b")
PATIENT_CHILD=re.compile(r"(?i)\b(?:con|trẻ|bé|em\s+bé)\s+(?:bị|mắc|có|được\s+chẩn\s+đoán)\b")


def scope(raw,e,w=140):
    return f"SECTION={e.section} LEFT={raw[max(0,e.start-w):e.start]} ENTITY={e.text} RIGHT={raw[e.end:min(len(raw),e.end+w)]}"


def _clause(raw:str,start:int,end:int,window:int=180)->str:
    left=max(0,start-window); right=min(len(raw),end+window)
    text=raw[left:right]
    local_start=start-left; local_end=end-left
    cut_left=max(text.rfind('.',0,local_start),text.rfind('\n',0,local_start),text.rfind(';',0,local_start))
    cuts=[x for x in (text.find('.',local_end),text.find('\n',local_end),text.find(';',local_end)) if x>=0]
    cut_right=min(cuts) if cuts else len(text)
    return text[cut_left+1:cut_right]


@dataclass
class AssertionScopeConfig:
    model_path:Path|None=None
    preserve_baseline:bool=True
    threshold:float=.5
    rules_union:bool=True


class AssertionScopeClassifier:
    def __init__(self,config:AssertionScopeConfig):
        self.config=config
        self.artifact=None
        self.load_error=None
        if config.model_path and config.model_path.exists():
            try:
                import joblib
                self.artifact=joblib.load(config.model_path)
            except Exception as exc:
                self.load_error=f"{type(exc).__name__}: {exc}"
                self.artifact=None

    def apply_many(self,raw,entities):
        eligible=[]
        for e in entities:
            if (e.source=="baseline_anchor" and self.config.preserve_baseline
                    and not e.metadata.get("requires_assertion_recheck")):
                continue
            if e.type not in {"TRIỆU_CHỨNG","CHẨN_ĐOÁN","THUỐC"}:
                e.assertions=[]
                continue
            eligible.append((e,scope(raw,e)))

        probs=[{} for _ in eligible]
        inference_error=None
        if eligible and self.artifact:
            try:
                X=self.artifact["vectorizer"].transform([x for _,x in eligible])
                vals=self.artifact["model"].predict_proba(X)
                labels=self.artifact["labels"]
                probs=[{label:float(value) for label,value in zip(labels,row)} for row in vals]
            except Exception as exc:
                inference_error=f"{type(exc).__name__}: {exc}"
                self.artifact=None
                probs=[{} for _ in eligible]

        for (e,text),p in zip(eligible,probs):
            clause=_clause(raw,e.start,e.end)
            rules=infer_assertions(raw,e.start,e.end,e.type,e.section)
            pred=[label for label in OFFICIAL if p.get(label,0)>=self.config.threshold]
            internal=[]

            is_reporter=bool(REPORTER.search(text))
            is_uncertain=bool(UNCERTAIN.search(clause))
            is_conditional=bool(CONDITIONAL.search(clause.strip()))
            if is_reporter:
                internal.append("isReporter")
            if is_uncertain:
                internal.append("isUncertain")
            if is_conditional:
                internal.append("isConditional")

            if self.config.rules_union:
                for label in rules:
                    # Conditional educational statements are not factual patient
                    # assertions. Keep the internal state but do not force an
                    # official negation/history label from a lexical rule alone.
                    if is_conditional and label in {"isNegated","isHistorical"}:
                        continue
                    if label=="isFamily" and not FAMILY_POSSESSIVE.search(clause):
                        continue
                    if label not in pred:
                        pred.append(label)

            if is_reporter or PATIENT_CHILD.search(clause):
                pred=[value for value in pred if value!="isFamily"]
                if PATIENT_CHILD.search(clause):
                    internal.append("isPatientChild")
            if is_conditional and p.get("isNegated",0.0)<max(.75,self.config.threshold):
                pred=[value for value in pred if value!="isNegated"]

            e.assertions=pred
            metadata={
                "probabilities":p,
                "rule_assertions":rules,
                "internal_states":internal,
                "final_assertions":pred,
                "clause":clause,
            }
            if inference_error or self.load_error:
                metadata["model_error"]=inference_error or self.load_error
                metadata["fallback"]="rules_selective"
            e.metadata["assertion_scope"]=metadata
        return entities
