from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from dqmon.config import DatasetConfig
from dqmon.models import Profile
from dqmon.profile import profile_records

ROOT = Path(__file__).resolve().parents[1]
START = dt.date(2024, 1, 1)


@pytest.fixture(scope="session")
def fires() -> DatasetConfig:
    cfg = DatasetConfig.load(ROOT / "datasets" / "ca_fires.yaml")
    source = cfg.source.model_copy(update={"path": str(ROOT / cfg.source.path)})
    return cfg.model_copy(update={"source": source})


def make_profile(day: int, records: Sequence[dict[str, Any]]) -> Profile:
    return profile_records(
        "orders", START + dt.timedelta(days=day), list(records), event_time_column="ts"
    )


def rows(day: int, n: int, **overrides: Any) -> list[dict[str, Any]]:
    """n ordinary records for a given day; keyword arguments replace a column in every row."""
    stamp = (START + dt.timedelta(days=day)).isoformat() + "T12:00:00Z"
    out = []
    for i in range(n):
        record: dict[str, Any] = {
            "id": f"{day}-{i}",
            "amount": 100.0 + (i * 37 % 50),
            "note": None if i % 4 == 0 else "ok",
            "ts": stamp,
        }
        record.update(overrides)
        out.append(record)
    return out


def history(days: int, n: int = 40) -> list[Profile]:
    return [make_profile(d, rows(d, n)) for d in range(days)]
