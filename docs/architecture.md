# Architecture

A run checks one dataset for one partition date. It reduces the partition to a profile, compares
the profile with a baseline of earlier profiles, turns the findings into alert transitions and
queues notifications. The backtest calls the same monitor code with altered partitions.

## One run

```mermaid
flowchart LR
    part["Partition<br/>2021-10-05.json.gz<br/>(or nothing)"] --> prof["profile<br/>row count, columns,<br/>types, nulls, numeric values,<br/>newest event, fingerprint"]

    subgraph state["DuckDB state"]
        runs[("runs<br/>profile + evaluation<br/>per date")]
        outbox[("outbox")]
    end

    runs -- "replay earlier runs" --> base["Baseline<br/>earlier profiles,<br/>minus quarantined ones"]
    runs -- "replay earlier runs" --> open["Open alerts"]

    prof --> mon
    base --> mon

    subgraph mon["Monitors (pure functions)"]
        f["freshness"]
        v["volume<br/>median / MAD"]
        s["schema"]
        n["nulls<br/>exact binomial"]
        d["drift<br/>PSI, KS, range"]
    end

    mon --> ev["Evaluation<br/>findings, what was checked,<br/>what was skipped"]
    ev --> step["alerts.step<br/>open, escalate, remind,<br/>adopt, resolve"]
    open --> step
    step --> route["route<br/>one notification<br/>per channel per run"]
    ev -- "same transaction" --> runs
    route -- "same transaction" --> outbox
    outbox --> deliver["deliver<br/>file, webhook<br/>(Idempotency-Key)"]
```

Nothing but `runs` and `outbox` is stored. The baseline and the open alerts are rebuilt by
replaying earlier runs, so they cannot disagree with the history
([ADR 0005](adr/0005-alerts-derived-from-run-history.md)).

## The monitors

| Monitor | Compares | Fires when | Needs |
|---|---|---|---|
| freshness | the date with what arrived; the partition with the previous one | no partition (critical on the second day in a row); records identical to the previous partition; records changed but no event is newer | nothing, or one earlier partition |
| volume | row count with the last 14 baseline partitions | modified z-score at or beyond the cut-off, or zero rows | 7 baseline partitions |
| schema | columns and JSON types with the last 14 baseline partitions | column removed or type changed (critical); column added or first values in an always-null column (info) | 1 baseline partition |
| nulls | each column's null rate with the pooled baseline rate | the rate moved at least 25 points and an exact binomial test gives p < 0.001 | 7 baseline partitions |
| drift | each numeric column's values with the pooled baseline values | a value more than 3 range-widths outside anything seen; or PSI of at least 0.25 with p < 0.001; or a KS test with p < 0.001 | 30 baseline values for the range check; also 7 partitions and 8 values today for the distribution tests |

Every monitor reports which checks it could not run. An evaluation carries three things:
findings, the keys that were really checked, and notes on what was skipped.

## The baseline

```mermaid
stateDiagram-v2
    [*] --> Baseline: partition has no warning or critical finding
    [*] --> Quarantined: partition has one
    Quarantined --> Baseline: the same finding repeats on 3 consecutive partitions (adopted)
    Baseline --> [*]: falls out of the 14-partition window
```

A quarantined partition is stored and reported, but later partitions are not compared with it.
Freshness findings do not quarantine: a late or repeated file says nothing about its content.

## Alert lifecycle

```mermaid
stateDiagram-v2
    [*] --> Open: finding at warning or critical (notify: opened)
    Open --> Open: same finding, same severity (silent)
    Open --> Open: higher severity (notify: escalated)
    Open --> Open: still firing 3 days after the last notification (notify: reminder)
    Open --> Open: run could not check it (silent, stays open)
    Open --> Adopted: finding repeated 3 partitions (notify: adopted)
    Adopted --> [*]: check passes (notify: closed, baseline adopted)
    Open --> [*]: check passes (notify: resolved)
```

Informational findings never open an alert. They are sent once as notices to whichever channel
the routes give them, usually a digest.

## The backtest

```mermaid
flowchart TB
    real["90 real partitions"] --> replay["Replay in order<br/>(same monitors, same baseline rules)"]
    replay --> hist["Section 1: every finding<br/>on unmodified history"]
    replay --> quiet["Quiet days with 14+ baseline partitions"]
    cat["Catalogue of 31 defects"] --> trial
    quiet --> trial["For each day x defect:<br/>alter that one day in memory,<br/>evaluate against its real history"]
    trial --> score["Caught = a warning or critical finding<br/>the unaltered day did not have"]
    score --> table["Section 2: catch rate per defect"]
    replay --> sweep["Section 3: nine volume settings,<br/>tuning period and holdout"]
```

Each trial is independent. The altered day never enters any baseline, so one defect cannot hide
or reveal another, and the catch rate of each defect can be read on its own.
