from __future__ import annotations

import datetime as dt

from dqmon.baseline import Baseline
from dqmon.config import Monitors
from dqmon.models import Severity
from dqmon.monitors import evaluate
from tests.conftest import START, history, make_profile, rows

M = Monitors()


def check(day: int, records: list[dict[str, object]] | None, past: int = 14, **kw: object):  # type: ignore[no-untyped-def]
    hist = history(past)
    profile = make_profile(day, records) if records is not None else None
    return evaluate(
        M,
        "orders",
        START + dt.timedelta(days=day),
        profile,
        hist,
        previous=hist[-1] if hist else None,
        **kw,  # type: ignore[arg-type]
    )


def keys(evaluation) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.key for f in evaluation.findings}


def test_an_ordinary_partition_raises_nothing() -> None:
    result = check(14, rows(14, 40))
    assert result.findings == ()
    assert result.was_checked("volume/row_count/-")
    assert result.was_checked("schema/column_removed/amount")
    assert result.was_checked("drift/distribution/amount")


def test_missing_partition_is_a_warning_then_critical() -> None:
    first = check(14, None, missing_streak=1)
    second = check(15, None, missing_streak=2)
    assert [f.severity for f in first.findings] == [Severity.WARNING]
    assert [f.severity for f in second.findings] == [Severity.CRITICAL]
    assert keys(first) == {"freshness/missing_partition/-"}
    # Nothing else was checked, so no other alert may be closed by this run.
    assert not first.was_checked("volume/row_count/-")


def test_redelivered_partition_is_reported() -> None:
    result = check(14, rows(13, 40))
    assert "freshness/duplicate_partition/-" in keys(result)


def test_records_changed_but_no_newer_event() -> None:
    stale = rows(14, 40, ts="2024-01-14T12:00:00Z")  # day 13's timestamp
    assert "freshness/stale_events/-" in keys(check(14, stale))


def test_volume_drop_and_spike() -> None:
    assert keys(check(14, rows(14, 20))) >= {"volume/row_count/-"}
    assert keys(check(14, rows(14, 80))) >= {"volume/row_count/-"}
    assert "volume/row_count/-" not in keys(check(14, rows(14, 41)))


def test_empty_partition_is_critical() -> None:
    result = check(14, [])
    volume = [f for f in result.findings if f.monitor == "volume"]
    assert [f.severity for f in volume] == [Severity.CRITICAL]


def test_volume_needs_history_and_says_so() -> None:
    result = check(3, rows(3, 5), past=3)
    assert "volume/row_count/-" not in keys(result)
    assert not result.was_checked("volume/row_count/-")
    assert any(s.startswith("volume:") for s in result.skipped)


def test_schema_column_removed_added_and_retyped() -> None:
    changed = [
        {"id": r["id"], "amount": str(r["amount"]), "ts": r["ts"], "channel": "web"}
        for r in rows(14, 40)
    ]
    found = {f.key: f.severity for f in check(14, changed).findings if f.monitor == "schema"}
    assert found == {
        "schema/column_removed/note": Severity.CRITICAL,
        "schema/column_added/channel": Severity.INFO,
        "schema/type_changed/amount": Severity.CRITICAL,
    }


def test_integer_among_floats_is_not_a_type_change() -> None:
    result = check(14, rows(14, 40, amount=120))
    assert not [f for f in result.findings if f.monitor == "schema"]


def test_first_values_in_an_always_null_column_is_informational() -> None:
    hist = [make_profile(d, rows(d, 40, note=None)) for d in range(14)]
    result = evaluate(
        M, "orders", START + dt.timedelta(days=14), make_profile(14, rows(14, 40)), hist
    )
    schema = [f for f in result.findings if f.monitor == "schema"]
    assert [(f.kind, f.severity) for f in schema] == [("first_values", Severity.INFO)]


def test_null_rate_up_down_and_fully_null() -> None:
    all_null = check(14, rows(14, 40, amount=None))
    finding = next(f for f in all_null.findings if f.key == "nulls/null_rate/amount")
    assert finding.severity == Severity.CRITICAL

    filled = check(14, rows(14, 40, note="ok"))  # 25% null -> 0% null
    finding = next(f for f in filled.findings if f.key == "nulls/null_rate/note")
    assert finding.severity == Severity.WARNING


def test_small_null_rate_changes_are_ignored() -> None:
    records = rows(14, 40)
    records[1]["note"] = None
    records[2]["note"] = None  # 25% -> 30%
    assert "nulls/null_rate/note" not in keys(check(14, records))


def test_out_of_range_value_is_caught_from_a_single_row() -> None:
    records = rows(14, 40)
    records[5]["amount"] = 12_000_000.0
    finding = next(f for f in check(14, records).findings if f.kind == "out_of_range")
    assert finding.column == "amount" and finding.severity == Severity.WARNING


def test_distribution_shift_is_caught() -> None:
    records = rows(14, 40)
    for r in records:
        r["amount"] = float(r["amount"]) + 40
    assert "drift/distribution/amount" in keys(check(14, records))


def test_drift_is_skipped_not_passed_when_there_are_too_few_values() -> None:
    result = check(14, rows(14, 5))
    assert not result.was_checked("drift/distribution/amount")
    assert any(s.startswith("drift/amount: 5 values") for s in result.skipped)


def test_quarantined_partitions_leave_the_baseline_until_adopted() -> None:
    baseline = Baseline(adopt_after=3)
    for day in range(14):
        profile = make_profile(day, rows(day, 40))
        baseline.record(
            profile, evaluate(M, "orders", profile.partition, profile, baseline.history())
        )
    assert len(baseline.history()) == 14

    adopted: list[frozenset[str]] = []
    held_out: list[int] = []
    for day in (14, 15, 16):
        profile = make_profile(day, rows(day, 90))  # a lasting jump in volume
        result = evaluate(M, "orders", profile.partition, profile, baseline.history())
        assert "volume/row_count/-" in keys(result)
        adopted.append(baseline.record(profile, result))
        held_out.append(len(baseline.quarantined))
    assert held_out == [1, 2, 0]  # held out of the baseline until the third repeat
    assert adopted == [frozenset(), frozenset(), frozenset({"volume/row_count/-"})]
    assert len(baseline.history()) == 17
