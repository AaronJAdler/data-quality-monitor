"""The five monitors. Each is a pure function of one partition's profile and its history.

`history` is the baseline: earlier partitions, oldest first, with quarantined ones already
removed by the caller (see baseline.py). No monitor touches storage or the clock, so the same
code runs in production and in the backtest.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from dqmon import stats
from dqmon.config import (
    DriftConfig,
    FreshnessConfig,
    Monitors,
    NullsConfig,
    SchemaConfig,
    VolumeConfig,
)
from dqmon.models import Evaluation, Finding, Profile, Severity

Result = tuple[list[Finding], set[str], list[str]]  # findings, checked keys, skipped notes


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def freshness_missing(cfg: FreshnessConfig, partition: dt.date, missing_streak: int) -> Finding:
    severity = (
        Severity.CRITICAL if missing_streak >= cfg.critical_after_missing else Severity.WARNING
    )
    return Finding(
        monitor="freshness",
        kind="missing_partition",
        column=None,
        severity=severity,
        observed=f"no partition for {partition.isoformat()}",
        expected="one partition per day",
        detail=f"{missing_streak} day(s) in a row with no data",
    )


def check_freshness(cfg: FreshnessConfig, profile: Profile, previous: Profile | None) -> Result:
    findings: list[Finding] = []
    checked = {"freshness/missing_partition/-"}
    if previous is None:
        return findings, checked, []
    checked.add("freshness/duplicate_partition/-")
    if profile.rows > 0 and profile.fingerprint == previous.fingerprint:
        findings.append(
            Finding(
                monitor="freshness",
                kind="duplicate_partition",
                column=None,
                severity=Severity.WARNING,
                observed=f"records identical to {previous.partition.isoformat()}",
                expected="new or changed records",
                detail="the file arrived but its content is a copy of the previous partition",
            )
        )
    elif cfg.check_event_time and profile.max_event_ts and previous.max_event_ts:
        checked.add("freshness/stale_events/-")
        if profile.max_event_ts <= previous.max_event_ts:
            findings.append(
                Finding(
                    monitor="freshness",
                    kind="stale_events",
                    column=None,
                    severity=Severity.WARNING,
                    observed=f"newest event {profile.max_event_ts.isoformat()}",
                    expected=f"later than {previous.max_event_ts.isoformat()}",
                    detail="records changed, but no event is newer than the previous partition's",
                )
            )
    return findings, checked, []


def check_volume(cfg: VolumeConfig, profile: Profile, history: Sequence[Profile]) -> Result:
    window = [float(p.rows) for p in history[-cfg.window :]]
    if len(window) < cfg.min_history:
        return [], set(), [f"volume: {len(window)} partitions of history, needs {cfg.min_history}"]
    centre = stats.median(window)
    floor = max(cfg.min_mad_rows, cfg.min_mad_fraction * centre)
    z = stats.robust_z(profile.rows, window, floor)
    findings: list[Finding] = []
    if profile.rows == 0 or abs(z) >= cfg.z_warning:
        critical = profile.rows == 0 or abs(z) >= cfg.z_critical
        change = (profile.rows - centre) / centre if centre else 0.0
        findings.append(
            Finding(
                monitor="volume",
                kind="row_count",
                column=None,
                severity=Severity.CRITICAL if critical else Severity.WARNING,
                observed=f"{profile.rows} rows",
                expected=f"about {centre:g} rows (median of the last {len(window)} partitions)",
                detail=f"{change:+.0%} against the median, robust z = {z:+.1f}",
            )
        )
    return findings, {"volume/row_count/-"}, []


def check_schema(cfg: SchemaConfig, profile: Profile, history: Sequence[Profile]) -> Result:
    window = history[-cfg.window :]
    if not window:
        return [], set(), ["schema: no earlier partition to compare with"]
    baseline: dict[str, set[str]] = {}
    for past in window:
        for name, column in past.columns.items():
            baseline.setdefault(name, set()).update(column.types)
    findings: list[Finding] = []
    if profile.rows == 0:
        # An empty partition has no columns to compare; the volume monitor reports it.
        return findings, set(), ["schema: partition is empty"]
    for name, types in sorted(baseline.items()):
        if name not in profile.columns:
            findings.append(
                Finding(
                    monitor="schema",
                    kind="column_removed",
                    column=name,
                    severity=Severity.CRITICAL,
                    observed="column absent",
                    expected=f"column present ({', '.join(sorted(types)) or 'always null'})",
                    detail="a column consumers may depend on is no longer delivered",
                )
            )
    for name, column in sorted(profile.columns.items()):
        if name not in baseline:
            findings.append(
                Finding(
                    monitor="schema",
                    kind="column_added",
                    column=name,
                    severity=Severity.INFO,
                    observed=f"new column ({', '.join(sorted(column.types)) or 'null'})",
                    expected="no such column",
                    detail="additive change; nothing breaks, but the contract has grown",
                )
            )
            continue
        new_types = set(column.types) - baseline[name]
        if not new_types:
            continue
        if not baseline[name]:
            findings.append(
                Finding(
                    monitor="schema",
                    kind="first_values",
                    column=name,
                    severity=Severity.INFO,
                    observed=f"first non-null values ({', '.join(sorted(new_types))})",
                    expected="always null so far",
                    detail="a column that was always null now carries values",
                )
            )
            continue
        findings.append(
            Finding(
                monitor="schema",
                kind="type_changed",
                column=name,
                severity=Severity.CRITICAL,
                observed=", ".join(sorted(column.types)),
                expected=", ".join(sorted(baseline[name])),
                detail=f"{sum(column.types[t] for t in new_types)} value(s) of a new type",
            )
        )
    return findings, {"schema/"}, []


def check_nulls(cfg: NullsConfig, profile: Profile, history: Sequence[Profile]) -> Result:
    window = history[-cfg.window :]
    if len(window) < cfg.min_history:
        return [], set(), [f"nulls: {len(window)} partitions of history, needs {cfg.min_history}"]
    if profile.rows == 0:
        return [], set(), ["nulls: partition is empty"]
    findings: list[Finding] = []
    for name, column in sorted(profile.columns.items()):
        base_rows = sum(p.rows for p in window if name in p.columns)
        if base_rows == 0:
            continue
        base_nulls = sum(p.columns[name].nulls for p in window if name in p.columns)
        base_rate = base_nulls / base_rows
        rate = column.nulls / profile.rows
        if abs(rate - base_rate) < cfg.min_change:
            continue
        lower, upper = stats.binom_tails(column.nulls, profile.rows, base_rate)
        p_value = upper if rate > base_rate else lower
        if p_value >= cfg.alpha:
            continue
        went_null = rate == 1.0 and base_rate < 0.5
        findings.append(
            Finding(
                monitor="nulls",
                kind="null_rate",
                column=name,
                severity=Severity.CRITICAL if went_null else Severity.WARNING,
                observed=f"{_pct(rate)} null ({column.nulls} of {profile.rows})",
                expected=f"about {_pct(base_rate)} null (last {len(window)} partitions)",
                detail=f"binomial p = {p_value:.2g}",
            )
        )
    return findings, {"nulls/"}, []


def check_drift(cfg: DriftConfig, profile: Profile, history: Sequence[Profile]) -> Result:
    window = history[-cfg.window :]
    findings: list[Finding] = []
    checked: set[str] = set()
    skipped: list[str] = []
    if not window:
        return findings, checked, ["drift: no earlier partition to compare with"]
    for name, column in sorted(profile.columns.items()):
        if cfg.columns is not None and name not in cfg.columns:
            continue
        baseline = [v for p in window if name in p.columns for v in p.columns[name].numeric]
        current = list(column.numeric)
        if not current and not baseline:
            continue
        if len(baseline) < cfg.min_baseline_values:
            if current:
                skipped.append(
                    f"drift/{name}: {len(baseline)} baseline values, "
                    f"needs {cfg.min_baseline_values}"
                )
            continue

        checked.add(f"drift/out_of_range/{name}")
        low, high = min(baseline), max(baseline)
        width = (high - low) or max(abs(high), 1.0)
        fence_low, fence_high = low - cfg.range_factor * width, high + cfg.range_factor * width
        outliers = [v for v in current if v < fence_low or v > fence_high]
        if outliers:
            share = len(outliers) / len(current)
            findings.append(
                Finding(
                    monitor="drift",
                    kind="out_of_range",
                    column=name,
                    severity=Severity.CRITICAL if share >= 0.5 else Severity.WARNING,
                    observed=f"{len(outliers)} of {len(current)} values, e.g. {outliers[0]:g}",
                    expected=f"between {low:g} and {high:g} (last {len(window)} partitions)",
                    detail=f"more than {cfg.range_factor:g} range-widths outside anything seen",
                )
            )

        if len(window) < cfg.min_history:
            skipped.append(
                f"drift/{name}: {len(window)} partitions of history, needs {cfg.min_history}"
            )
            continue
        if len(current) < cfg.min_values:
            skipped.append(f"drift/{name}: {len(current)} values, needs {cfg.min_values}")
            continue
        checked.add(f"drift/distribution/{name}")
        bins = max(2, min(cfg.max_bins, len(current) // cfg.values_per_bin))
        psi, psi_p, used = stats.psi(baseline, current, bins)
        d, ks_p = stats.ks_2samp(baseline, current)
        by_psi = psi_p < cfg.alpha and psi >= cfg.min_psi
        by_ks = ks_p < cfg.alpha
        if by_psi or by_ks:
            findings.append(
                Finding(
                    monitor="drift",
                    kind="distribution",
                    column=name,
                    severity=Severity.CRITICAL if by_psi and by_ks else Severity.WARNING,
                    observed=f"PSI {psi:.2f} over {used} bins, KS D {d:.2f}",
                    expected=f"the distribution of the last {len(window)} partitions",
                    detail=(
                        f"PSI p = {psi_p:.2g}, KS p = {ks_p:.2g}; "
                        f"{len(current)} values against {len(baseline)}"
                    ),
                )
            )
    return findings, checked, skipped


def evaluate(
    monitors: Monitors,
    dataset: str,
    partition: dt.date,
    profile: Profile | None,
    history: Sequence[Profile],
    *,
    previous: Profile | None = None,
    missing_streak: int = 0,
) -> Evaluation:
    """Check one partition date.

    `profile` is None when no partition arrived. `previous` is the latest partition that did
    arrive, quarantined or not. `missing_streak` counts this date and the missing dates
    directly before it.
    """
    if profile is None:
        if not monitors.freshness.enabled:
            return Evaluation(dataset, partition, ())
        finding = freshness_missing(monitors.freshness, partition, max(1, missing_streak))
        return Evaluation(
            dataset,
            partition,
            (finding,),
            checked=frozenset({finding.key}),
            skipped=("volume, schema, nulls and drift: no partition to check",),
        )

    results: list[Result] = []
    if monitors.freshness.enabled:
        results.append(check_freshness(monitors.freshness, profile, previous))
    if monitors.volume.enabled:
        results.append(check_volume(monitors.volume, profile, history))
    if monitors.schema_.enabled:
        results.append(check_schema(monitors.schema_, profile, history))
    if monitors.nulls.enabled:
        results.append(check_nulls(monitors.nulls, profile, history))
    if monitors.drift.enabled:
        results.append(check_drift(monitors.drift, profile, history))

    findings = [f for r in results for f in r[0]]
    checked = frozenset(k for r in results for k in r[1])
    skipped = tuple(s for r in results for s in r[2])
    return Evaluation(dataset, partition, tuple(findings), checked, skipped)
