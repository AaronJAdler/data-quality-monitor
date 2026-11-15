"""One production run: profile a date's partition, check it, update alerts, queue notifications."""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from dqmon import alerts as alerting
from dqmon.baseline import Baseline
from dqmon.config import Channel, DatasetConfig
from dqmon.models import Evaluation
from dqmon.monitors import evaluate
from dqmon.profile import profile_records, read_partition
from dqmon.store import Store


@dataclass(frozen=True)
class RunResult:
    evaluation: Evaluation
    events: tuple[alerting.Event, ...]
    open_alerts: tuple[alerting.Alert, ...]
    new_notifications: int


def _rebuild(
    store: Store, cfg: DatasetConfig, partition: dt.date
) -> tuple[Baseline, dict[str, alerting.Alert]]:
    """Replay every stored run before `partition` to get the baseline and the open alerts.

    State is derived from the run history instead of being stored separately, so there is one
    source of truth and rerunning a date cannot leave the two out of step. It costs a pass over
    the history on every run, which is fine for daily partitions.
    """
    baseline = Baseline()
    open_alerts: dict[str, alerting.Alert] = {}
    for profile, evaluation in store.runs_before(cfg.dataset, partition):
        adopted = baseline.record(profile, evaluation)
        open_alerts, _ = alerting.step(
            open_alerts, evaluation, adopted, cfg.routing.renotify_after_days
        )
    return baseline, open_alerts


def run_date(store: Store, cfg: DatasetConfig, partition: dt.date) -> RunResult:
    baseline, open_alerts = _rebuild(store, cfg, partition)
    loaded = read_partition(cfg.source, partition)
    profile = None
    if loaded is not None:
        records, arrived_at = loaded
        profile = profile_records(
            cfg.dataset,
            partition,
            records,
            event_time_column=cfg.source.event_time_column,
            arrived_at=arrived_at,
        )
    evaluation = evaluate(
        cfg.monitors,
        cfg.dataset,
        partition,
        profile,
        baseline.history(),
        previous=baseline.previous,
        missing_streak=baseline.missing_streak + 1,
    )
    adopted = baseline.record(profile, evaluation)
    open_alerts, events = alerting.step(
        open_alerts, evaluation, adopted, cfg.routing.renotify_after_days
    )
    notifications = alerting.route(cfg.routing, cfg.dataset, cfg.owner, partition, events)
    added = store.save_run(profile, evaluation, notifications)
    return RunResult(evaluation, tuple(events), tuple(open_alerts.values()), added)


def deliver(store: Store, cfg: DatasetConfig, client: httpx.Client | None = None) -> dict[str, int]:
    """Send everything pending. At-least-once: a failure leaves the row pending for next time."""
    outcome = {"delivered": 0, "failed": 0, "unconfigured": 0}
    own_client = client is None
    http = client or httpx.Client(timeout=10)
    try:
        for notification_id, channel_name, payload in store.pending():
            channel = cfg.routing.channels.get(channel_name)
            try:
                status = _send(channel, payload, notification_id, http)
            except (httpx.HTTPError, OSError) as exc:
                store.mark(notification_id, "pending", type(exc).__name__)
                outcome["failed"] += 1
                continue
            store.mark(notification_id, status)
            outcome[status] += 1
    finally:
        if own_client:
            http.close()
    return outcome


def _send(
    channel: Channel | None, payload: dict[str, object], notification_id: str, http: httpx.Client
) -> str:
    if channel is None:
        return "unconfigured"
    if channel.type == "file":
        if not channel.path:
            return "unconfigured"
        path = Path(channel.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
        return "delivered"
    url = os.environ.get(channel.url_env or "")
    if not url:
        return "unconfigured"
    response = http.post(url, json=payload, headers={"Idempotency-Key": notification_id})
    response.raise_for_status()
    return "delivered"
