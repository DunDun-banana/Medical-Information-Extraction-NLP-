from __future__ import annotations

from collections import Counter
import math
import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    return [t for t in normalize(text).split() if len(t) > 1]


class BM25Index:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokenize(d) for d in documents]
        self.lengths = [len(d) for d in self.docs]
        self.avgdl = sum(self.lengths) / max(1, len(self.lengths))
        self.tfs = [Counter(d) for d in self.docs]
        df = Counter()
        for d in self.docs:
            df.update(set(d))
        n = max(1, len(self.docs))
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        terms = tokenize(query)
        if not terms:
            return []
        scores: list[tuple[int, float]] = []
        for idx, tf in enumerate(self.tfs):
            score = 0.0
            dl = self.lengths[idx]
            for term in terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-9))
                score += self.idf.get(term, 0.0) * (freq * (self.k1 + 1) / denom)
            if score > 0:
                scores.append((idx, score))
        return sorted(scores, key=lambda x: x[1], reverse=True)[:top_k]
