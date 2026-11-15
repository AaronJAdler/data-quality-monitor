from __future__ import annotations

import random

import pytest
from scipy import stats as sp

from dqmon import stats


def test_median_mad_and_robust_z() -> None:
    values = [10, 10, 11, 12, 30]
    assert stats.median(values) == 11
    assert stats.mad(values) == 1
    assert stats.robust_z(11, values, min_mad=1) == 0
    assert stats.robust_z(21, values, min_mad=1) == pytest.approx(6.745)
    # A flat history has a MAD of 0; the floor keeps the score finite.
    assert stats.robust_z(13, [12] * 14, min_mad=2) == pytest.approx(0.6745 / 2)


@pytest.mark.parametrize(
    ("k", "n", "p"), [(0, 15, 0.75), (7, 15, 0.74), (15, 15, 0.1), (3, 40, 0.02)]
)
def test_binomial_tails_match_scipy(k: int, n: int, p: float) -> None:
    lower, upper = stats.binom_tails(k, n, p)
    assert lower == pytest.approx(sp.binom.cdf(k, n, p), rel=1e-9)
    assert upper == pytest.approx(sp.binom.sf(k - 1, n, p), rel=1e-9)


def test_binomial_tails_at_the_edges() -> None:
    assert stats.binom_tails(0, 10, 0.0) == (1.0, 1.0)
    assert stats.binom_tails(3, 10, 0.0) == (1.0, 0.0)
    assert stats.binom_tails(10, 10, 1.0) == (1.0, 1.0)


@pytest.mark.parametrize("df", [1, 2, 5, 9])
@pytest.mark.parametrize("x", [0.05, 1.0, 4.5, 12.0, 40.0])
def test_chi_square_tail_matches_scipy(x: float, df: int) -> None:
    assert stats.chi2_sf(x, df) == pytest.approx(sp.chi2.sf(x, df), rel=1e-8, abs=1e-14)


def test_ks_statistic_matches_scipy_and_p_value_is_close() -> None:
    rng = random.Random(7)
    for shift in (0.0, 0.3, 1.0):
        a = [rng.gauss(0, 1) for _ in range(200)]
        b = [rng.gauss(shift, 1) for _ in range(60)]
        d, p = stats.ks_2samp(a, b)
        reference = sp.ks_2samp(a, b, method="asymp")
        assert d == pytest.approx(reference.statistic, abs=1e-12)
        assert p == pytest.approx(reference.pvalue, abs=0.03)


def test_ks_handles_ties() -> None:
    a = [1, 1, 1, 2, 2, 3] * 10
    b = [1, 2, 2, 2, 3, 3] * 5
    d, _ = stats.ks_2samp(a, b)
    assert d == pytest.approx(sp.ks_2samp(a, b).statistic, abs=1e-12)


def test_psi_is_near_zero_for_the_same_distribution_and_large_for_a_shift() -> None:
    rng = random.Random(11)
    base = [rng.gauss(50, 10) for _ in range(2000)]
    same = [rng.gauss(50, 10) for _ in range(2000)]
    moved = [rng.gauss(65, 10) for _ in range(2000)]
    assert stats.psi(base, same, 10)[0] < 0.02
    value, p, bins = stats.psi(base, moved, 10)
    assert value > 0.25 and p < 1e-6 and bins == 10


def test_psi_p_value_keeps_small_samples_from_alerting_on_noise() -> None:
    """With 15 values, PSI exceeds the usual 0.25 cut-off by chance alone most of the time.

    The chi-square p-value is what makes the monitor usable on small partitions.
    """
    rng = random.Random(3)
    trials, over_fixed, over_p = 500, 0, 0
    for _ in range(trials):
        base = [rng.gauss(0, 1) for _ in range(200)]
        current = [rng.gauss(0, 1) for _ in range(15)]
        value, p, _ = stats.psi(base, current, 3)
        over_fixed += value > 0.25
        over_p += p < 0.001
    assert over_fixed / trials > 0.10
    assert over_p / trials < 0.01


def test_psi_with_a_constant_baseline() -> None:
    value, p, bins = stats.psi([5.0] * 50, [5.0] * 10, 5)
    assert bins == 3 and p > 0.5
    assert stats.psi([5.0] * 50, [9.0] * 10, 5)[1] < 0.001
