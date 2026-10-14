from math import isfinite, sqrt
from numbers import Integral, Real

from pydantic import Field, model_validator
from scipy.stats import norm

from .descriptive import StatisticalResult


class ProportionConfidenceInterval(StatisticalResult):
	successes: int = Field(ge=0)
	sample_size: int = Field(ge=1)
	proportion: float = Field(ge=0, le=1)
	confidence_level: float = Field(gt=0, lt=1)
	lower_bound: float = Field(ge=0, le=1)
	upper_bound: float = Field(ge=0, le=1)
	method: str = "wilson_score"

	@model_validator(mode="after")
	def validate_interval(self):
		if self.successes > self.sample_size:
			raise ValueError("successes cannot exceed sample_size")
		if self.proportion != self.successes / self.sample_size:
			raise ValueError("proportion must match successes / sample_size")
		if self.lower_bound > self.proportion or self.proportion > self.upper_bound:
			raise ValueError("interval bounds must contain the observed proportion")
		if self.method != "wilson_score":
			raise ValueError("method must be 'wilson_score'")
		return self


def validate_confidence_level(confidence_level: float) -> float:
	if isinstance(confidence_level, bool) or not isinstance(
		confidence_level, Real
	):
		raise TypeError("confidence_level must be a real number")
	confidence_level = float(confidence_level)
	if not isfinite(confidence_level) or not 0 < confidence_level < 1:
		raise ValueError("confidence_level must be finite and between 0 and 1")
	return confidence_level


def wilson_interval(
	successes: int,
	sample_size: int,
	confidence_level: float = 0.95,
) -> ProportionConfidenceInterval:
	if isinstance(successes, bool) or not isinstance(successes, Integral):
		raise TypeError("successes must be an integer")
	if isinstance(sample_size, bool) or not isinstance(sample_size, Integral):
		raise TypeError("sample_size must be an integer")
	successes = int(successes)
	sample_size = int(sample_size)
	if sample_size < 1:
		raise ValueError("sample_size must be positive")
	if successes < 0 or successes > sample_size:
		raise ValueError("successes must be between zero and sample_size")
	confidence_level = validate_confidence_level(confidence_level)

	proportion = successes / sample_size
	z_score = float(norm.ppf(0.5 + confidence_level / 2.0))
	z_squared = z_score * z_score
	denominator = 1.0 + z_squared / sample_size
	center = (proportion + z_squared / (2.0 * sample_size)) / denominator
	half_width = (
		z_score
		* sqrt(
			proportion * (1.0 - proportion) / sample_size
			+ z_squared / (4.0 * sample_size * sample_size)
		)
		/ denominator
	)

	return ProportionConfidenceInterval(
		successes=successes,
		sample_size=sample_size,
		proportion=proportion,
		confidence_level=confidence_level,
		lower_bound=max(0.0, center - half_width),
		upper_bound=min(1.0, center + half_width),
	)
