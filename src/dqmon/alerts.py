"""Alert lifecycle and routing.

A finding is what a monitor saw on one day. An alert is the problem behind it: it opens once,
stays open while the finding repeats, and closes only when a run that really checked it comes
back clean. People are notified about changes to alerts, never about every finding.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from dqmon.config import Routing
from dqmon.models import Evaluation, Finding, Severity


@dataclass(frozen=True)
class Alert:
    key: str
    monitor: str
    severity: Severity
    opened: dt.date
    last_notified: dt.date
    finding: Finding
    adopted: bool = False


@dataclass(frozen=True)
class Event:
    kind: str  # opened, escalated, reminder, adopted, resolved, notice
    partition: dt.date
    key: str
    monitor: str
    severity: Severity
    opened: dt.date
    text: str

    @property
    def id(self) -> str:
        """Stable across reruns: the same transition always has the same id."""
        raw = f"{self.kind}|{self.key}|{self.opened.isoformat()}|{self.partition.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _describe(f: Finding) -> str:
    return f"{f.observed}; expected {f.expected} ({f.detail})"


def step(
    open_alerts: dict[str, Alert],
    evaluation: Evaluation,
    adopted: Iterable[str],
    renotify_after_days: int,
) -> tuple[dict[str, Alert], list[Event]]:
    """Apply one day's evaluation to the open alerts. Pure: returns the new state and events."""
    day = evaluation.partition
    alerts = dict(open_alerts)
    events: list[Event] = []
    firing: set[str] = set()

    for f in evaluation.findings:
        if f.severity == Severity.INFO:
            events.append(Event("notice", day, f.key, f.monitor, f.severity, day, _describe(f)))
            continue
        firing.add(f.key)
        current = alerts.get(f.key)
        if current is None:
            alerts[f.key] = Alert(f.key, f.monitor, f.severity, day, day, f)
            events.append(Event("opened", day, f.key, f.monitor, f.severity, day, _describe(f)))
        elif f.severity > current.severity:
            alerts[f.key] = replace(current, severity=f.severity, last_notified=day, finding=f)
            events.append(
                Event("escalated", day, f.key, f.monitor, f.severity, current.opened, _describe(f))
            )
        elif (day - current.last_notified).days >= renotify_after_days:
            alerts[f.key] = replace(current, last_notified=day, finding=f)
            text = f"still open after {(day - current.opened).days} days. {_describe(f)}"
            events.append(
                Event("reminder", day, f.key, f.monitor, current.severity, current.opened, text)
            )
        else:
            alerts[f.key] = replace(current, finding=f)

    for key in adopted:
        current = alerts.get(key)
        if current is not None and not current.adopted:
            alerts[key] = replace(current, adopted=True)
            events.append(
                Event(
                    "adopted", day, key, current.monitor, current.severity, current.opened,
                    "repeated on consecutive partitions; these partitions now count as the "
                    "baseline, and the alert closes when the monitor stops firing",
                )
            )  # fmt: skip

    for key, current in list(alerts.items()):
        if key in firing:
            continue
        if not evaluation.was_checked(key):
            continue  # this run could not check it, so it cannot close it
        del alerts[key]
        how = "closed: the new baseline was adopted" if current.adopted else "check passed"
        events.append(
            Event("resolved", day, key, current.monitor, current.severity, current.opened, how)
        )
    return alerts, events


@dataclass(frozen=True)
class Notification:
    id: str  # also sent as the Idempotency-Key
    channel: str
    dataset: str
    owner: str
    partition: dt.date
    text: str
    events: tuple[Event, ...]


def route(
    routing: Routing, dataset: str, owner: str, partition: dt.date, events: Sequence[Event]
) -> list[Notification]:
    """One notification per channel per run, listing every event routed to that channel."""
    by_channel: dict[str, list[Event]] = {}
    for event in events:
        for rule in routing.routes:
            m = rule.match
            if m.dataset is not None and dataset not in m.dataset:
                continue
            if m.monitor is not None and event.monitor not in m.monitor:
                continue
            if m.severity is not None and event.severity.label not in m.severity:
                continue
            for channel in rule.channels:
                bucket = by_channel.setdefault(channel, [])
                if event not in bucket:
                    bucket.append(event)
    notifications = []
    for channel, routed in sorted(by_channel.items()):
        ids = "|".join(sorted(e.id for e in routed))
        digest = hashlib.sha256(f"{dataset}|{partition}|{channel}|{ids}".encode()).hexdigest()
        lines = [f"{dataset} {partition.isoformat()} (owner: {owner})"]
        lines += [f"- {e.kind.upper()} {e.severity.label} {e.key}: {e.text}" for e in routed]
        notifications.append(
            Notification(
                digest[:24], channel, dataset, owner, partition, "\n".join(lines), tuple(routed)
            )
        )
    return notifications
