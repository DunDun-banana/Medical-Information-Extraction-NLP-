from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.common.schema import Entity
from src.linking.bm25 import normalize
from src.linking.embedding_linker import EmbeddingLinker


RXNORM_GENERIC_BLACKLIST = {
    "thuốc", "đơn", "thuốc uống", "thuốc tiêm", "điều trị",
    "thuốc điều trị", "thuốc cũ", "thuốc mới", "thuốc kê đơn",
}

CLINICAL_TTYS = {"SCD", "SBD", "SCDF", "SBDF", "SCDC", "SBDC"}
INGREDIENT_TTYS = {"IN", "PIN", "MIN"}
BRAND_TTYS = {"BN"}
ALLOWED_TTYS = CLINICAL_TTYS | INGREDIENT_TTYS | BRAND_TTYS

STRENGTH_RE = re.compile(
    r"(?ix)\b(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg/ml|mcg/ml|mg|mcg|g|ml|meq|iu|units?|%)\b"
)
STRENGTH_RANGE_RE = re.compile(
    r"(?ix)\b(?P<low>\d+(?:[.,]\d+)?)\s*[-–]\s*"
    r"(?P<high>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg/ml|mcg/ml|mg|mcg|g|ml|meq|iu|units?|%)\b"
)
SCHEDULE_RE = re.compile(
    r"(?ix)\b(?:po|iv|im|sc|sl|bid|tid|qid|qhs|qam|qd|q\d+h|prn|daily|"
    r"once|twice|mỗi\s+ngày|hằng\s+ngày|đường\s+uống|uống|"
    r"tiêm\s+tĩnh\s+mạch|tiêm\s+bắp|ngậm\s+dưới\s+lưỡi)\b.*$"
)
LEADING_CUE_RE = re.compile(
    r"(?ix)^\s*(?:đang\s+)?(?:dùng|sử\s+dụng|uống|điều\s+trị\s+với|thuốc)\s+"
)

STOP_TOKENS = {
    "mg", "mcg", "ml", "meq", "unit", "units", "oral", "tablet", "tablets",
    "capsule", "capsules", "solution", "suspension", "injection", "injectable",
    "extended", "release", "delayed", "topical", "cream", "ointment", "patch",
    "dose", "pack", "hour", "hours", "actuation", "spray", "product",
}


@dataclass(frozen=True)
class RxEntry:
    rxcui: str
    name: str
    tty: str
    norm: str
    tokens: frozenset[str]
    strengths: tuple[str, ...]


def _strengths(text: str) -> tuple[str, ...]:
    values: list[str] = []
    range_spans: list[tuple[int, int]] = []
    # The benchmark examples map a PRN range such as 325-650 mg to the lower
    # unit dose (325 mg), so preserve the lower bound as the primary strength.
    for match in STRENGTH_RANGE_RE.finditer(text):
        unit = match.group("unit").casefold().replace("units", "unit")
        low = match.group("low").replace(",", ".")
        values.append(f"{low} {unit}")
        range_spans.append(match.span())
    for match in STRENGTH_RE.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        number = match.group("value").replace(",", ".")
        unit = match.group("unit").casefold().replace("units", "unit")
        key = f"{number} {unit}"
        if key not in values:
            values.append(key)
    return tuple(values)


def _name_tokens(text: str) -> list[str]:
    values = []
    for token in normalize(text).split():
        if token.isdigit() or token in STOP_TOKENS or len(token) < 2:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            continue
        values.append(token)
    return list(dict.fromkeys(values))


def _clean_mention(text: str) -> str:
    value = LEADING_CUE_RE.sub("", text.strip())
    value = SCHEDULE_RE.sub("", value).strip(" ,;:-")
    return re.sub(r"\s+", " ", value)


def _load_verified_drugs(path: Path | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if path is None or not Path(path).exists():
        return result
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in payload.get("verified", []):
        concept = row.get("selected_concept") or {}
        parsed = row.get("parsed") or {}
        code = str(concept.get("rxcui") or "").strip()
        if not code:
            continue
        surfaces = [
            row.get("original_text"), parsed.get("raw_text"),
            parsed.get("normalized_text"), parsed.get("drug_name"),
            concept.get("name"),
        ]
        for surface in surfaces:
            key = normalize(str(surface or ""))
            if key:
                result.setdefault(key, [])
                if code not in result[key]:
                    result[key].append(code)
    return result


class RxNormLinker:
    def __init__(
        self,
        parquet_path: Path,
        enable_embedding: bool = False,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        embedding_index: Path | None = None,
        embedding_top_k: int = 10,
        embedding_threshold: float = 0.78,
        knowledge_mapping_path: Path | None = None,
    ):
        self.embedding_threshold = embedding_threshold
        self.embedding_margin = 0.035
        self.entries: list[RxEntry] = []
        self.exact: dict[str, list[RxEntry]] = defaultdict(list)
        self.token_index: dict[str, list[int]] = defaultdict(list)
        self.verified = _load_verified_drugs(knowledge_mapping_path)
        self.embedding = None

        for rxcui, name, tty in self._load_rows(Path(parquet_path)):
            if tty not in ALLOWED_TTYS:
                continue
            norm = normalize(name)
            if not norm:
                continue
            entry = RxEntry(
                rxcui=str(rxcui),
                name=str(name),
                tty=str(tty),
                norm=norm,
                tokens=frozenset(_name_tokens(name)),
                strengths=_strengths(name),
            )
            index = len(self.entries)
            self.entries.append(entry)
            self.exact[norm].append(entry)
            for token in entry.tokens:
                if len(token) >= 3:
                    self.token_index[token].append(index)

        if enable_embedding and embedding_index is not None and Path(embedding_index).exists():
            self.embedding = EmbeddingLinker(
                model_name=embedding_model,
                index_path=embedding_index,
                top_k=embedding_top_k,
                threshold=embedding_threshold,
            )

    @staticmethod
    def _load_rows(parquet_path: Path) -> Iterable[tuple[str, str, str]]:
        if parquet_path.suffix.casefold() == ".json" and parquet_path.exists():
            payload = json.loads(parquet_path.read_text(encoding="utf-8"))
            for row in payload:
                rxcui = row.get("rxcui") or row.get("RXCUI")
                name = row.get("name") or row.get("STR")
                tty = row.get("tty") or row.get("TTY")
                if rxcui and name and tty:
                    yield str(rxcui), str(name), str(tty)
            return
        try:
            import pandas as pd
            frame = pd.read_parquet(parquet_path, columns=["RXCUI", "STR", "TTY"])
            for row in frame.itertuples(index=False):
                yield str(row.RXCUI), str(row.STR), str(row.TTY)
            return
        except Exception:
            pass

        raw_path = parquet_path.parent.parent / "raw" / "ontology" / "rxnorm" / "RXNCONSO.RRF"
        if not raw_path.exists():
            return
        with raw_path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                parts = line.rstrip("\r\n").split("|")
                if len(parts) <= 16:
                    continue
                if parts[1] != "ENG" or parts[16] != "N":
                    continue
                yield parts[0], parts[14], parts[12]

    @staticmethod
    def _tty_rank(tty: str, has_strength: bool) -> int:
        if has_strength:
            order = {"SCD": 0, "SBD": 1, "SCDF": 2, "SBDF": 3, "SCDC": 4, "SBDC": 5,
                     "PIN": 7, "IN": 8, "MIN": 9, "BN": 10}
        else:
            order = {"IN": 0, "PIN": 1, "MIN": 2, "BN": 3, "SCD": 6, "SBD": 7,
                     "SCDF": 8, "SBDF": 9, "SCDC": 10, "SBDC": 11}
        return order.get(tty, 99)

    def _best_exact(self, query: str, has_strength: bool) -> RxEntry | None:
        rows = self.exact.get(normalize(query), [])
        if not rows:
            return None
        return min(rows, key=lambda row: self._tty_rank(row.tty, has_strength))

    def _clinical_match(self, entity: Entity) -> tuple[RxEntry, float] | None:
        mention = _clean_mention(entity.text)
        mention_strengths = _strengths(mention)
        if not mention_strengths:
            return None

        canonical = str(
            entity.metadata.get("canonical_name")
            or entity.metadata.get("normalized")
            or ""
        ).strip()
        name_source = canonical or STRENGTH_RE.split(mention, maxsplit=1)[0]
        name_tokens = [token for token in _name_tokens(name_source) if not token.isdigit()]
        if not name_tokens:
            return None

        postings = [self.token_index[token] for token in name_tokens if token in self.token_index]
        if not postings:
            return None
        candidate_ids = set(min(postings, key=len))

        scored: list[tuple[float, RxEntry]] = []
        mention_norm = normalize(mention)
        raw_lower = entity.text.casefold()
        is_iv = bool(re.search(r"(?i)\biv\b|tĩnh\s+mạch|truyền\s+tĩnh\s+mạch", raw_lower))
        is_im = bool(re.search(r"(?i)\bim\b|tiêm\s+bắp", raw_lower))
        is_oral = bool(re.search(r"(?i)\bpo\b|\boral\b|đường\s+uống|\buống\b", raw_lower))
        for index in candidate_ids:
            entry = self.entries[index]
            if entry.tty not in CLINICAL_TTYS:
                continue
            overlap = len(set(name_tokens) & entry.tokens) / max(1, len(set(name_tokens)))
            if overlap < 0.6:
                continue
            strength_overlap = len(set(mention_strengths) & set(entry.strengths))
            if strength_overlap == 0:
                continue
            score = 3.0 * overlap + 2.5 * strength_overlap
            score += 1.2 if entry.tty == "SCD" else 0.9 if entry.tty == "SBD" else 0.3
            if is_iv or is_im:
                # Do not replace a valid ingredient seed with an oral tablet
                # when the note explicitly says IV/IM and RxNorm lacks an exact
                # injectable strength concept in the local allowed-TTY index.
                if "injection" not in entry.norm and "injectable" not in entry.norm:
                    continue
                score += 1.0
            elif is_oral:
                if "oral" in entry.norm:
                    score += 0.5
                if "injection" in entry.norm or "injectable" in entry.norm:
                    score -= 0.5
            if "xl" in raw_lower or "extended" in mention_norm:
                score += 0.45 if "extended release" in entry.norm or "24 hr" in entry.norm else 0.0
            if "/" not in mention and "/" in entry.name:
                score -= 0.75
            scored.append((score, entry))

        if not scored:
            return None
        scored.sort(key=lambda value: (-value[0], self._tty_rank(value[1].tty, True), len(value[1].name)))
        return scored[0][1], scored[0][0]

    def search(self, query: str, top_k: int = 10, method: str = "hybrid") -> list[dict]:
        if method == "exact":
            entry = self._best_exact(query, bool(_strengths(query)))
            return [] if entry is None else [{"rxcui": entry.rxcui, "name": entry.name, "tty": entry.tty, "score": 1.0}]
        if method == "embedding":
            if self.embedding is None:
                return []
            output = []
            for item in self.embedding.search(query, top_k):
                meta = item["metadata"]
                output.append({
                    "rxcui": str(meta["RXCUI"]), "name": meta["STR"],
                    "tty": meta["TTY"], "score": item["score"],
                })
            return output
        exact = self.search(query, method="exact")
        return exact or self.search(query, top_k=top_k, method="embedding")

    def link(self, entity: Entity) -> Entity:
        if entity.type != "THUỐC" or not self.entries:
            return entity
        if normalize(entity.text) in {normalize(x) for x in RXNORM_GENERIC_BLACKLIST}:
            entity.candidates = []
            entity.metadata["link_method"] = "rxnorm_blacklisted"
            return entity

        mention = _clean_mention(entity.text)
        has_strength = bool(_strengths(mention))

        # Dose-specific clinical concepts should replace ingredient seed codes.
        if has_strength:
            clinical = self._clinical_match(entity)
            if clinical is not None:
                entry, score = clinical
                entity.candidates = [entry.rxcui]
                entity.metadata["link_method"] = "rxnorm_clinical_strength"
                entity.metadata["rxnorm_match"] = {
                    "query": mention, "score": score, "rxcui": entry.rxcui,
                    "name": entry.name, "tty": entry.tty,
                }
                return entity

        # Keep a trusted dictionary candidate when no stronger dose match exists.
        if entity.candidates:
            entity.metadata.setdefault("link_method", "rxnorm_seed_exact")
            return entity

        query_candidates = [
            mention,
            str(entity.metadata.get("normalized") or "").strip(),
            str(entity.metadata.get("canonical_name") or "").strip(),
        ]
        for query in query_candidates:
            if not query:
                continue
            verified_codes = self.verified.get(normalize(query), [])
            if verified_codes:
                entity.candidates = verified_codes[:1]
                entity.metadata["link_method"] = "rxnorm_knowledge_exact"
                return entity
            entry = self._best_exact(query, has_strength)
            if entry is not None:
                entity.candidates = [entry.rxcui]
                entity.metadata["link_method"] = "rxnorm_exact"
                entity.metadata["rxnorm_match"] = {"query": query, "name": entry.name, "tty": entry.tty}
                return entity

        if self.embedding is not None:
            query = next((value for value in query_candidates[1:] if value), mention)
            results = self.embedding.search(query, top_k=3)
            if results:
                filtered = []
                for item in results:
                    meta = item["metadata"]
                    tty = str(meta.get("TTY", ""))
                    if has_strength and tty not in CLINICAL_TTYS:
                        continue
                    filtered.append(item)
                if filtered:
                    best = filtered[0]
                    second_score = filtered[1]["score"] if len(filtered) > 1 else 0.0
                    meta = best["metadata"]
                    score = float(best["score"])
                    entity.metadata["rxnorm_embedding"] = {
                        "query": query, "score": score, "rxcui": str(meta["RXCUI"]),
                        "name": meta["STR"], "tty": meta["TTY"],
                        "margin": score - float(second_score),
                    }
                    if score >= self.embedding_threshold and score - float(second_score) >= self.embedding_margin:
                        entity.candidates = [str(meta["RXCUI"])]
                        entity.metadata["link_method"] = "rxnorm_embedding_calibrated"
        return entity
