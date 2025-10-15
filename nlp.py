from __future__ import annotations

import logging
import os
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
_MODEL_NAME = "all-MiniLM-L6-v2"
_HF_ENDPOINT_ENV = "HF_ENDPOINT"
_MIRROR_ENV = "POLICY_MONITOR_HF_MIRROR"
_DEFAULT_MIRROR = "https://hf-mirror.com"


def _load_sentence_transformer() -> SentenceTransformer:
    """Instantiate the configured SentenceTransformer model."""

    return SentenceTransformer(_MODEL_NAME)


def _configure_hf_mirror_on_failure(exc: Exception) -> bool:
    """Set a Hugging Face mirror endpoint if model download failed.

    Returns ``True`` when a mirror endpoint was configured and the caller
    should retry loading the model, otherwise ``False``.
    """

    if os.environ.get(_HF_ENDPOINT_ENV):
        return False

    mirror_endpoint = os.getenv(_MIRROR_ENV, _DEFAULT_MIRROR).strip()
    if not mirror_endpoint:
        return False

    message = str(exc).lower()
    if "huggingface.co" not in message and "connection" not in message:
        return False

    os.environ[_HF_ENDPOINT_ENV] = mirror_endpoint
    LOGGER.info("Retrying SentenceTransformer download via mirror %s", mirror_endpoint)
    return True


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer | None:
    """Load and cache the sentence transformer model if available."""

    if SentenceTransformer is None:
        return None
    try:
        return _load_sentence_transformer()
    except Exception as exc:  # pragma: no cover - network dependent
        LOGGER.warning("Failed to load SentenceTransformer model: %s", exc)
        if _configure_hf_mirror_on_failure(exc):
            try:
                return _load_sentence_transformer()
            except Exception as mirror_exc:  # pragma: no cover - network dependent
                LOGGER.error("Failed to load model from mirror endpoint: %s", mirror_exc)
        return None


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
