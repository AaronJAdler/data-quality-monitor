"""Facts about the real CAL FIRE partitions, and what the monitors make of them."""

from __future__ import annotations

import datetime as dt

from dqmon import backtest
from dqmon.config import DatasetConfig
from dqmon.engine import run_date
from dqmon.models import Severity
from dqmon.profile import list_partitions, parse_ts, read_partition
from dqmon.store import Store


def test_ninety_partitions_with_a_three_day_gap(fires: DatasetConfig) -> None:
    partitions = list_partitions(fires.source)
    assert len(partitions) == 90
    assert partitions[0] == dt.date(2021, 7, 15) and partitions[-1] == dt.date(2021, 10, 15)
    missing = {dt.date(2021, 10, d) for d in (4, 5, 6)}
    assert not missing & set(partitions)


def test_the_outage_opens_escalates_and_resolves(fires: DatasetConfig) -> None:
    store = Store()
    seen: dict[dt.date, list[tuple[str, str, Severity]]] = {}
    day = dt.date(2021, 9, 15)
    while day <= dt.date(2021, 10, 7):
        result = run_date(store, fires, day)
        seen[day] = [(e.kind, e.key, e.severity) for e in result.events if e.kind != "notice"]
        day += dt.timedelta(days=1)
    key = "freshness/missing_partition/-"
    assert ("opened", key, Severity.WARNING) in seen[dt.date(2021, 10, 4)]
    assert ("escalated", key, Severity.CRITICAL) in seen[dt.date(2021, 10, 5)]
    assert seen[dt.date(2021, 10, 6)] == []  # still down: no second page
    assert ("resolved", key, Severity.CRITICAL) in seen[dt.date(2021, 10, 7)]


def test_the_real_bad_longitude_is_caught(fires: DatasetConfig) -> None:
    days = {d.date: d for d in backtest.replay_history(fires, fires.monitors)}
    findings = days[dt.date(2021, 7, 20)].evaluation.findings
    assert [(f.key, f.severity) for f in findings] == [
        ("drift/out_of_range/Longitude", Severity.WARNING)
    ]
    assert "-1.18465e+07" in findings[0].observed


def test_updated_is_always_at_least_seven_hours_behind_arrival(fires: DatasetConfig) -> None:
    """`Updated` carries a Z suffix, but the newest one is never under 7 h before the snapshot.

    That is what Pacific Daylight Time labelled as UTC would look like. It is why the freshness
    monitor compares event times with each other and never with the wall clock.
    """
    lags = []
    for partition in list_partitions(fires.source):
        loaded = read_partition(fires.source, partition)
        assert loaded is not None
        records, arrived = loaded
        newest = max(ts for r in records if (ts := parse_ts(r["Updated"])) is not None)
        assert arrived is not None
        lags.append((arrived - newest).total_seconds() / 3600)
    assert len(lags) == 90
    assert 7.0 <= min(lags) < 7.01
