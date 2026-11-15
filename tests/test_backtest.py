from __future__ import annotations

import json

import pytest

from dqmon import backtest
from dqmon.config import DatasetConfig
from tests.conftest import ROOT


@pytest.fixture(scope="module")
def summary(fires: DatasetConfig) -> dict[str, object]:
    return backtest.summarise(fires, backtest.run(fires))


def by_name(summary: dict[str, object]) -> dict[str, dict[str, int]]:
    return {a["anomaly"]: a for a in summary["anomalies"]}  # type: ignore[index,union-attr]


def test_committed_report_matches_a_fresh_run(summary: dict[str, object]) -> None:
    committed = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(summary))
    assert (ROOT / "docs" / "backtest.md").read_text(encoding="utf-8") == backtest.render_markdown(
        summary  # type: ignore[arg-type]
    )


def test_structural_defects_are_always_caught(summary: dict[str, object]) -> None:
    results = by_name(summary)
    for name in (
        "missing_partition",
        "redelivered_partition",
        "drop_rows_100",
        "drop_column_UniqueId",
        "rename_column_AcresBurned",
        "type_Counties_to_string",
        "null_Name_100",
        "zero_fill_AcresBurned",
        "swap_lat_lon",
        "one_bad_longitude",
    ):
        assert results[name]["caught_by_expected_monitor"] == results[name]["trials"] > 0, name


def test_known_blind_spots_stay_documented(summary: dict[str, object]) -> None:
    """If these start passing, the README's limits section is out of date."""
    results = by_name(summary)
    assert results["scale_AcresBurned_hectares"]["caught_by_any_monitor"] == 0
    assert results["shift_Latitude_0.5"]["caught_by_any_monitor"] == 0
    assert (
        0
        < results["drop_rows_30"]["caught_by_expected_monitor"]
        < results["drop_rows_30"]["trials"]
    )


def test_every_trial_runs_on_a_quiet_day_with_enough_history(fires: DatasetConfig) -> None:
    result = backtest.run(fires)
    days = {d.date: d for d in result.days}
    for date in result.trial_dates:
        day = days[date]
        assert len(day.history) >= backtest.WARMUP
        assert not [f for f in day.evaluation.findings if f.severity > 0]


def test_volume_setting_was_selected_on_tuning_data_only(summary: dict[str, object]) -> None:
    sweep = summary["volume_sweep"]
    assert sweep["selected"] == sweep["configured"]  # type: ignore[index]
