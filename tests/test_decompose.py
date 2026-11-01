import math

import pytest

from funnel_magnet.decompose import Factor, decompose, log_mean


def roas_example():
    return decompose(
        "roas", 3.0, 2.28, [Factor("aov", 60, 57), Factor("cpa", 20, 25, -1)]
    )


def test_approved_worked_example():
    result = roas_example()
    aov, cpa = result.contributions

    assert aov.contribution == pytest.approx(-0.1346, abs=1e-3)
    assert cpa.contribution == pytest.approx(-0.5854, abs=1e-3)
    assert aov.share == pytest.approx(0.187, abs=1e-3)
    assert cpa.share == pytest.approx(0.813, abs=1e-3)


def test_contributions_sum_exactly_to_the_change():
    result = roas_example()

    assert sum(c.contribution for c in result.contributions) == pytest.approx(2.28 - 3.0)
    assert sum(c.share for c in result.contributions) == pytest.approx(1.0)
    assert sum(c.weight for c in result.contributions) == pytest.approx(1.0)


def test_order_does_not_change_contributions():
    reversed_result = decompose(
        "roas", 3.0, 2.28, [Factor("cpa", 20, 25, -1), Factor("aov", 60, 57)]
    )
    by_name = {c.name: c.contribution for c in reversed_result.contributions}

    assert by_name["aov"] == pytest.approx(roas_example().contributions[0].contribution)


def test_offsetting_factors_have_no_net_share():
    result = decompose("roas", 3.0, 3.0, [Factor("aov", 60, 72), Factor("cpa", 20, 24, -1)])
    aov, cpa = result.contributions

    assert aov.share is None and cpa.share is None
    assert aov.contribution == pytest.approx(-cpa.contribution)
    assert aov.weight == pytest.approx(0.5)


def test_unchanged_values_contribute_nothing():
    result = decompose("roas", 3.0, 3.0, [Factor("aov", 60, 60), Factor("cpa", 20, 20, -1)])

    assert all(c.contribution == 0 and c.weight == 0 for c in result.contributions)


@pytest.mark.parametrize(
    "prior, current, factors",
    [
        (None, 2.0, [Factor("aov", 60, 60), Factor("cpa", 20, 30, -1)]),
        (3.0, 0.0, [Factor("aov", 60, 0.0), Factor("cpa", 20, 30, -1)]),
        (3.0, 2.0, [Factor("aov", 60, 60), Factor("cpa", None, 30, -1)]),
        (0.0, 2.0, [Factor("aov", 0.0, 60), Factor("cpa", 20, 30, -1)]),
    ],
)
def test_zero_or_missing_values_give_no_contributions(prior, current, factors):
    result = decompose("roas", prior, current, factors)

    assert not result.defined
    assert result.contributions == ()
    assert result.undefined_reason


def test_inconsistent_definitions_are_rejected():
    result = decompose("roas", 3.0, 2.0, [Factor("aov", 60, 60), Factor("cpa", 25, 30, -1)])

    assert not result.defined
    assert "inconsistent" in result.undefined_reason


def test_log_mean_handles_equal_values():
    assert log_mean(4.0, 4.0) == 4.0
    assert log_mean(1.0, math.e) == pytest.approx(math.e - 1)
