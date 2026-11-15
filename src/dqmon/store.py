"""State in one DuckDB file: profiles, each day's evaluation, and the notification outbox."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb

from dqmon.alerts import Notification
from dqmon.models import Evaluation, Finding, Profile, Severity

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    dataset     TEXT NOT NULL,
    partition   DATE NOT NULL,
    profile     TEXT,              -- NULL when no partition arrived
    evaluation  TEXT NOT NULL,
    PRIMARY KEY (dataset, partition)
);
CREATE TABLE IF NOT EXISTS outbox (
    id            TEXT PRIMARY KEY,
    dataset       TEXT NOT NULL,
    partition     DATE NOT NULL,
    channel       TEXT NOT NULL,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending, delivered, unconfigured
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT
);
"""


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        "monitor": f.monitor,
        "kind": f.kind,
        "column": f.column,
        "severity": int(f.severity),
        "observed": f.observed,
        "expected": f.expected,
        "detail": f.detail,
    }


def evaluation_to_json(e: Evaluation) -> str:
    return json.dumps(
        {
            "dataset": e.dataset,
            "partition": e.partition.isoformat(),
            "findings": [_finding_to_dict(f) for f in e.findings],
            "checked": sorted(e.checked),
            "skipped": list(e.skipped),
        }
    )


def evaluation_from_json(text: str) -> Evaluation:
    raw = json.loads(text)
    findings = tuple(
        Finding(
            f["monitor"], f["kind"], f["column"], Severity(f["severity"]),
            f["observed"], f["expected"], f["detail"],
        )
        for f in raw["findings"]
    )  # fmt: skip
    return Evaluation(
        raw["dataset"],
        dt.date.fromisoformat(raw["partition"]),
        findings,
        frozenset(raw["checked"]),
        tuple(raw["skipped"]),
    )


def notification_payload(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "dataset": n.dataset,
        "owner": n.owner,
        "partition": n.partition.isoformat(),
        "text": n.text,
        "events": [
            {
                "id": e.id,
                "kind": e.kind,
                "key": e.key,
                "severity": e.severity.label,
                "opened": e.opened.isoformat(),
                "text": e.text,
            }
            for e in n.events
        ],
    }


class Store:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(path))
        self.db.execute(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def runs_before(
        self, dataset: str, partition: dt.date
    ) -> list[tuple[Profile | None, Evaluation]]:
        rows = self.db.execute(
            "SELECT profile, evaluation FROM runs WHERE dataset = ? AND partition < ? "
            "ORDER BY partition",
            [dataset, partition],
        ).fetchall()
        return [(Profile.from_json(p) if p else None, evaluation_from_json(e)) for p, e in rows]

    def save_run(
        self,
        profile: Profile | None,
        evaluation: Evaluation,
        notifications: list[Notification],
    ) -> int:
        """Store the run and its notifications in one transaction. Returns new notifications.

        Rerunning a date replaces its run row. Notification ids are derived from the events, so
        a rerun with the same outcome inserts nothing and nobody is notified twice.
        """
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
                [
                    evaluation.dataset,
                    evaluation.partition,
                    profile.to_json() if profile else None,
                    evaluation_to_json(evaluation),
                ],
            )
            added = 0
            for n in notifications:
                exists = self.db.execute("SELECT 1 FROM outbox WHERE id = ?", [n.id]).fetchone()
                if exists:
                    continue
                self.db.execute(
                    "INSERT INTO outbox (id, dataset, partition, channel, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [n.id, n.dataset, n.partition, n.channel, json.dumps(notification_payload(n))],
                )
                added += 1
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return added

    def pending(self) -> list[tuple[str, str, dict[str, Any]]]:
        rows = self.db.execute(
            "SELECT id, channel, payload FROM outbox WHERE status = 'pending' "
            "ORDER BY partition, channel, id"
        ).fetchall()
        return [(i, c, json.loads(p)) for i, c, p in rows]

    def mark(self, notification_id: str, status: str, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE outbox SET status = ?, attempts = attempts + 1, last_error = ? WHERE id = ?",
            [status, error, notification_id],
        )

    def outbox_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, count(*) FROM outbox GROUP BY 1").fetchall()
        return {str(s): int(n) for s, n in rows}
