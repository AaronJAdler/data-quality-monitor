from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import httpx
import pytest

from dqmon.config import DatasetConfig
from dqmon.engine import deliver, run_date
from dqmon.store import Store
from tests.conftest import START, rows


def write_partition(root: Path, day: int, records: list[dict[str, object]]) -> None:
    path = root / f"{(START + dt.timedelta(days=day)).isoformat()}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(records, handle)


@pytest.fixture
def cfg(tmp_path: Path) -> DatasetConfig:
    data = tmp_path / "data"
    data.mkdir()
    for day in range(10):
        write_partition(data, day, rows(day, 40))
    write_partition(data, 10, rows(10, 6))  # a truncated load
    write_partition(data, 11, rows(11, 40))
    return DatasetConfig.model_validate(
        {
            "dataset": "orders",
            "owner": "sales",
            "source": {"path": str(data), "event_time_column": "ts"},
            "routing": {
                "channels": {
                    "owners": {"type": "file", "path": str(tmp_path / "out" / "owners.jsonl")},
                    "pager": {"type": "webhook", "url_env": "DQMON_TEST_PAGER"},
                },
                "routes": [
                    {"match": {"severity": ["warning", "critical"]}, "channels": ["owners"]},
                    {"match": {"severity": ["critical"]}, "channels": ["pager"]},
                ],
            },
        }
    )


def replay(store: Store, cfg: DatasetConfig, upto: int) -> None:
    for day in range(upto + 1):
        run_date(store, cfg, START + dt.timedelta(days=day))


def test_truncated_load_opens_then_resolves(cfg: DatasetConfig) -> None:
    store = Store()
    replay(store, cfg, 9)
    bad = run_date(store, cfg, START + dt.timedelta(days=10))
    assert [(e.kind, e.key) for e in bad.events] == [("opened", "volume/row_count/-")]
    assert bad.new_notifications == 2  # critical: owners and pager
    good = run_date(store, cfg, START + dt.timedelta(days=11))
    assert [(e.kind, e.key) for e in good.events] == [("resolved", "volume/row_count/-")]
    assert good.open_alerts == ()


def test_rerunning_a_date_notifies_nobody_twice(cfg: DatasetConfig) -> None:
    store = Store()
    replay(store, cfg, 10)
    again = run_date(store, cfg, START + dt.timedelta(days=10))
    assert [e.kind for e in again.events] == ["opened"]
    assert again.new_notifications == 0
    assert store.outbox_counts() == {"pending": 2}


def test_state_survives_reopening_the_file(cfg: DatasetConfig, tmp_path: Path) -> None:
    path = tmp_path / "state" / "dqmon.duckdb"
    store = Store(path)
    replay(store, cfg, 10)
    store.close()
    store = Store(path)
    result = run_date(store, cfg, START + dt.timedelta(days=11))
    assert [e.kind for e in result.events] == ["resolved"]
    store.close()


def test_delivery_to_file_and_webhook(cfg: DatasetConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store()
    replay(store, cfg, 10)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503 if len(seen) == 1 else 200)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    # No URL configured: the page is parked, the file notification still goes out.
    assert deliver(store, cfg, client) == {"delivered": 1, "failed": 0, "unconfigured": 1}
    store.db.execute("UPDATE outbox SET status = 'pending' WHERE status = 'unconfigured'")

    monkeypatch.setenv("DQMON_TEST_PAGER", "https://pager.example/hook")
    assert deliver(store, cfg, client) == {"delivered": 0, "failed": 1, "unconfigured": 0}
    assert store.outbox_counts() == {"delivered": 1, "pending": 1}  # the 503 left it pending
    assert deliver(store, cfg, client) == {"delivered": 1, "failed": 0, "unconfigured": 0}

    # The retry carried the same Idempotency-Key, so the receiver can drop a duplicate.
    assert len({r.headers["Idempotency-Key"] for r in seen}) == 1
    body = json.loads(seen[-1].content)
    assert body["dataset"] == "orders" and body["events"][0]["kind"] == "opened"

    lines = Path(cfg.routing.channels["owners"].path or "").read_text().splitlines()
    assert len(lines) == 1 and "volume/row_count/-" in lines[0]
