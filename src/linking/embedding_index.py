from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None


class EmbeddingIndex:
    _MODEL_CACHE = {}
    """
    Dense vector index.

    Dùng chung cho ICD10 và RxNorm.

    File .npz lưu gồm:

        embeddings : float32 [N,D]
        ids        : object
        texts      : object

    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        
    ):
        if SentenceTransformer is None:
            raise ImportError(
                "sentence-transformers chưa được cài.\n"
                "pip install sentence-transformers"
            )

        model_path = Path(model_name)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Local embedding model not found: {model_name}. "
                "Run scripts/68_download_hybrid_models.py or restore the model store."
            )
        self.model_name = str(model_path.resolve())
        if self.model_name not in self._MODEL_CACHE:
            self._MODEL_CACHE[self.model_name] = SentenceTransformer(
                self.model_name,
                local_files_only=True,
            )
        self.model = self._MODEL_CACHE[self.model_name]

        self.metadata = None
        self.embeddings = None
        self.ids = None
        self.texts = None

    # --------------------------------------------------------
    # Encode
    # --------------------------------------------------------

    def _prepare(self, texts: Iterable[str], role: str) -> list[str]:
        values = list(texts)
        if "multilingual-e5" in self.model_name.casefold():
            prefix = "query: " if role == "query" else "passage: "
            return [prefix + value for value in values]
        return values

    def encode(
        self,
        texts: Iterable[str],
        batch_size: int = 64,
        role: str = "passage",
    ) -> np.ndarray:

        vectors = self.model.encode(
            self._prepare(texts, role),
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        return vectors.astype(np.float32)

    # --------------------------------------------------------
    # Build
    # --------------------------------------------------------

    def build(
        self,
        ids: list[str],
        texts: list[str],
    ):

        vectors = self.encode(texts, role="passage")

        self.ids = np.asarray(ids, dtype=object)
        self.texts = np.asarray(texts, dtype=object)
        self.embeddings = vectors

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    def save(
        self,
        path: Path,
        metadata=None,
    ):

        path.parent.mkdir(parents=True, exist_ok=True)

        np.savez_compressed(
            path,
            embeddings=self.embeddings,
            ids=self.ids,
            texts=self.texts,
            metadata=np.asarray(metadata, dtype=object),
        )

    # --------------------------------------------------------
    # Load
    # --------------------------------------------------------

    @classmethod
    def load(
        cls,
        path: Path,
        model_name: str = "BAAI/bge-small-en-v1.5",
    ):

        obj = cls(model_name)

        data = np.load(path, allow_pickle=True)

        obj.embeddings = data["embeddings"]
        obj.ids = data["ids"]
        obj.texts = data["texts"]

        if "metadata" in data.files:
            obj.metadata = data["metadata"]
        else:
            obj.metadata = None

        return obj

    # --------------------------------------------------------
    # Search
    # --------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
    ):

        if self.embeddings is None:
            return []

        q = self.model.encode(
            self._prepare([query], "query")[0],
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).astype(np.float32)

        scores = self.embeddings @ q

        top_idx = np.argsort(scores)[::-1][:top_k]

        results = []

        for idx in top_idx:

            # results.append(
            #     {
            #         "id": str(self.ids[idx]),
            #         "text": str(self.texts[idx]),
            #         "score": float(scores[idx]),
            #     }
            # )

            item = {
                "id": str(self.ids[idx]),
                "text": str(self.texts[idx]),
                "score": float(scores[idx]),
            }

            if self.metadata is not None:
                item["metadata"] = dict(self.metadata[idx])

            results.append(item)

        return results

    # --------------------------------------------------------
    # Batch Search
    # --------------------------------------------------------

    def batch_search(
        self,
        queries: list[str],
        top_k: int = 10,
    ):

        if self.embeddings is None:
            return [[] for _ in queries]

        q_vectors = self.model.encode(
            self._prepare(queries, "query"),
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=64,
        ).astype(np.float32)

        similarity = q_vectors @ self.embeddings.T

        outputs = []

        for row in similarity:

            idx = np.argsort(row)[::-1][:top_k]

            one = []

            for i in idx:

                # one.append(
                #     {
                #         "id": str(self.ids[i]),
                #         "text": str(self.texts[i]),
                #         "score": float(row[i]),
                #     }
                # )

                item = {
                    "id": str(self.ids[i]),
                    "text": str(self.texts[i]),
                    "score": float(row[i]),
                }

                if self.metadata is not None:
                    item["metadata"] = dict(self.metadata[i])

                one.append(item)

            outputs.append(one)

        return outputs

    # --------------------------------------------------------
    # Size
    # --------------------------------------------------------

    def __len__(self):

        if self.ids is None:
            return 0

        return len(self.ids)

    # --------------------------------------------------------
    # Info
    # --------------------------------------------------------

    def info(self):

        if self.embeddings is None:
            return {}

        return {
            "model": self.model_name,
            "items": len(self.ids),
            "dimension": self.embeddings.shape[1],
        }