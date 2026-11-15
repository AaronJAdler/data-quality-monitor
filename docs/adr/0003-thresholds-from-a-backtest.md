# 3. Thresholds come from a backtest with a holdout period

- Status: Accepted
- Date: 2026-11-14

## Context

The volume monitor uses the modified z-score, `0.6745 * (rows - median) / MAD`, with a floor on
the MAD so a flat history cannot make a one-row change infinite. The textbook cut-off is 3.5
(Iglewicz and Hoaglin). With that cut-off and a floor of 1 row, the monitor fired on 9 of 90
unmodified days, all in August, when the list of fires really did grow and shrink.

Picking a quieter setting by looking at those same 90 days would fit the threshold to the
answer.

## Decision

- The backtest scores nine settings (floors of 1, 2 and 3 rows; cut-offs of 2.5, 3.5 and 5) on
  two periods: tuning (through 2021-08-31) and holdout (after it).
- All settings are scored on the same trial days. A day where a setting already alerts gives it
  no credit for an injected defect.
- The selection rule is code (`backtest.volume_sweep`): among settings with at most 1 alert day
  on unmodified tuning data, take the highest mean catch rate on tuning data.
- `datasets/ca_fires.yaml` uses what the rule selects: a floor of 2 rows and a cut-off of 2.5.
  A test fails if the configured setting and the selected one differ.
- Every other monitor keeps its defaults. Their findings on unmodified data are few, and each
  one is listed and explained in the README.

## Consequences

- On holdout the selected setting had 0 alert days of 42 and caught 64% of half-lost loads, all
  of the 80% losses and all of the doubled loads.
- On holdout the textbook setting was also quiet (0 of 42) and caught more half-lost loads
  (83%). The holdout period had no surge, so it could not show the textbook setting's weakness.
  The selected setting is kept because the rule was written before the holdout numbers were
  read, and tuning is the only period with a surge in it.

## Caveats

- I saw the whole-season sweep before splitting it, and wrote the rule after that. The rule is
  simple and was not adjusted afterwards, but it was not written blind.
- 40 tuning days and 42 holdout days from one fire season is a small sample. The honest summary
  is that both settings are defensible and the data cannot separate them.
- A threshold fitted to 2021 says nothing certain about a year with a different season.

## Alternatives considered

- **Keep the textbook cut-off**: no tuning to defend, but 9 alert days in 90 from real
  movement would train people to ignore the monitor.
- **A Poisson floor on the MAD**: principled for counts, but these counts are sticky (the same
  fires are listed day after day), so it overstates the noise and misses even 80% losses.
