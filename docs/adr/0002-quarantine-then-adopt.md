# 2. An anomaly does not become the baseline by itself

- Status: Accepted
- Date: 2026-11-14

## Context

Every monitor compares today with a trailing window. If a bad partition joins that window, it
shifts what "normal" means. A column that vanishes on Monday is, by Tuesday, a column the
baseline partly lacks. In the worst case the monitor fires once and then goes quiet while the
problem continues.

The opposite rule has its own failure: if flagged partitions never join the baseline, a
legitimate lasting change alerts for ever.

## Decision

- A partition with a warning or critical finding is quarantined: profiled, stored and reported,
  but left out of every later baseline.
- If the same finding repeats on 3 consecutive partitions, it is treated as a lasting change.
  Those partitions are adopted into the baseline and the alert is notified as `adopted`.
- An adopted alert closes when its check next passes, with the text "the new baseline was
  adopted". That is a different outcome from "resolved" and is recorded as such.
- Freshness findings do not quarantine. A missing or repeated file says nothing about content.

## Consequences

- A one-day defect is compared against clean history on every following day.
- A real level change costs at least three alerting days. Run with the textbook volume
  settings, the August rise in listed fires alerted on six consecutive days: adoption admits
  the new partitions, but the median only moves once they fill half the window.
- A slow drift that never trips a monitor on any single day is absorbed silently. This design
  does not detect it.
- Three days is a guess. A dataset where a person reviews every alert might prefer manual
  acceptance only.

## Alternatives considered

- **Robust statistics alone, no quarantine**: the median and MAD shrug off one bad day, but the
  schema and null-rate baselines have no robust equivalent.
- **Manual acknowledgement only**: the safest, and the first thing to be ignored at 3 a.m.
