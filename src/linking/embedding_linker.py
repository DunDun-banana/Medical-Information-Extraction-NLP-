from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Optional

import numpy as np

from src.linking.embedding_index import EmbeddingIndex


class EmbeddingLinker:
    """
    Generic embedding-based linker.

    Supports:
        - ICD
        - RxNorm
        - Any ontology whose embedding index is built by
          EmbeddingIndex.save()
    """

    def __init__(
        self,
        model_name: str,
        index_path: Path,
        top_k: int = 10,
        threshold: float = 0.75,
    ):
        self.model_name = model_name
        self.index_path = Path(index_path)
        self.top_k = top_k
        self.threshold = threshold

        self.index = EmbeddingIndex.load(
               index_path,
               model_name=model_name,
            )        

      #   self.index = EmbeddingIndex(model_name)
      #   self.index.load(self.index_path)
      

    # -------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[Dict]:

        if not query.strip():
            return []

        results = self.index.search(
            query=query,
            top_k=top_k or self.top_k,
        )

        output = []

        for item in results:


            output.append(
                {
                    "score": float(item["score"]),
                    "metadata": item["metadata"],
                }
            )

        return output

    # -------------------------------------------------------

    def best(
        self,
        query: str,
    ) -> Optional[Dict]:

        results = self.search(query, top_k=1)

        if len(results) == 0:
            return None

        return results[0]

    # -------------------------------------------------------

    def link_icd(self, query: str) -> Optional[Dict]:
        """
        Return

        {
            code,
            title,
            score
        }
        """

        hit = self.best(query)

        if hit is None:
            return None

        meta = hit["metadata"]

        return {
            "code": meta.get("code"),
            "title": meta.get("title"),
            "score": hit["score"],
        }

    # -------------------------------------------------------

    def link_rxnorm(self, query: str) -> Optional[Dict]:
        """
        Return

        {
            RXCUI,
            STR,
            score
        }
        """

        hit = self.best(query)

        if hit is None:
            return None

        meta = hit["metadata"]

        return {
            "RXCUI": meta.get("RXCUI"),
            "STR": meta.get("STR"),
            "TTY": meta.get("TTY"),
            "score": hit["score"],
        }

    # -------------------------------------------------------

    def search_debug(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Dict]:

        return self.search(query, top_k)

    # -------------------------------------------------------

    def __len__(self):

        return len(self.index.metadata)

    # -------------------------------------------------------

    def __repr__(self):

        return (
            f"EmbeddingLinker("
            f"items={len(self)}, "
            f"threshold={self.threshold}, "
            f"top_k={self.top_k})"
        )