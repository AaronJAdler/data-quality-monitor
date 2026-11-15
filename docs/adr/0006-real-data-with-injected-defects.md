# 6. Real data with injected defects as the test oracle

- Status: Accepted
- Date: 2026-11-14

## Context

A monitor can be wrong in two directions, and each needs different evidence. False alarms can
only be measured on data where nothing is wrong, and generated data is too clean to produce
them. Missed defects can only be measured where the truth is known, and real history has too
few known defects: this feed has two.

## Decision

- **False alarms are measured on real, unmodified history.** All 90 partitions of CAL FIRE's
  2021 feed are replayed in order. Every finding is listed in the report, and the README says
  what each one turned out to be.
- **Missed defects are measured by injection.** A catalogue of 31 defects, each a thing
  pipelines really do: a truncated or doubled load, a dropped or renamed column, a changed
  type, nulls, a default leaking through, swapped columns, a unit change.
- Each defect is injected into each quiet day (a partition, 14 baseline partitions, no finding
  of its own), one at a time, and evaluated against the real history before it.
- A defect counts as caught when the altered day has a warning or critical finding the
  unaltered day did not. The report separates "caught by the intended monitor" from "caught by
  any monitor".
- Defects come in sizes (30%, 50%, 80%, 100% of rows) so the result is the smallest size each
  monitor can see, not a single pass or fail.
- Row choices are seeded from the defect's name and the date. The report is committed, and a
  test and a CI step fail if a fresh run differs from it.
- The known misses are asserted in a test, so the README's limits cannot go stale silently.

## Consequences

- The README can state a catch rate for each defect and size, with the trial count behind it.
- The numbers describe single-day defects on one small feed. They do not transfer to other
  datasets, and they say nothing about slow drift.
- Injected defects are cleaner than real ones. A real truncated load might also lose the newest
  rows, which the freshness monitor would notice.

## Alternatives considered

- **Generated data throughout**: full control, and no honest false-alarm rate.
- **Only the two real defects**: honest, and far too few to compare settings.
