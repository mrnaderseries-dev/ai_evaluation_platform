import pytest

from ai_eval_platform.statistics.comparison import holm_adjust


def test_hand_calculated_three_hypothesis_reference_case():
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx((0.03, 0.06, 0.06))


def test_hand_calculated_reference_case_two():
    assert holm_adjust([0.001, 0.008, 0.039, 0.04]) == pytest.approx(
        (0.004, 0.024, 0.078, 0.078)
    )


def test_unavailable_p_values_stay_none_and_are_excluded_from_family_size():
    assert holm_adjust([None, 0.01, 0.5]) == (
        None,
        pytest.approx(0.02),
        pytest.approx(0.5),
    )


def test_single_available_p_value_is_reported_unchanged():
    assert holm_adjust([0.2]) == (pytest.approx(0.2),)


def test_all_none_returns_all_none():
    assert holm_adjust([None, None]) == (None, None)


def test_adjusted_values_are_clipped_to_one():
    assert holm_adjust([0.6, 0.7]) == (pytest.approx(1.0), pytest.approx(1.0))


def test_tied_p_values_share_the_strongest_adjustment():
    assert holm_adjust([0.04, 0.04]) == (pytest.approx(0.08), pytest.approx(0.08))


def test_adjusted_values_are_never_smaller_than_raw_values():
    raw = [0.031, 0.017, 0.002, 0.044]
    adjusted = holm_adjust(raw)

    assert all(a >= r for a, r in zip(adjusted, raw))


def test_rejects_invalid_p_values():
    with pytest.raises(ValueError, match="between 0 and 1"):
        holm_adjust([1.5])
    with pytest.raises(ValueError, match="between 0 and 1"):
        holm_adjust([-0.1])
    with pytest.raises(TypeError, match="real numbers"):
        holm_adjust([True])
    with pytest.raises(TypeError, match="real numbers"):
        holm_adjust(["0.05"])
