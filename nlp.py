from __future__ import annotations

import logging
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    np = None  # type: ignore[assignment]

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency
    SentenceTransformer = None  # type: ignore[misc,assignment]


LOGGER = logging.getLogger(__name__)
_FALLBACK_NOTICE_EMITTED = False


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer | None:
    """Load and cache the sentence transformer model if available."""

    if SentenceTransformer is None:
        return None
    return SentenceTransformer("all-MiniLM-L6-v2")


def cosine_similarity(vec_a: "np.ndarray", vec_b: "np.ndarray") -> float:
    denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
    if denom == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)


def similarity(text: str, candidates: Iterable[str]) -> list[float]:
    model = get_model()
    if not candidates:
        return []

    if model is None or np is None:
        global _FALLBACK_NOTICE_EMITTED
        if not _FALLBACK_NOTICE_EMITTED:
            LOGGER.warning(
                "sentence-transformers not available; falling back to basic text similarity."
            )
            _FALLBACK_NOTICE_EMITTED = True

        baseline = text.lower()
        return [SequenceMatcher(None, baseline, candidate.lower()).ratio() for candidate in candidates]

    sentences = [text, *candidates]
    embeddings = model.encode(sentences, convert_to_numpy=True, normalize_embeddings=False)
    base_vec = embeddings[0]
    scores: list[float] = []
    for idx in range(1, len(embeddings)):
        scores.append(cosine_similarity(base_vec, embeddings[idx]))
    return scores
