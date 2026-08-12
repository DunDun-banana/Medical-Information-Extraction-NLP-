from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.common.schema import Entity
from src.linking.bm25 import BM25Index, normalize, tokenize
from src.linking.embedding_linker import EmbeddingLinker


ICD_GENERIC_BLACKLIST = {
    "tổn thương", "ổ dịch", "nang", "u", "khối", "chấn thương", "vết thương",
    "rối loạn", "bất thường", "thay đổi", "gián đoạn", "hình ảnh", "kết quả",
    "tiền sử", "bệnh sử", "triệu chứng", "chẩn đoán", "điều trị", "thủ thuật",
}
ICD10_REGEX = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,4})?)\b")


def _load_verified_icd(path: Path | None) -> dict[str, list[str]]:
    """Load only strict exact mappings; reject lexical mappings such as tụy/tủy."""
    result: dict[str, list[str]] = {}
    if path is None or not Path(path).exists():
        return result
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in payload.get("verified", []):
        if str(row.get("mapping_method", "")) != "exact_icd10":
            continue
        if float(row.get("confidence", 0.0) or 0.0) < 0.99:
            continue
        concept = row.get("selected_concept") or {}
        code = str(concept.get("code") or "").strip()
        if not code:
            continue
        for surface in [row.get("vn_mention"), concept.get("name_vi")]:
            key = normalize(str(surface or ""))
            if key:
                result.setdefault(key, [])
                if code not in result[key]:
                    result[key].append(code)
    return result


def _related_code(a: str, b: str) -> bool:
    """True for a category/detail pair, e.g. I50 and I50.9."""
    left = a.replace(".", "")
    right = b.replace(".", "")
    return left.startswith(right) or right.startswith(left)


class ICDLinker:
    def __init__(
        self,
        icd_csv: Path,
        aliases_csv: Path,
        top_k: int = 10,
        min_score: float = 3.4,
        min_margin: float = 0.45,
        enable_embedding: bool = False,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        embedding_index: Path | None = None,
        embedding_top_k: int = 10,
        embedding_threshold: float = 0.78,
        knowledge_mapping_path: Path | None = None,
        byt_csv: Path | None = None,
    ):
        self.top_k = top_k
        self.min_score = min_score
        self.min_margin = min_margin
        self.embedding_threshold = embedding_threshold
        self.embedding_margin = 0.035
        self.alias_codes: dict[str, list[str]] = {}
        self.verified_codes = _load_verified_icd(knowledge_mapping_path)

        with aliases_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                codes = [x.strip() for x in row.get("icd_codes", "").split("|") if x.strip()]
                for field in ("surface_form", "canonical_name"):
                    key = normalize(row.get(field, ""))
                    if key and codes:
                        self.alias_codes[key] = list(dict.fromkeys(codes))

        self.byt_rows: list[dict] = []
        if byt_csv is not None and Path(byt_csv).exists():
            with Path(byt_csv).open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    code = str(row.get("code") or "").strip()
                    title = str(row.get("name_vi") or "").strip()
                    if not code or not title:
                        continue
                    self.byt_rows.append({
                        "code": code,
                        "title": title,
                        "category_title": str(row.get("group_vi") or ""),
                    })
                    key = normalize(title)
                    if key:
                        self.alias_codes.setdefault(key, [])
                        if code not in self.alias_codes[key]:
                            self.alias_codes[key].append(code)

        self.rows: list[dict] = []
        docs: list[str] = []
        with icd_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if str(row.get("terminal_flag", "")).strip().upper() != "T":
                    continue
                self.rows.append(row)
                docs.append(f"{row.get('title', '')} {row.get('category_title', '')}")
        for row in self.byt_rows:
            self.rows.append(row)
            docs.append(f"{row.get('title', '')} {row.get('category_title', '')}")
        self.index = BM25Index(docs)

        self.embedding = None
        if enable_embedding and embedding_index is not None and Path(embedding_index).exists():
            self.embedding = EmbeddingLinker(
                model_name=embedding_model,
                index_path=embedding_index,
                top_k=embedding_top_k,
                threshold=embedding_threshold,
            )

    def _apply_verified(self, entity: Entity) -> bool:
        codes = self.verified_codes.get(normalize(entity.text), [])
        if not codes:
            return False
        if not entity.candidates:
            entity.candidates = codes[:2]
            entity.metadata["link_method"] = "icd_knowledge_exact"
            return True

        # Preserve a more specific dictionary code but add its verified parent.
        merged = list(entity.candidates)
        for code in codes:
            if code in merged:
                continue
            if any(_related_code(code, current) for current in merged):
                merged.insert(0, code)
        entity.candidates = list(dict.fromkeys(merged))[:2]
        if entity.candidates != merged[-len(entity.candidates):] or any(code in entity.candidates for code in codes):
            entity.metadata["link_method"] = "icd_knowledge_enriched"
        return False

    def link(self, entity: Entity) -> Entity:
        if entity.type != "CHẨN_ĐOÁN":
            return entity

        explicit_codes = ICD10_REGEX.findall(entity.text.upper())
        if explicit_codes:
            entity.candidates = list(dict.fromkeys(explicit_codes))[:3]
            entity.metadata["link_method"] = "icd_explicit_regex"
            return entity

        if normalize(entity.text) in {normalize(value) for value in ICD_GENERIC_BLACKLIST}:
            entity.candidates = []
            entity.metadata["link_method"] = "icd_blacklisted"
            return entity

        # Never broaden or replace an existing rule/dictionary candidate.
        # Parent+detail enrichment reduced Jaccard when the reference contained
        # only the detailed code. Strict knowledge is used only to fill empties.
        if entity.candidates:
            entity.metadata.setdefault("link_method", "icd_dictionary_exact")
            return entity
        if self._apply_verified(entity):
            return entity

        surface = normalize(entity.text)
        if surface in self.alias_codes:
            entity.candidates = self.alias_codes[surface][:3]
            entity.metadata["link_method"] = "icd_alias_exact"
            return entity

        query = str(entity.metadata.get("normalized") or entity.metadata.get("canonical_name") or "").strip()
        if not query or not tokenize(query):
            query = entity.text
        qnorm = normalize(query)

        if qnorm in self.alias_codes:
            entity.candidates = self.alias_codes[qnorm][:3]
            entity.metadata["link_method"] = "icd_canonical_exact"
            return entity

        hits = self.index.search(query, self.top_k)
        if hits:
            rescored: list[tuple[int, float, float, float]] = []
            qtokens = set(tokenize(query))
            for idx, score in hits:
                title = self.rows[idx].get("title", "")
                title_norm = normalize(title)
                bonus = 3.0 if title_norm == qnorm else 1.5 if title_norm.startswith(qnorm) else 0.75 if qnorm in title_norm else 0.0
                coverage = len(qtokens & set(tokenize(title))) / max(1, len(qtokens))
                rescored.append((idx, score, score + bonus, coverage))
            rescored.sort(key=lambda value: value[2], reverse=True)
            best_idx, best_score, best_adjusted, coverage = rescored[0]
            second_adjusted = rescored[1][2] if len(rescored) > 1 else 0.0
            margin = best_adjusted - second_adjusted
            best = self.rows[best_idx]
            entity.metadata["icd_bm25_debug"] = {
                "query": query, "code": best["code"], "title": best["title"],
                "score": best_score, "adjusted": best_adjusted,
                "coverage": coverage, "margin": margin,
            }
            exactish = normalize(best.get("title", "")) == qnorm
            if best_score >= self.min_score and coverage >= 0.6 and (exactish or margin >= self.min_margin):
                entity.candidates = [best["code"]]
                entity.metadata["link_method"] = "icd_bm25_calibrated"
                return entity

        if self.embedding is not None:
            results = self.embedding.search(query, top_k=3)
            if results:
                best = results[0]
                second_score = float(results[1]["score"]) if len(results) > 1 else 0.0
                meta = best["metadata"]
                score = float(best["score"])
                margin = score - second_score
                entity.metadata["icd_embedding"] = {
                    "query": query, "score": score, "code": meta["code"],
                    "title": meta["title"], "margin": margin,
                }
                if score >= self.embedding_threshold and margin >= self.embedding_margin:
                    entity.candidates = [str(meta["code"])]
                    entity.metadata["link_method"] = "icd_embedding_calibrated"
        return entity

    def search(self, query: str, top_k: int = 15, method: str = "hybrid") -> list[dict]:
        if method == "embedding":
            if self.embedding is None:
                return []
            return [
                {
                    "code": item["metadata"]["code"],
                    "title": item["metadata"]["title"],
                    "score": item["score"],
                }
                for item in self.embedding.search(query, top_k)
            ]
        if method == "bm25":
            return [
                {"code": self.rows[idx]["code"], "title": self.rows[idx]["title"], "score": score}
                for idx, score in self.index.search(query, top_k)
            ]

        bm25_hits = self.search(query, top_k=top_k * 3, method="bm25")
        embedding_hits = self.search(query, top_k=top_k * 3, method="embedding")
        merged: dict[str, dict] = {}
        for item in bm25_hits:
            merged[item["code"]] = {**item, "bm25": item["score"], "embedding": 0.0}
        for item in embedding_hits:
            row = merged.setdefault(item["code"], {**item, "bm25": 0.0, "embedding": 0.0})
            row["embedding"] = item["score"]
        max_bm25 = max((row["bm25"] for row in merged.values()), default=1.0)
        for row in merged.values():
            row["score"] = 0.45 * row["bm25"] / max(max_bm25, 1e-6) + 0.55 * row["embedding"]
        return sorted(merged.values(), key=lambda row: row["score"], reverse=True)[:top_k]
