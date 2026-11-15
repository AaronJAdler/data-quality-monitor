"""Backtest: what do the monitors say about real history, and which injected defects do they catch?

Two questions, answered separately:

1. Unmodified history. Every partition is checked in order, exactly as in production. Whatever
   fires here is either a real problem in the source or noise; both are listed in full.
2. Injected defects. For each normal day and each anomaly in the catalogue, that one day is
   altered, checked against the real history before it, and the alteration discarded. A defect
   counts as caught when the altered day raises a warning or critical finding that the
   unaltered day did not.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dqmon.baseline import Baseline
from dqmon.config import DatasetConfig, Monitors
from dqmon.inject import Anomaly, Records, catalogue
from dqmon.models import Evaluation, Finding, Profile, Severity
from dqmon.monitors import evaluate
from dqmon.profile import list_partitions, profile_records, read_partition

SEED = 20211015
WARMUP = 14  # partitions of history a day needs before it is used for trials


@dataclass(frozen=True)
class Day:
    date: dt.date
    records: Records | None
    evaluation: Evaluation
    adopted: frozenset[str]
    # What the monitors could see when this day was checked:
    history: tuple[Profile, ...]
    previous: Profile | None
    previous_records: Records | None
    missing_before: int


@dataclass(frozen=True)
class Trial:
    anomaly: str
    date: dt.date
    by_expected: bool
    by_any: bool
    new_keys: tuple[str, ...]


@dataclass(frozen=True)
class Result:
    dataset: str
    days: tuple[Day, ...]
    trial_dates: tuple[dt.date, ...]
    anomalies: tuple[Anomaly, ...]
    trials: tuple[Trial, ...]


def _profile(cfg: DatasetConfig, date: dt.date, records: Records) -> Profile:
    return profile_records(
        cfg.dataset, date, records, event_time_column=cfg.source.event_time_column
    )


def _alerting(findings: Sequence[Finding]) -> set[str]:
    return {f.key for f in findings if f.severity >= Severity.WARNING}


def replay_history(cfg: DatasetConfig, monitors: Monitors) -> tuple[Day, ...]:
    """Check every calendar day from the first partition to the last, in order."""
    partitions = list_partitions(cfg.source)
    baseline = Baseline()
    days: list[Day] = []
    previous_records: Records | None = None
    date = partitions[0]
    while date <= partitions[-1]:
        loaded = read_partition(cfg.source, date)
        records = loaded[0] if loaded else None
        profile = _profile(cfg, date, records) if records is not None else None
        history = tuple(baseline.history())
        missing_before = baseline.missing_streak
        previous = baseline.previous
        evaluation = evaluate(
            monitors,
            cfg.dataset,
            date,
            profile,
            history,
            previous=previous,
            missing_streak=missing_before + 1,
        )
        adopted = baseline.record(profile, evaluation)
        days.append(
            Day(
                date, records, evaluation, adopted, history, previous, previous_records,
                missing_before,
            )
        )  # fmt: skip
        if records is not None:
            previous_records = records
        date += dt.timedelta(days=1)
    return tuple(days)


def run_trials(
    cfg: DatasetConfig,
    monitors: Monitors,
    days: Sequence[Day],
    anomalies: Sequence[Anomaly],
    *,
    require_quiet: bool = True,
) -> tuple[tuple[dt.date, ...], tuple[Trial, ...]]:
    """Inject each anomaly into each eligible day.

    With `require_quiet`, days that already have a warning or critical finding are left out.
    The sweep turns that off so every setting is scored on the same days.
    """
    eligible = [
        d
        for d in days
        if d.records is not None
        and d.previous_records is not None
        and len(d.history) >= WARMUP
        and not (require_quiet and _alerting(d.evaluation.findings))
    ]
    trials: list[Trial] = []
    for anomaly in anomalies:
        for day in eligible:
            assert day.records is not None and day.previous_records is not None  # noqa: S101
            rng = random.Random(f"{SEED}/{anomaly.name}/{day.date.isoformat()}")
            altered = anomaly.apply(day.records, day.previous_records, rng)
            if altered == day.records:
                continue  # nothing to alter that day (e.g. no non-null value to rescale)
            profile = _profile(cfg, day.date, altered) if altered is not None else None
            evaluation = evaluate(
                monitors,
                cfg.dataset,
                day.date,
                profile,
                day.history,
                previous=day.previous,
                missing_streak=day.missing_before + 1,
            )
            new = sorted(_alerting(evaluation.findings) - _alerting(day.evaluation.findings))
            trials.append(
                Trial(
                    anomaly=anomaly.name,
                    date=day.date,
                    by_expected=any(k.startswith(e) for k in new for e in anomaly.expected),
                    by_any=bool(new),
                    new_keys=tuple(new),
                )
            )
    return tuple(d.date for d in eligible), tuple(trials)


def run(cfg: DatasetConfig, monitors: Monitors | None = None) -> Result:
    monitors = monitors or cfg.monitors
    days = replay_history(cfg, monitors)
    anomalies = tuple(catalogue())
    trial_dates, trials = run_trials(cfg, monitors, days, anomalies)
    return Result(cfg.dataset, days, trial_dates, anomalies, trials)


# ---------------------------------------------------------------------------------------------
# Summaries


def _rate(trials: Sequence[Trial], attribute: str) -> tuple[int, int]:
    return sum(1 for t in trials if getattr(t, attribute)), len(trials)


TUNING_END = dt.date(2021, 8, 31)
SWEEP_ANOMALIES = (
    "drop_rows_30",
    "drop_rows_50",
    "drop_rows_80",
    "drop_rows_100",
    "duplicate_rows_50",
    "duplicate_rows_100",
)
MAX_TUNING_ALERT_DAYS = 1


def volume_sweep(cfg: DatasetConfig) -> dict[str, Any]:
    """The volume monitor's trade-off: alerts on unmodified days against defects caught.

    Every setting is scored twice: on a tuning period (through August 31) and on a holdout
    period (September 1 onward). A setting is selected from the tuning numbers alone, by a fixed
    rule, so its holdout numbers are an honest estimate rather than a fitted one.
    """
    anomalies = [a for a in catalogue() if a.name in SWEEP_ANOMALIES]
    rows: list[dict[str, Any]] = []
    for min_mad in (1.0, 2.0, 3.0):
        for z in (2.5, 3.5, 5.0):
            volume = cfg.monitors.volume.model_copy(
                update={"z_warning": z, "min_mad_rows": min_mad, "z_critical": max(7.0, z)}
            )
            monitors = cfg.monitors.model_copy(update={"volume": volume})
            days = replay_history(cfg, monitors)
            _, trials = run_trials(cfg, monitors, days, anomalies, require_quiet=False)
            row: dict[str, Any] = {"min_mad_rows": min_mad, "z_warning": z}
            for period in ("tuning", "holdout"):

                def keep(d: dt.date, period: str = period) -> bool:
                    return (d <= TUNING_END) == (period == "tuning")

                checked = [
                    d for d in days if keep(d.date) and "volume/row_count/-" in d.evaluation.checked
                ]
                alerting = [
                    d for d in checked if "volume/row_count/-" in _alerting(d.evaluation.findings)
                ]
                caught = {}
                for anomaly in anomalies:
                    mine = [t for t in trials if t.anomaly == anomaly.name and keep(t.date)]
                    caught[anomaly.name] = list(_rate(mine, "by_expected"))
                row[period] = {
                    "alert_days": len(alerting),
                    "days": len(checked),
                    "caught": caught,
                }
            rows.append(row)

    def mean_caught(row: dict[str, Any], period: str) -> float:
        rates = [hit / total for hit, total in row[period]["caught"].values() if total]
        return float(sum(rates) / len(rates))

    quiet = [r for r in rows if r["tuning"]["alert_days"] <= MAX_TUNING_ALERT_DAYS]
    selected = max(quiet, key=lambda r: mean_caught(r, "tuning")) if quiet else None
    return {
        "tuning_end": TUNING_END.isoformat(),
        "rule": (
            f"at most {MAX_TUNING_ALERT_DAYS} alert day on unmodified tuning data, then the "
            "highest mean catch rate on tuning data"
        ),
        "rows": rows,
        "selected": (
            {"min_mad_rows": selected["min_mad_rows"], "z_warning": selected["z_warning"]}
            if selected
            else None
        ),
        "configured": {
            "min_mad_rows": cfg.monitors.volume.min_mad_rows,
            "z_warning": cfg.monitors.volume.z_warning,
        },
    }


def drift_coverage(result: Result) -> list[dict[str, Any]]:
    """For each numeric column, on how many days the distribution check could run at all."""
    with_data = [d for d in result.days if d.records is not None and len(d.history) >= WARMUP]
    columns: dict[str, list[int]] = {}
    for day in with_data:
        assert day.records is not None  # noqa: S101
        names = {k for r in day.records for k, v in r.items() if isinstance(v, int | float)}
        for name in names:
            values = sum(
                1
                for r in day.records
                if isinstance(r.get(name), int | float) and not isinstance(r.get(name), bool)
            )
            if values:
                columns.setdefault(name, []).append(values)
    rows = []
    for name, counts in sorted(columns.items()):
        checked = sum(1 for d in with_data if f"drift/distribution/{name}" in d.evaluation.checked)
        rows.append(
            {
                "column": name,
                "median_values_per_day": sorted(counts)[len(counts) // 2],
                "days_checked": checked,
                "days": len(with_data),
            }
        )
    return rows


def summarise(cfg: DatasetConfig, result: Result) -> dict[str, Any]:
    arrived = [d for d in result.days if d.records is not None]
    history_findings = [
        {
            "date": d.date.isoformat(),
            "severity": f.severity.label,
            "finding": f.key,
            "observed": f.observed,
            "expected": f.expected,
            "detail": f.detail,
        }
        for d in result.days
        for f in d.evaluation.findings
    ]
    per_anomaly = []
    for anomaly in result.anomalies:
        mine = [t for t in result.trials if t.anomaly == anomaly.name]
        expected, total = _rate(mine, "by_expected")
        anyone, _ = _rate(mine, "by_any")
        other: dict[str, int] = {}
        for t in mine:
            if t.by_any and not t.by_expected:
                for key in t.new_keys:
                    other[key] = other.get(key, 0) + 1
        per_anomaly.append(
            {
                "anomaly": anomaly.name,
                "family": anomaly.family,
                "description": anomaly.description,
                "trials": total,
                "caught_by_expected_monitor": expected,
                "caught_by_any_monitor": anyone,
                "other_monitors": dict(sorted(other.items())),
            }
        )
    return {
        "dataset": result.dataset,
        "first_partition": result.days[0].date.isoformat(),
        "last_partition": result.days[-1].date.isoformat(),
        "calendar_days": len(result.days),
        "partitions": len(arrived),
        "trial_days": len(result.trial_dates),
        "trials": len(result.trials),
        "seed": SEED,
        "history_findings": history_findings,
        "adopted": [
            {"date": d.date.isoformat(), "findings": sorted(d.adopted)}
            for d in result.days
            if d.adopted
        ],
        "anomalies": per_anomaly,
        "volume_sweep": volume_sweep(cfg),
        "drift_coverage": drift_coverage(result),
    }


def _share(hit: int, total: int) -> str:
    return f"{hit} of {total} ({100 * hit / total:.0f}%)" if total else "0 of 0"


def render_markdown(summary: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    add("# Backtest results")
    add("")
    add(
        "Generated by `uv run dqmon backtest`. Do not edit by hand: CI regenerates this file and "
        "fails if it differs."
    )
    add("")
    add(
        f"Dataset `{summary['dataset']}`: {summary['partitions']} real partitions over "
        f"{summary['calendar_days']} calendar days, {summary['first_partition']} to "
        f"{summary['last_partition']}."
    )
    add("")
    add("## 1. Unmodified history")
    add("")
    serious = [f for f in summary["history_findings"] if f["severity"] != "info"]
    info = [f for f in summary["history_findings"] if f["severity"] == "info"]
    add(
        f"Checking the real partitions in order raised {len(serious)} warning or critical "
        f"findings and {len(info)} informational ones. Nothing was injected here."
    )
    add("")
    add("| Date | Severity | Finding | Observed | Expected |")
    add("|---|---|---|---|---|")
    for f in serious:
        add(
            f"| {f['date']} | {f['severity']} | `{f['finding']}` | {f['observed']}; {f['detail']} "
            f"| {f['expected']} |"
        )
    add("")
    if summary["adopted"]:
        add("Findings that repeated for 3 partitions and were adopted as the new baseline:")
        add("")
        for a in summary["adopted"]:
            add(f"- {a['date']}: {', '.join(f'`{k}`' for k in a['findings'])}")
        add("")
    counts: dict[str, int] = {}
    for f in info:
        counts[f["finding"]] = counts.get(f["finding"], 0) + 1
    if counts:
        listed = ", ".join(f"`{k}` x{v}" for k, v in sorted(counts.items()))
        add(f"Informational findings: {listed}.")
        add("")
    add("## 2. Injected defects")
    add("")
    add(
        f"{summary['trials']} trials: each of {len(summary['anomalies'])} defects injected into "
        f"each of {summary['trial_days']} days. A day qualifies when it has a partition, at least "
        f"{WARMUP} partitions of baseline, and no warning or critical finding of its own. Row "
        f"choices are seeded ({summary['seed']})."
    )
    add("")
    add("| Defect | Injected | Caught by the intended monitor | Caught by any monitor |")
    add("|---|---|---|---|")
    for a in summary["anomalies"]:
        add(
            f"| {a['family']} | {a['description']} "
            f"| {_share(a['caught_by_expected_monitor'], a['trials'])} "
            f"| {_share(a['caught_by_any_monitor'], a['trials'])} |"
        )
    add("")
    others = [a for a in summary["anomalies"] if a["other_monitors"]]
    if others:
        add("Where a different monitor caught the defect:")
        add("")
        for a in others:
            listed = ", ".join(f"`{k}` x{v}" for k, v in a["other_monitors"].items())
            add(f"- {a['description']}: {listed}")
        add("")
    add("## 3. Volume monitor: choosing thresholds without fitting them to the answer")
    add("")
    sweep = summary["volume_sweep"]
    add(
        "The same backtest with nine volume settings, scored separately on a tuning period "
        f"(through {sweep['tuning_end']}) and a holdout period (after it). Every setting is "
        "scored on the same days. `Alert days` counts unmodified days with a volume finding; the "
        "other columns are the share of injected defects caught."
    )
    add("")
    labels = {
        "drop_rows_30": "-30% rows",
        "drop_rows_50": "-50%",
        "drop_rows_80": "-80%",
        "drop_rows_100": "empty",
        "duplicate_rows_50": "+50%",
        "duplicate_rows_100": "+100%",
    }
    for period, title in (("tuning", "Tuning period"), ("holdout", "Holdout period")):
        add(f"**{title}**")
        add("")
        add("| MAD floor (rows) | z cut-off | Alert days | " + " | ".join(labels.values()) + " |")
        add("|---|---|---|" + "---|" * len(labels))
        for row in sweep["rows"]:
            cells = " | ".join(
                f"{100 * row[period]['caught'][n][0] / row[period]['caught'][n][1]:.0f}%"
                for n in labels
            )
            chosen = sweep["selected"] and all(
                row[k] == sweep["selected"][k] for k in ("min_mad_rows", "z_warning")
            )
            mark = " (selected)" if chosen else ""
            add(
                f"| {row['min_mad_rows']:g} | {row['z_warning']:g}{mark} "
                f"| {row[period]['alert_days']} of {row[period]['days']} | {cells} |"
            )
        add("")
    if sweep["selected"]:
        add(
            f"Selection rule, applied to the tuning table only: {sweep['rule']}. It selects a MAD "
            f"floor of {sweep['selected']['min_mad_rows']:g} rows and a z cut-off of "
            f"{sweep['selected']['z_warning']:g}. `datasets/ca_fires.yaml` is configured with a "
            f"floor of {sweep['configured']['min_mad_rows']:g} and a cut-off of "
            f"{sweep['configured']['z_warning']:g}."
        )
        add("")
    add("## 4. Drift monitor: where the distribution check can run")
    add("")
    add(
        "The distribution check needs enough non-null values in the day's partition. Columns "
        "below that minimum are reported as skipped, not as healthy."
    )
    add("")
    add("| Column | Median non-null values per day | Days the check ran |")
    add("|---|---|---|")
    for row in summary["drift_coverage"]:
        add(
            f"| {row['column']} | {row['median_values_per_day']} "
            f"| {row['days_checked']} of {row['days']} |"
        )
    add("")
    return "\n".join(out)
