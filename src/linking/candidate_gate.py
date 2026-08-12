from __future__ import annotations

import re

from src.common.schema import Entity


ICD_CODE_RE = re.compile(r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b")
DIAGNOSIS_LINE_RE = re.compile(r"(?i)chẩn\s*đoán\s*:")
TREATMENT_LINE_RE = re.compile(r"(?i)(?:điều\s*trị|xử\s*trí|thuốc)\s*:")

# These methods are deterministic exact/dictionary matches, not fuzzy retrieval.
TRUSTED_EXACT_METHODS = {
    "icd_explicit_regex",
    "icd_dictionary_exact",
    "icd_alias_exact",
    "icd_canonical_exact",
    "icd_knowledge_exact",
    "rxnorm_seed_exact",
    "rxnorm_exact",
    "rxnorm_exact_normalized",
    "rxnorm_knowledge_exact",
    "rxnorm_clinical_strength",
}
FUZZY_METHODS = {
    "icd_bm25_calibrated",
    "icd_embedding_calibrated",
    "rxnorm_embedding_calibrated",
}


class ContextualCandidateGate:
    """Generic candidate gate with no Part-2-derived priors.

    Modes:
    - off: never changes candidates.
    - balanced (default): keeps explicit/exact/dictionary/clinical-strength
      matches; fuzzy candidates are kept only in structured diagnosis/treatment
      lines and otherwise cleared.
    - strict: keeps only explicit ICD codes, full RxNorm clinical-strength
      matches, or candidates on structured diagnosis/treatment lines.
    """

    def __init__(self, mode: str = "balanced"):
        if mode not in {"off", "balanced", "strict"}:
            raise ValueError(f"Unsupported candidate gate mode: {mode}")
        self.mode = mode

    @staticmethod
    def _line(raw_text: str, entity: Entity) -> str:
        start = raw_text.rfind("\n", 0, entity.start) + 1
        end = raw_text.find("\n", entity.end)
        if end < 0:
            end = len(raw_text)
        return raw_text[start:end]

    def apply(self, raw_text: str, entity: Entity) -> Entity:
        if entity.type not in {"CHẨN_ĐOÁN", "THUỐC"}:
            return entity

        original = list(entity.candidates)
        if self.mode == "off":
            entity.metadata["candidate_gate"] = {
                "mode": self.mode,
                "decision": "gate_off",
                "before": original,
                "after": list(entity.candidates),
            }
            return entity

        method = str(entity.metadata.get("link_method") or "")
        line = self._line(raw_text, entity)
        explicit_icd = (
            entity.type == "CHẨN_ĐOÁN"
            and bool(ICD_CODE_RE.search(entity.text.upper()))
        )
        structured_line = (
            entity.type == "CHẨN_ĐOÁN" and bool(DIAGNOSIS_LINE_RE.search(line))
        ) or (
            entity.type == "THUỐC" and bool(TREATMENT_LINE_RE.search(line))
        )
        clinical_strength = method == "rxnorm_clinical_strength"
        trusted_exact = method in TRUSTED_EXACT_METHODS

        if not entity.candidates:
            decision = "keep_empty"
        elif explicit_icd:
            decision = "keep_explicit_code"
        elif clinical_strength:
            decision = "keep_clinical_strength"
        elif structured_line:
            decision = "keep_structured_line"
        elif self.mode == "balanced" and trusted_exact:
            decision = "keep_trusted_exact"
        else:
            # The gate mainly targets fuzzy BM25/embedding guesses outside a
            # structured diagnosis/treatment context.
            entity.candidates = []
            decision = "clear_fuzzy" if method in FUZZY_METHODS else "clear_untrusted"

        entity.metadata["candidate_gate"] = {
            "mode": self.mode,
            "decision": decision,
            "link_method": method,
            "before": original,
            "after": list(entity.candidates),
        }
        return entity
