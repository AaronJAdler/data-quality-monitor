# 4. Drift: PSI with a p-value, a KS test, and a range check

- Status: Accepted
- Date: 2026-11-14

## Context

The population stability index is usually read against fixed cut-offs: 0.1 is a small shift,
0.25 a large one. Those cut-offs assume large samples. PSI grows with sampling noise: with no
drift at all, `PSI / (1/n + 1/m)` is approximately chi-square with `bins - 1` degrees of freedom
(Yurdakul, 2018). At 15 values a day and 3 bins, a test in this repository shows PSI passing
0.25 by chance in more than 10% of trials.

The real feed also has a defect no distribution test can see: one longitude of -11846461 among
a dozen normal ones. One value in twelve barely moves a distribution.

## Decision

- **PSI needs both** a p-value under 0.001 from the chi-square approximation and a value of at
  least 0.25. The p-value handles small samples; the floor stops huge samples alerting on
  shifts too small to matter.
- Bins are the baseline's quantiles, at most 10, and no more than one per 5 values in today's
  partition. Each share is smoothed by half a count.
- **A two-sample KS test** runs beside it at the same p-value. KS sees shifts inside a bin that
  PSI cannot. Either test firing is a warning; both is critical.
- **A range check** flags any value more than 3 range-widths outside everything in the
  baseline. It needs 30 baseline values and works on a single row.
- Below 8 values in the partition or 30 in the baseline, the distribution tests report
  `skipped` with the counts.
- The statistics are written out in `stats.py` and checked against scipy in the tests. scipy is
  a development dependency only.

## Consequences

- No distribution alert fired on 90 unmodified days.
- Swapped coordinates, a lost sign and the absurd longitude were caught every time; a 2-degree
  shift in latitude about half the time; a 0.5-degree shift never.
- The range check caught the real 2021-07-20 longitude on day 6, with 5 partitions of history.
- 11 of 16 numeric columns never had enough values for the distribution tests.

## Caveats

- Both p-values assume independent samples. This feed lists the same fires every day, so the
  baseline's 200 values are perhaps 20 fires repeated. The tests are therefore used as
  calibrated alarms, judged by the backtest, and not as probabilities.
- The range check was added because Project 1 had already found the bad longitude. It is a
  sensible check in general, but it was not designed blind.
- A unit change on a column spanning five orders of magnitude (acres to hectares) is invisible
  to all three checks. That needs a per-entity rule.

## Alternatives considered

- **PSI with the fixed 0.25 cut-off**: familiar, and wrong at this sample size.
- **KS alone**: weak on heavily tied or discrete columns.
- **scipy at runtime**: fewer lines here, a large dependency for four functions.
