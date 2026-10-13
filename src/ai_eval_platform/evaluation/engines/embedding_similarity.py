from math import sqrt


def cosine_similarity(embedding_a: object, embedding_b: object) -> float:
    vector_a = list(embedding_a)
    vector_b = list(embedding_b)
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = sqrt(sum(a * a for a in vector_a))
    norm_b = sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        raise ValueError("Embeddings must not be zero vectors.")
    return dot / (norm_a * norm_b)


class EmbeddingSimilarityEngine:

    def __init__(self, embedder):
        self._embedder = embedder

    def compare(
        self,
        text_a: str,
        text_b: str,
    ) -> float:

        embedding_a = self._embedder.embed(text_a)
        embedding_b = self._embedder.embed(text_b)

        return cosine_similarity(
            embedding_a,
            embedding_b,
        )