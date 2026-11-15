# 1. Monitors are pure functions of partition profiles

- Status: Accepted
- Date: 2026-11-14

## Context

A monitor needs today's partition and some history. If each monitor queries storage for both,
then the backtest needs a database full of altered partitions, a monitor's result depends on
when it ran, and two monitors can see different history.

## Decision

- A partition is reduced once to a `Profile`: row count, each column's JSON types, null count
  and numeric values, the newest event timestamp and a fingerprint of the records. The rows are
  not kept.
- Each monitor is a function of `(config, profile, history of profiles)`. It reads no storage
  and no clock.
- Every evaluation returns three things: findings, the keys it really checked, and notes on
  what it skipped and why.
- Integers and floats are one type, `number`. JSON does not distinguish them, and a latitude of
  exactly 39 is not a schema change.

## Consequences

- The whole backtest (2,198 trials plus the nine-setting sweep) runs in about 10 seconds,
  because a trial is one profile and one function call.
- Production and the backtest cannot drift apart: there is one implementation.
- "Skipped" is a first-class result. A column with 3 values a day shows up as unmonitored
  instead of as healthy.
- A profile keeps every numeric value, which is fine at 20 rows a day and wrong at 20 million.
  A large table needs a sketch or a fixed-size sample in its place; the monitors would not
  change.

## Alternatives considered

- **SQL checks against the warehouse**: the natural fit for big tables, but each check then
  needs a live database to test, and history is whatever the table holds today.
- **Keeping raw rows for the window**: the monitors would have more to work with, at the cost
  of storing a second copy of the data.
