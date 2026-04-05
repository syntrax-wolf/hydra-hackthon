"""BGE-M3 embedding computation via sentence-transformers.

Lazy singleton — model is loaded on first call, then cached in memory.
Uses BAAI/bge-m3 (1024 dimensions) for semantic search.
"""

import logging
import numpy as np

log = logging.getLogger("embeddings")

_model = None


def _get_model():
    """Load BGE-M3 model on first use. ~2GB download on first run."""
    global _model
    if _model is None:
        log.info("[EMBEDDINGS] Loading BAAI/bge-m3 model (first call, may download ~2GB)...")
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("BAAI/bge-m3")
            log.info("[EMBEDDINGS] Model loaded successfully")
        except Exception as e:
            log.error("[EMBEDDINGS] Failed to load BGE-M3: %s", e)
            raise
    return _model


def compute_embedding(text: str) -> list[float]:
    """Compute a 1024-dim embedding for the given text.

    Returns a list of floats suitable for PostgreSQL VECTOR(1024).
    """
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def compute_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Compute embeddings for a batch of texts. More efficient than calling one at a time."""
    if not texts:
        return []
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return [e.tolist() for e in embeddings]


def random_embedding(dim: int = 1024) -> list[float]:
    """Generate a normalized random vector. Placeholder for testing without the real model."""
    vec = np.random.randn(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()
