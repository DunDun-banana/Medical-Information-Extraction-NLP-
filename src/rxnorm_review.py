from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def load_rxnorm_review_lexicon(
    rrf_path: Path,
    allowed_tty: set[str] | None = None,
) -> dict[str, list[str]]:
    """Load ingredient/brand terms for corpus review, not final linking."""
    if not rrf_path.exists():
        return {}
    allowed_tty = allowed_tty or {"IN", "PIN", "BN"}
    terms: dict[str, set[str]] = defaultdict(set)
    with rrf_path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        reader = csv.reader(stream, delimiter="|")
        for fields in reader:
            if len(fields) < 17:
                continue
            rxcui, language, tty, term, suppress = (
                fields[0], fields[1], fields[12], fields[14], fields[16]
            )
            if language != "ENG" or tty not in allowed_tty or suppress != "N":
                continue
            normalized = normalize_term(term)
            if not (4 <= len(normalized) <= 60):
                continue
            if len(normalized.split()) > 5:
                continue
            terms[normalized].add(rxcui)
    return {term: sorted(codes) for term, codes in terms.items()}


def find_rxnorm_hits(
    line: str,
    lexicon: dict[str, list[str]],
    allow_embedded: bool = False,
) -> list[dict]:
    if not lexicon:
        return []
    tokens = [match.group(0).casefold() for match in ASCII_TOKEN.finditer(line)]
    hits: dict[str, list[str]] = {}

    for size in range(1, min(5, len(tokens)) + 1):
        for index in range(0, len(tokens) - size + 1):
            candidate = " ".join(tokens[index:index + size])
            if candidate in lexicon:
                hits[candidate] = lexicon[candidate]

    # Recover an English drug embedded in a glued token only in medication-like
    # context, e.g. `Dùngmethadonekéo` or `albuterolipratropium`.
    if allow_embedded:
        for token in tokens:
            if token in lexicon or len(token) > 35:
                continue
            for start in range(len(token)):
                for end in range(start + 4, min(len(token), start + 26) + 1):
                    candidate = token[start:end]
                    if candidate in lexicon:
                        hits[candidate] = lexicon[candidate]

    # Remove shorter accidental matches inside a longer detected drug term,
    # e.g. `opium` inside `ipratropium`.
    terms = sorted(hits, key=len, reverse=True)
    kept = []
    for term in terms:
        if any(term != longer and term in longer for longer in kept):
            continue
        kept.append(term)

    return [
        {"term": term, "rxcui": hits[term][:5]}
        for term in sorted(kept, key=lambda value: (-len(value), value))
    ]
