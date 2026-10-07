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