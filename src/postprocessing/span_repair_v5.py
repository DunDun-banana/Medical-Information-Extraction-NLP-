from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from src.common.schema import Entity

WORD_RE = re.compile(r"[0-9A-Za-zÀ-ỹ]")
PROTECTED_BIOCHEMICAL_RE = re.compile(
    r"(?ix)\bglucose[-\s]*6[-\s]*phosphate\s+dehydrogenase\b"
)
TRUSTED_PREFIXES = (
    "diagnosis_dictionary",
    "symptom_dictionary",
    "test_dictionary",
    "structured_",
    "contextual_",
)
SHORT_BASELINE_ALLOW={"ho","đỏ","mủ"}
GENERIC_BASELINE_FRAGMENTS = {
    "men", "6pd", "da", "huyết", "não", "máu", "vàng", "răng",
    "bẩm sinh", "kết", "tích", "cao", "hủy", "thể", "bị",
}


def _overlap(a: Entity, b: Entity) -> bool:
    return max(a.start, b.start) < min(a.end, b.end)


def _contains(outer: Entity, inner: Entity) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def _is_mid_word(raw: str, entity: Entity) -> bool:
    if entity.start > 0 and WORD_RE.match(raw[entity.start - 1]) and WORD_RE.match(raw[entity.start]):
        return True
    if entity.end < len(raw) and WORD_RE.match(raw[entity.end - 1]) and WORD_RE.match(raw[entity.end]):
        return True
    return False


def _inside_protected_biochemical(raw: str, entity: Entity) -> bool:
    if entity.type not in {"TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"}:
        return False
    left = max(0, entity.start - 40)
    right = min(len(raw), entity.end + 80)
    for match in PROTECTED_BIOCHEMICAL_RE.finditer(raw[left:right]):
        start = left + match.start()
        end = left + match.end()
        if start <= entity.start and entity.end <= end:
            return True
    return False


def _baseline_context_rejection(raw: str, entity: Entity) -> str | None:
    value = entity.text.strip().casefold()
    left = raw[max(0, entity.start - 18):entity.start].casefold()
    right = raw[entity.end:min(len(raw), entity.end + 18)].casefold()
    if value == "yếu" and re.match(r"\s*tố\b", right):
        return "weakness_inside_risk_factor_phrase"
    if value == "đau" and re.search(r"giảm\s*$", left):
        return "pain_inside_analgesic_phrase"
    if value == "sốt" and (re.search(r"(?:hạ|kháng)\s*$", left) or re.match(r"\s*rét\b", right)):
        return "fever_inside_drug_or_malaria_phrase"
    if value == "phù" and re.match(r"\s*hợp\b", right):
        return "edema_inside_suitable_phrase"
    if entity.type == "CHẨN_ĐOÁN" and value in {"bẩm sinh", "huyết", "da", "men"}:
        return "generic_baseline_diagnosis_fragment"
    return None


def _trusted(entity: Entity) -> bool:
    return entity.source.startswith(TRUSTED_PREFIXES) or bool(entity.metadata.get("trusted"))


def _anchor_from_candidate(candidate: Entity, anchors: list[Entity], reason: str) -> Entity:
    repaired = copy.deepcopy(candidate)
    repaired.source = "baseline_anchor"
    repaired.confidence = max([candidate.confidence] + [row.confidence for row in anchors])
    inherited_assertions = list(dict.fromkeys(value for row in anchors for value in row.assertions))
    repaired.assertions = []
    repaired.candidates = list(dict.fromkeys(value for row in anchors for value in row.candidates)) or list(candidate.candidates)
    repaired.metadata = copy.deepcopy(candidate.metadata)
    repaired.metadata.update({
        "baseline_anchor": True,
        "baseline_anchor_repaired": True,
        "span_repair_reason": reason,
        "replaced_baseline_spans": [
            {"text": row.text, "position": [row.start, row.end], "type": row.type}
            for row in anchors
        ],
        "supporting_sources": list(dict.fromkeys(
            [candidate.source] + [row.source for row in anchors]
        )),
        "inherited_baseline_assertions": inherited_assertions,
        "requires_assertion_recheck": True,
    })
    return repaired


@dataclass
class SpanRepairConfig:
    enabled: bool = True
    remove_midword_anchors: bool = True
    repair_exact_type: bool = True
    repair_cross_type_exact: bool = True
    contextual_test_overrides_diagnosis: bool = True


class SpanBoundaryRepairV5:
    """Conservative pre-merge repair for fragmented baseline anchors.

    The repair never invents text. It can only replace baseline fragments with a
    trusted exact span already proposed by a dictionary or structured rule.
    """

    def __init__(self, config: SpanRepairConfig | None = None):
        self.config = config or SpanRepairConfig()

    def apply(
        self,
        raw_text: str,
        baseline_entities: list[Entity],
        additions: list[Entity],
        repair_candidates: list[Entity] | None = None,
    ) -> tuple[list[Entity], list[Entity], list[dict]]:
        if not self.config.enabled:
            return baseline_entities, additions, []

        report: list[dict] = []
        anchors: list[Entity] = []

        # Remove only mechanically impossible or protected biochemical anchors.
        for entity in baseline_entities:
            reason = None
            context_reason = _baseline_context_rejection(raw_text, entity)
            if context_reason:
                reason = context_reason
            elif _inside_protected_biochemical(raw_text, entity):
                reason = "component_of_protected_biochemical_name"
            elif self.config.remove_midword_anchors and _is_mid_word(raw_text, entity):
                reason = "baseline_midword_fragment"
            elif (
                entity.type in {"TRIỆU_CHỨNG", "CHẨN_ĐOÁN", "THUỐC"}
                and len(entity.text.strip()) <= 2
                and entity.text.strip().isalpha()
                and entity.text.strip().casefold() not in SHORT_BASELINE_ALLOW
            ):
                reason = "baseline_too_short_alpha_fragment"
            if reason:
                report.append({"action": "drop", "reason": reason, "entity": entity.to_debug_dict()})
            else:
                anchors.append(entity)

        repair_pool = repair_candidates if repair_candidates is not None else additions
        trusted = [row for row in repair_pool if _trusted(row)]
        consumed_anchor_ids: set[int] = set()
        repaired: list[Entity] = []

        # Exact-span trusted rules can correct a baseline type error, e.g.
        # ``bại não`` symptom -> diagnosis.
        if self.config.repair_cross_type_exact:
            for candidate in trusted:
                exact = [
                    row for row in anchors
                    if row.start == candidate.start and row.end == candidate.end and row.type != candidate.type
                ]
                if not exact:
                    continue
                # Contextual and dictionary rules are the only allowed type overrides.
                if not candidate.source.startswith(("diagnosis_dictionary", "symptom_dictionary", "contextual_", "test_dictionary")):
                    continue
                for row in exact:
                    consumed_anchor_ids.add(id(row))
                repaired_entity = _anchor_from_candidate(candidate, exact, "trusted_exact_cross_type")
                repaired.append(repaired_entity)
                report.append({
                    "action": "replace",
                    "reason": "trusted_exact_cross_type",
                    "entity": repaired_entity.to_debug_dict(),
                })

        # Replace one or several fragmented anchors with a longer trusted alias.
        if self.config.repair_exact_type:
            for candidate in sorted(trusted, key=lambda row: (row.end - row.start), reverse=True):
                if len(candidate.text) > 120 or "\n" in candidate.text:
                    continue
                overlapping = []
                for row in anchors:
                    if id(row) in consumed_anchor_ids or row.type != candidate.type:
                        continue
                    contained = _contains(candidate, row)
                    # Permit a trusted alias to replace the same mention with
                    # trailing punctuation or a small (<=3 token) boundary
                    # extension, e.g. ``máu tan huyết.`` ->
                    # ``thiếu máu tan huyết``.
                    punctuation_compatible = (
                        candidate.start <= row.start
                        and candidate.end >= row.end - 1
                        and raw_text[max(candidate.end, row.end - 1):row.end] in {"", ".", ",", ";", ":"}
                    )
                    if not (contained or punctuation_compatible):
                        continue
                    if candidate.start == row.start and candidate.end == row.end:
                        continue
                    extension_text = (
                        raw_text[candidate.start:row.start] + " "
                        + raw_text[min(candidate.end, row.end):max(candidate.end, row.end)]
                    )
                    extension_tokens = re.findall(r"[\wÀ-ỹ]+", extension_text)
                    if len(extension_tokens) <= 3:
                        overlapping.append(row)
                if not overlapping:
                    continue
                for row in overlapping:
                    consumed_anchor_ids.add(id(row))
                repaired_entity = _anchor_from_candidate(candidate, overlapping, "trusted_alias_boundary_extension")
                repaired.append(repaired_entity)
                report.append({
                    "action": "replace",
                    "reason": "trusted_alias_boundary_extension",
                    "entity": repaired_entity.to_debug_dict(),
                })

        # Trusted dictionary spans may replace a generic fragment of another
        # type, e.g. baseline symptom ``não`` inside diagnosis ``bại não``.
        if self.config.repair_cross_type_exact:
            for candidate in sorted(trusted, key=lambda row: (row.end-row.start), reverse=True):
                nested = [
                    row for row in anchors
                    if id(row) not in consumed_anchor_ids
                    and row.type != candidate.type
                    and _contains(candidate, row)
                    and row.text.strip().casefold() in GENERIC_BASELINE_FRAGMENTS
                ]
                if not nested or not candidate.source.startswith(("diagnosis_dictionary","symptom_dictionary","contextual_")):
                    continue
                for row in nested:
                    consumed_anchor_ids.add(id(row))
                repaired_entity = _anchor_from_candidate(candidate, nested, "trusted_contained_cross_type")
                repaired.append(repaired_entity)
                report.append({
                    "action":"replace",
                    "reason":"trusted_contained_cross_type",
                    "entity":repaired_entity.to_debug_dict(),
                })

        # An explicit test phrase overrides diagnosis fragments nested inside it.
        if self.config.contextual_test_overrides_diagnosis:
            tests = [
                row for row in trusted
                if row.source in {"contextual_test_cue_v5", "contextual_result_cue_v5"}
            ]
            for test in tests:
                nested = [
                    row for row in anchors
                    if id(row) not in consumed_anchor_ids
                    and row.type == "CHẨN_ĐOÁN"
                    and _contains(test, row)
                ]
                if not nested:
                    continue
                for row in nested:
                    consumed_anchor_ids.add(id(row))
                    report.append({
                        "action": "drop",
                        "reason": "diagnosis_fragment_inside_explicit_test_or_result",
                        "entity": row.to_debug_dict(),
                        "replacement": test.to_debug_dict(),
                    })

        final_anchors = [row for row in anchors if id(row) not in consumed_anchor_ids] + repaired
        final_anchors.sort(key=lambda row: (row.start, row.end, row.type))
        return final_anchors, additions, report
