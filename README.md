# data-quality-monitor

[![CI](https://github.com/AaronJAdler/data-quality-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/AaronJAdler/data-quality-monitor/actions/workflows/ci.yml)

Five data quality monitors (freshness, volume, schema, null rate, distribution drift) with alert
routing, and a backtest that measures what they catch. The backtest runs on 90 real daily
snapshots of CAL FIRE's 2021 incident feed, the same data as
[ca-wildfire-elt](https://github.com/AaronJAdler/ca-wildfire-elt): first unmodified, then with
31 kinds of defect injected one day at a time.

**[Full backtest results](docs/backtest.md)** (generated; CI fails if the file is out of date)

## What it demonstrates

- **Monitors that are measured, not assumed.** Each one is scored on real history: how often it
  fires when nothing is wrong, and which injected defects it catches at which size.
- **Statistics that hold up on small partitions.** A modified z-score on median and MAD for
  volume; an exact binomial test for null rates; PSI with a chi-square p-value instead of the
  usual fixed 0.25 cut-off, which is noise at 15 rows a day.
- **Thresholds chosen on one period and judged on another**, by a rule written into the code.
- **Honest blind spots.** The checks that cannot run say "skipped", and the defects the monitors
  miss are in the results table and pinned by a test.
- **Alerts as a lifecycle, not a stream of findings.** One alert per problem: opened, escalated,
  reminded, resolved. A run that could not check something never closes its alert.
- **Idempotent notifications.** A transactional outbox with ids derived from the events, sent as
  `Idempotency-Key`; rerunning a day notifies nobody twice.

## Results

### On the real, unmodified feed

93 calendar days, 90 partitions. The monitors raised 8 warning or critical findings, which are
6 alerts:

| Date | Alert | What it was |
|---|---|---|
| 2021-07-20 | Longitude out of range: -11846461 | A real defect. The Peak Fire's longitude was published without its decimal point. |
| 2021-08-04 | Volume: 19 rows against a median of 10 | Real, but not a defect. The list grew from 12 fires to 19 in three days. |
| 2021-10-03 | No event newer than the previous partition's | One fire left the list and nothing else changed. The feed went silent the next day. |
| 2021-10-04 to 06 | Partition missing: warning, then critical | A real outage. The source recorded nothing for three days. One alert: opened, escalated on day 2, resolved on October 7. |
| 2021-10-13 | No event newer than the previous partition's | The most recently updated fire left the list, so the newest timestamp moved backwards. Benign. |
| 2021-10-15 | Records identical to the day before | End of season; nothing changed. Benign. |

So: two real source defects found, one real change in the world, and three warnings from a quiet
feed. The null-rate and distribution checks raised nothing on unmodified data.

One more finding came from building the freshness monitor rather than from an alert. `Updated`
carries a `Z` suffix, but across all 90 partitions the newest `Updated` is never less than 7.00
hours older than the snapshot (minimum 7.001 hours). That is what Pacific Daylight Time
labelled as UTC looks like. The freshness monitor therefore compares event times with each other
and never with the clock. A test asserts the 7-hour floor.

### On injected defects

2,198 trials: each defect injected into each of 71 quiet days (70 for the acreage ones, where
one day has no acreage to alter), checked against the real history before that day. Caught by
the intended monitor: 1,858.

| Defect | Caught |
|---|---|
| Partition missing; yesterday's file delivered again; empty partition | 100% |
| Column removed or renamed; type changed (number to text, list to string, boolean to text) | 100% |
| 30%, 60% or 100% of a populated column goes null | 100% |
| Nulls replaced with 0 in a sparse column | 100% |
| Latitude and longitude swapped; longitude sign lost; one absurd coordinate | 100% |
| All rows loaded twice | 94% |
| 80% of rows lost | 89% |
| AcresBurned multiplied by 1000 / by 10 | 81% / 80% |
| Every latitude moved 2 degrees north | 56% |
| 50% of rows lost | 54% |
| 50% of rows loaded twice | 46% |
| 30% of rows lost | 18% |
| Every latitude moved 0.5 degrees north | **0%** |
| AcresBurned in hectares instead of acres (multiplied by 0.4047) | **0%** |

What the misses say:

- **Volume on a 10 to 20 row table is a blunt instrument.** The feed's own day-to-day movement
  can be as large as a 30% loss (20 fires one day, 15 the next), so the monitor cannot tell them
  apart. It reliably sees a
  partition that is empty, nearly empty or doubled.
- **A unit change is invisible to a distribution test when the column spans five orders of
  magnitude.** Acreage runs from 10 to over 900,000; multiplying by 0.4 moves every value
  less than the gap between neighbouring fires. Catching it needs a per-entity rule (a fire's
  acreage does not fall 60% overnight), which is what Project 1's row-level flags do.
- **The acreage misses at 10 and 1000 times are not misses of judgment.** On all 13 of those
  days the 14-day baseline held fewer than 30 non-null acreage values, so the check did not run
  and said so.
- **Sparse columns are mostly unmonitored for drift.** 11 of the 16 numeric columns never had
  the 8 values a day the distribution check needs, and acreage had them on 7 days of 75.
  [Section 4](docs/backtest.md) lists them.

### Choosing the volume threshold

With the textbook settings (MAD floor of 1 row, cut-off 3.5) the volume monitor fired on 9
unmodified days out of 90, all in August, as the list of fires grew and then shrank. Nine settings were scored on a
tuning period (through August 31) and a holdout period (September 1 on). The rule, applied to
the tuning numbers only: at most 1 alert day on unmodified data, then the highest mean catch
rate. It selected a floor of 2 rows and a cut-off of 2.5.

| | Alert days, unmodified | 50% of rows lost | 80% lost | All rows doubled |
|---|---|---|---|---|
| Selected, tuning period | 1 of 40 | 33% | 73% | 85% |
| Selected, holdout | 0 of 42 | 64% | 100% | 100% |
| Textbook, holdout | 0 of 42 | 83% | 98% | 100% |

The holdout period was calm, and on it the textbook setting would have been just as quiet and
caught more half-lost loads. I kept the selected setting because the rule was fixed before the
holdout numbers were read, and because the tuning period is the only evidence of how each
setting behaves during a surge. One season is too little to settle it.
[ADR 0003](docs/adr/0003-thresholds-from-a-backtest.md) has the caveats, including that I had
seen the whole-season sweep before I split it.

## Run it

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/). Works on Windows, macOS and Linux.

```powershell
uv sync
uv run pytest             # 68 tests, about 15 seconds
uv run dqmon backtest     # rewrites docs/backtest.md and docs/backtest.json
uv run dqmon replay       # every day through the production path; prints alert transitions
uv run dqmon run --date 2021-10-05   # one day, with state in state/dqmon.duckdb
```

`replay` on the real feed prints, among others:

```
== 2021-10-04
   opened    warning  freshness/missing_partition/-: no partition for 2021-10-04; ...
== 2021-10-05
   escalated critical freshness/missing_partition/-: no partition for 2021-10-05; ...
== 2021-10-07
   resolved  critical freshness/missing_partition/-: check passed
```

October 6 prints nothing: the alert is already open at critical, so nobody is paged again.

A dataset is one YAML file ([datasets/ca_fires.yaml](datasets/ca_fires.yaml)): where the
partitions are, which monitor settings differ from the defaults, and where alerts go.

```yaml
routing:
  channels:
    pager:  {type: webhook, url_env: DQMON_PAGER_URL}
    owners: {type: file, path: out/owners.jsonl}
  routes:
    - match: {severity: [critical]}
      channels: [pager, owners]
    - match: {severity: [warning]}
      channels: [owners]
```

## How it was verified

| Check | Where it ran |
|---|---|
| ruff lint and format, strict mypy | Locally (Linux, Python 3.12.13) |
| 68 tests: each statistic against scipy; each monitor; baseline quarantine and adoption; alert lifecycle and routing; idempotent reruns; webhook retry with the same `Idempotency-Key`; the real outage and the real bad longitude; the backtest report matching a fresh run | Locally |
| Backtest and replay on the 90 real partitions | Locally; the numbers above are copied from that output |
| The same steps in GitHub Actions (lint, types, 68 tests, backtest report unchanged, replay) | Passed on the first push, commit 5d610b8 (Ubuntu 24.04). The badge shows the current state. |
| Webhook delivery | Against a mock transport only. No real endpoint was called. |
| Windows | Not run. Nothing is platform-specific, but it was not tested there. |

## Design

- [Architecture](docs/architecture.md): diagrams of a run, the baseline and the alert lifecycle.
- [ADR 0001](docs/adr/0001-profiles-and-pure-monitors.md): monitors are pure functions of partition profiles
- [ADR 0002](docs/adr/0002-quarantine-then-adopt.md): an anomaly does not become the baseline by itself
- [ADR 0003](docs/adr/0003-thresholds-from-a-backtest.md): thresholds from a backtest with a holdout
- [ADR 0004](docs/adr/0004-drift-tests-for-small-partitions.md): PSI with a p-value, KS, and a range check
- [ADR 0005](docs/adr/0005-alerts-derived-from-run-history.md): alerts derived from run history; missing data never resolves
- [ADR 0006](docs/adr/0006-real-data-with-injected-defects.md): real data with injected defects as the oracle

## Limits

- One dataset, one season, 1,311 rows. The catch rates describe this feed. A table with a
  million rows a day would make the volume and drift monitors far more sensitive and would need
  its own backtest.
- The injected defects last one day. A slow drift over weeks is not tested, and the adoption
  rule (ADR 0002) would absorb a slow enough one.
- Monitors see one partition at a time. They do not follow an entity across days, so they cannot
  see that one fire's acreage fell.
- The source reader handles one layout: a gzipped JSON file per day. Reading a warehouse table
  would mean writing the profile query in SQL; the monitors would not change.
- State is rebuilt from the run history on every run. That is simple and correct for daily
  partitions, and too slow for thousands of partitions a day.

## Data

`data/ca-fires-2021/` is real: CAL FIRE's public incident feed as recorded by
[simonw/ca-fires-history](https://github.com/simonw/ca-fires-history), copied unchanged from
Project 1. See [data/README.md](data/README.md). The injected defects are generated, seeded and
applied in memory; no altered data is stored. The small tables in `tests/conftest.py` are
generated.
