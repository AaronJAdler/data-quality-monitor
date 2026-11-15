"""The statistics the monitors use, written out so each can be read and tested.

Nothing here needs numpy or scipy. The tests compare the Kolmogorov-Smirnov statistic, the
chi-square tail and the binomial tails against scipy.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of no values")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def mad(values: Sequence[float]) -> float:
    """Median absolute deviation, unscaled."""
    centre = median(values)
    return median([abs(v - centre) for v in values])


def robust_z(value: float, history: Sequence[float], min_mad: float) -> float:
    """Modified z-score (Iglewicz and Hoaglin): 0.6745 * (x - median) / MAD.

    `min_mad` stops a flat history (MAD of 0) from turning a one-row change into an infinite
    score.
    """
    centre = median(history)
    spread = max(mad(history), min_mad)
    return 0.6745 * (value - centre) / spread


def _log_binom_pmf(k: int, n: int, p: float) -> float:
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def binom_tails(k: int, n: int, p: float) -> tuple[float, float]:
    """Exact P(X <= k) and P(X >= k) for X ~ Binomial(n, p)."""
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    if p <= 0.0:
        return 1.0, 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0, 1.0
    pmf = [math.exp(_log_binom_pmf(i, n, p)) for i in range(n + 1)]
    return min(1.0, math.fsum(pmf[: k + 1])), min(1.0, math.fsum(pmf[k:]))


def chi2_sf(x: float, df: int) -> float:
    """Upper tail of the chi-square distribution: the regularised incomplete gamma Q(df/2, x/2)."""
    if df <= 0:
        raise ValueError("df must be positive")
    if x <= 0:
        return 1.0
    a, half = df / 2, x / 2
    log_prefix = -half + a * math.log(half) - math.lgamma(a)
    if half < a + 1:
        # Series for P(a, x); Q = 1 - P.
        term = total = 1.0 / a
        n = a
        for _ in range(500):
            n += 1
            term *= half / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefix))
    # Continued fraction for Q(a, x) (modified Lentz).
    tiny = 1e-300
    b = half + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return min(1.0, h * math.exp(log_prefix))


def ks_2samp(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Two-sample Kolmogorov-Smirnov statistic D and its asymptotic two-sided p-value.

    The p-value uses the Kolmogorov series with Stephens' small-sample correction. It assumes
    independent samples; see the drift monitor for why the monitors do not rely on it alone.
    """
    if not a or not b:
        raise ValueError("both samples need at least one value")
    xs, ys = sorted(a), sorted(b)
    n, m = len(xs), len(ys)
    d = 0.0
    for v in sorted(set(xs) | set(ys)):
        d = max(d, abs(bisect.bisect_right(xs, v) / n - bisect.bisect_right(ys, v) / m))
    en = math.sqrt(n * m / (n + m))
    lam = (en + 0.12 + 0.11 / en) * d
    if lam < 0.2:
        return d, 1.0
    p = 2 * math.fsum((-1) ** (j - 1) * math.exp(-2 * j * j * lam * lam) for j in range(1, 101))
    return d, min(1.0, max(0.0, p))


def quantile_edges(baseline: Sequence[float], bins: int) -> list[float]:
    """Interior bin edges at the baseline's quantiles, with repeated edges removed."""
    ordered = sorted(baseline)
    edges: list[float] = []
    for i in range(1, bins):
        edge = ordered[min(len(ordered) - 1, (i * len(ordered)) // bins)]
        if not edges or edge > edges[-1]:
            edges.append(edge)
    return edges


def psi(baseline: Sequence[float], current: Sequence[float], bins: int) -> tuple[float, float, int]:
    """Population stability index, its p-value and the number of bins actually used.

    Bins are the baseline's quantiles, open-ended at both ends. Each proportion is smoothed by
    half a count so an empty bin cannot produce an infinite value.

    A fixed cut-off such as 0.25 means little for small samples, because PSI grows with
    sampling noise alone: under no drift, PSI / (1/n + 1/m) is approximately chi-square with
    (bins - 1) degrees of freedom (Yurdakul, 2018). The p-value comes from that approximation.
    """
    if not baseline or not current:
        raise ValueError("both samples need at least one value")
    edges = quantile_edges(baseline, bins)
    if min(baseline) == max(baseline):
        # Every baseline value is the same: split into below, equal and above.
        edges = [baseline[0], math.nextafter(baseline[0], math.inf)]
    used = len(edges) + 1

    def shares(values: Sequence[float]) -> list[float]:
        counts = [0] * used
        for v in values:
            counts[bisect.bisect_right(edges, v)] += 1
        return [(c + 0.5) / (len(values) + 0.5 * used) for c in counts]

    p, q = shares(baseline), shares(current)
    value = math.fsum((qi - pi) * math.log(qi / pi) for pi, qi in zip(p, q, strict=True))
    scale = 1 / len(baseline) + 1 / len(current)
    return value, chi2_sf(value / scale, used - 1), used
