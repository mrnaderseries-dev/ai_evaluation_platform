from math import isfinite
from numbers import Integral, Real
from typing import Sequence

from scipy.stats import rankdata


def rank_biserial_correlation(
    mann_whitney_u: Real,
    first_group_size: Integral,
    second_group_size: Integral,
) -> float:
    if isinstance(mann_whitney_u, bool) or not isinstance(mann_whitney_u, Real):
        raise TypeError("mann_whitney_u must be a real number")
    if isinstance(first_group_size, bool) or not isinstance(
        first_group_size, Integral
    ):
        raise TypeError("first_group_size must be an integer")
    if isinstance(second_group_size, bool) or not isinstance(
        second_group_size, Integral
    ):
        raise TypeError("second_group_size must be an integer")
    if first_group_size < 1 or second_group_size < 1:
        raise ValueError("both group sizes must be positive")

    statistic = float(mann_whitney_u)
    if not isfinite(statistic):
        raise ValueError("mann_whitney_u must be finite")
    maximum_u = first_group_size * second_group_size
    if not 0 <= statistic <= maximum_u:
        raise ValueError("mann_whitney_u is outside its possible range")

    return 2.0 * statistic / maximum_u - 1.0


def matched_pairs_rank_biserial(
    differences: Sequence[float],
) -> float | None:
    """Matched-pairs (Wilcoxon) rank-biserial correlation for one comparison.

    ``differences`` are paired ``candidate - baseline`` score differences in
    any order. Differences of exactly zero are discarded, matching the
    Wilcoxon signed-rank test's ``zero_method="wilcox"`` convention. The
    absolute non-zero differences are ranked with average ranks on ties (the
    convention scipy's ``wilcoxon`` uses), producing the rank sum ``W+`` of
    the positive differences and ``W-`` of the negative differences. The
    correlation is the candidate-oriented effect size::

        rb = (W+ - W-) / (W+ + W-)

    Positive values mean the candidate tends to score higher. Returns
    ``None`` when every difference is zero (or the sequence is empty), i.e.
    when the denominator ``W+ + W-`` is zero and no effect size is defined.
    """
    if not isinstance(differences, (list, tuple)):
        raise TypeError("differences must be a list or tuple")
    validated: list[float] = []
    for value in differences:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("differences must contain real numbers")
        number = float(value)
        if not isfinite(number):
            raise ValueError("differences must be finite")
        validated.append(number)

    non_zero = [difference for difference in validated if difference != 0.0]
    if not non_zero:
        return None
    ranks = rankdata([abs(difference) for difference in non_zero])
    w_plus = float(
        sum(rank for rank, difference in zip(ranks, non_zero) if difference > 0.0)
    )
    w_minus = float(
        sum(rank for rank, difference in zip(ranks, non_zero) if difference < 0.0)
    )
    denominator = w_plus + w_minus
    if denominator <= 0.0:
        return None
    return (w_plus - w_minus) / denominator