class GroundednessJudge:

    def __init__(self, model):
        self._model = model

    def evaluate(
        self,
        answer: str,
        context: tuple,
    ) -> float:

        prompt = self._build_prompt(
            answer=answer,
            context=context,
        )

        response = self._model.generate(prompt)

        score = self._parse_score(response)

        return max(0.0, min(1.0, score))

    def _build_prompt(self, *, answer: str, context: tuple) -> str:
        items = "\n".join(str(item) for item in context)
        return f"Answer: {answer}\nContext:\n{items}"

    def _parse_score(self, response: object) -> float:
        return float(str(response).strip())