from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.common.schema import Entity


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFC", text).casefold().strip()
    return re.sub(r"\s+", " ", value).strip(" .,:;()[]{}")


NEURAL_PREFIXES = ("gliner", "self_host_llm", "vihealthbert", "phobert")
GENERIC_BY_TYPE = {
    "TÊN_XÉT_NGHIỆM": {"dấu hiệu sinh tồn"},
    "KẾT_QUẢ_XÉT_NGHIỆM": {"kết quả khám"},
}
DROP_TYPE_EXACT = {
    "THUỐC": {"xông khí dung", "trichophyton rubrum"},
    "CHẨN_ĐOÁN": {"lympho t", "đại thực bào"},
}
TYPE_REPAIRS = {
    ("sỏi mật", "TRIỆU_CHỨNG"): "CHẨN_ĐOÁN",
    ("mất thị lực", "CHẨN_ĐOÁN"): "TRIỆU_CHỨNG",
    ("mù vĩnh viễn", "CHẨN_ĐOÁN"): "TRIỆU_CHỨNG",
    ("rậm lông", "CHẨN_ĐOÁN"): "TRIỆU_CHỨNG",
    ("cơn co giật", "CHẨN_ĐOÁN"): "TRIỆU_CHỨNG",
}


@dataclass(frozen=True)
class PrecisionCleanupV53Config:
    enabled: bool = True
    repair_types: bool = True
    drop_generic_labels: bool = True
    drop_category_conflicts: bool = True


class PrecisionCleanupV53:
    """Narrow source-aware cleanup learned from V5.2 verifier-off errors.

    Only neural additions are touched. Baseline/rule/structured entities are
    preserved exactly, avoiding the broad admission changes that hurt V6.
    """

    def __init__(self, config: PrecisionCleanupV53Config):
        self.config = config

    def apply(self, entities: list[Entity]) -> tuple[list[Entity], list[dict]]:
        if not self.config.enabled:
            return entities, []
        kept: list[Entity] = []
        report: list[dict] = []
        for entity in entities:
            neural = entity.source.startswith(NEURAL_PREFIXES)
            value = _norm(entity.text)
            action = "keep"
            reason = "unchanged"
            before_type = entity.type

            if neural and self.config.drop_generic_labels and value in GENERIC_BY_TYPE.get(entity.type, set()):
                action, reason = "drop", "generic_field_or_section_label"
            elif neural and self.config.drop_category_conflicts and value in DROP_TYPE_EXACT.get(entity.type, set()):
                action, reason = "drop", "known_category_type_conflict"
            elif neural and self.config.repair_types:
                repaired = TYPE_REPAIRS.get((value, entity.type))
                if repaired:
                    entity.type = repaired
                    entity.candidates = []
                    entity.metadata["v53_original_type"] = before_type
                    entity.metadata["v53_type_repaired"] = True
                    action, reason = "repair", "trusted_exact_type_repair"

            report.append({
                "action": action,
                "reason": reason,
                "before_type": before_type,
                "entity": entity.to_debug_dict(),
            })
            if action != "drop":
                kept.append(entity)
        return kept, report
