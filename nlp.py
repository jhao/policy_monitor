from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import numpy as np
from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load and cache the sentence transformer model."""
    return SentenceTransformer("all-MiniLM-L6-v2")


def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
    if denom == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)


def similarity(text: str, candidates: Iterable[str]) -> list[float]:
    model = get_model()
    sentences = [text, *candidates]
    embeddings = model.encode(sentences, convert_to_numpy=True, normalize_embeddings=False)
    base_vec = embeddings[0]
    scores: list[float] = []
    for idx in range(1, len(embeddings)):
        scores.append(cosine_similarity(base_vec, embeddings[idx]))
    return scores
