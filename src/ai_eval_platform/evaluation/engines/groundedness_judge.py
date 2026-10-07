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