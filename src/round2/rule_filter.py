from __future__ import annotations

from src.common.schema import Entity


def requires_independent_support(entity: Entity) -> bool:
    return (
        entity.source.endswith("_fallback")
        or bool(entity.metadata.get("requires_independent_support"))
    )


def is_structural_rule(entity: Entity) -> bool:
    return entity.source.endswith("_fallback")


def _exact_support(entity: Entity, rows: list[Entity]) -> Entity | None:
    for row in rows:
        if row.type != entity.type or row.start != entity.start:
            continue
        if row.end == entity.end:
            return row
        # Permit only a terminal-punctuation difference. This allows the rule
        # extractor to emit the cleaner clinical span while keeping the
        # independent-support requirement effectively exact.
        longer, shorter = (row, entity) if row.end > entity.end else (entity, row)
        if longer.end - shorter.end <= 2:
            tail = longer.text[len(shorter.text):]
            if longer.text.startswith(shorter.text) and tail and all(
                char.isspace() or char in ".;:," for char in tail
            ):
                return row
    return None


def filter_rule_entities(
    rule_entities: list[Entity],
    baseline_entities: list[Entity] | None = None,
    ner_raw_entities: list[Entity] | None = None,
    thresholds: dict[str, float] | None = None,
    mode: str = "section_consensus",
) -> tuple[list[Entity], list[dict]]:
    """Select rule entities that may enter the final ensemble.

    Exact dictionary/test rules remain trusted. Structural section fallbacks are
    useful proposals, but should not independently alter an anchor submission.
    In ``section_consensus`` mode they enter only when the baseline or an
    independent high-confidence ViHealthBERT prediction has the same span/type.
    Qwen raw output is intentionally not used here because Qwen receives rule
    proposals and can echo them, so that would not be independent evidence.
    """
    baseline_entities = baseline_entities or []
    ner_raw_entities = ner_raw_entities or []
    thresholds = thresholds or {}
    if mode not in {"all", "exact_only", "section_consensus"}:
        raise ValueError(f"Unsupported rule addition mode: {mode}")

    accepted: list[Entity] = []
    report: list[dict] = []
    for entity in rule_entities:
        needs_support = requires_independent_support(entity)
        structural = is_structural_rule(entity)
        reason = "trusted_exact_rule"
        ok = True
        support = None

        if needs_support and mode == "exact_only":
            ok, reason = False, (
                "structural_rule_disabled" if structural else "context_rule_disabled"
            )
        elif needs_support and mode == "section_consensus":
            support = _exact_support(entity, baseline_entities)
            if support is not None:
                ok, reason = True, "structural_exact_baseline_support"
            else:
                support = _exact_support(entity, ner_raw_entities)
                threshold = float(thresholds.get(entity.type, 0.95))
                if support is not None and float(support.confidence) >= threshold:
                    ok, reason = True, "structural_exact_vihealth_support"
                else:
                    ok, reason = False, (
                        "structural_rule_requires_independent_support"
                        if structural
                        else "context_rule_requires_independent_support"
                    )

        if ok:
            if support is not None:
                entity.metadata["structural_support_source"] = support.source
                entity.metadata["structural_support_confidence"] = support.confidence
            accepted.append(entity)
        report.append({
            "accepted": ok,
            "reason": reason,
            "entity": entity.to_debug_dict(),
        })
    return accepted, report
