from math import isfinite
from numbers import Real
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class StatisticalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DescriptiveStatistics(StatisticalResult):
    sample_size: int = Field(ge=1)
    mean: FiniteFloat
    median: FiniteFloat
    standard_deviation: FiniteFloat | None
    minimum: FiniteFloat
    maximum: FiniteFloat
    p95: FiniteFloat


def validate_values(
    values: list[float] | tuple[float, ...],
    minimum_size: int,
) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError("values must be a list or tuple")
    if len(values) < minimum_size:
        raise ValueError(f"At least {minimum_size} observations are required")

    validated: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("All observations must be real numbers")
        try:
            numeric_value = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError(
                "Observations must be representable as finite floats"
            ) from exc
        if not isfinite(numeric_value):
            raise ValueError("Observations must be finite")
        validated.append(numeric_value)
    return tuple(validated)


def finite_result(value: float, name: str) -> float:
    result = float(value)
    if not isfinite(result):
        raise ArithmeticError(f"{name} is not finite; inspect the input scale")
    return result


def describe(
    values: list[float] | tuple[float, ...],
) -> DescriptiveStatistics:
    data = validate_values(values, minimum_size=1)
    array = np.asarray(data, dtype=np.float64)

    with np.errstate(over="ignore", invalid="ignore"):
        mean = finite_result(np.mean(array), "mean")
        median = finite_result(np.median(array), "median")
        minimum = finite_result(np.min(array), "minimum")
        maximum = finite_result(np.max(array), "maximum")
        p95 = finite_result(np.percentile(array, 95, method="linear"), "p95")
        standard_deviation = (
            finite_result(np.std(array, ddof=1), "standard_deviation")
            if len(data) > 1
            else None
        )

    return DescriptiveStatistics(
        sample_size=len(data),
        mean=mean,
        median=median,
        standard_deviation=standard_deviation,
        minimum=minimum,
        maximum=maximum,
        p95=p95,
    )