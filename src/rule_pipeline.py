from __future__ import annotations
from pathlib import Path
from src.common.schema import Entity
from src.extraction.diagnosis_extractor import DiagnosisExtractor
from src.extraction.contextual_clinical_extractor import ContextualClinicalExtractor
from src.extraction.drug_extractor import DrugExtractor
from src.extraction.lab_extractor import LabExtractor
from src.extraction.structured_extractor import StructuredClinicalExtractor
from src.extraction.symptom_extractor import SymptomExtractor
from src.postprocessing.merge import merge_entities
from src.section_parser import SectionParser, SectionSpan

class RulePipeline:
    def __init__(self, section_aliases_path:Path, test_aliases_path:Path,
                 drug_terms_path:Path, symptom_aliases_path:Path,
                 diagnosis_aliases_path:Path, enable_structured:bool=True, enable_contextual:bool=True):
        self.section_parser=SectionParser(section_aliases_path)
        self.lab_extractor=LabExtractor(test_aliases_path)
        self.drug_extractor=DrugExtractor(drug_terms_path)
        self.symptom_extractor=SymptomExtractor(symptom_aliases_path)
        self.diagnosis_extractor=DiagnosisExtractor(diagnosis_aliases_path)
        self.structured_extractor=StructuredClinicalExtractor() if enable_structured else None
        self.contextual_extractor=ContextualClinicalExtractor() if enable_contextual else None
    def parse_sections(self,raw_text:str)->list[SectionSpan]: return self.section_parser.parse(raw_text)
    def extract(self,raw_text:str)->tuple[list[SectionSpan],list[Entity]]:
        sections=self.parse_sections(raw_text); entities=[]
        if self.structured_extractor is not None:
            entities.extend(self.structured_extractor.extract(raw_text,sections))
        if self.contextual_extractor is not None:
            entities.extend(self.contextual_extractor.extract(raw_text,sections))
        entities.extend(self.lab_extractor.extract(raw_text,sections))
        entities.extend(self.drug_extractor.extract(raw_text,sections))
        entities.extend(self.symptom_extractor.extract(raw_text,sections))
        entities.extend(self.diagnosis_extractor.extract(raw_text,sections))
        merged=merge_entities(entities)
        for e in merged: e.validate(raw_text)
        return sections,merged
