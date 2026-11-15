from __future__ import annotations

import datetime as dt
import enum
import json
from dataclasses import dataclass, field
from typing import Any


class Severity(enum.IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class ColumnProfile:
    """What one column looked like in one partition."""

    name: str
    types: dict[str, int]  # JSON type -> count of non-null values
    nulls: int
    numeric: tuple[float, ...]  # every numeric value; booleans are not numbers here


@dataclass(frozen=True)
class Profile:
    """Everything the monitors need to know about one partition. The rows are not kept."""

    dataset: str
    partition: dt.date
    rows: int
    columns: dict[str, ColumnProfile]
    max_event_ts: dt.datetime | None
    fingerprint: str  # hash of the partition's records, for spotting a re-delivered file
    arrived_at: dt.datetime | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "dataset": self.dataset,
                "partition": self.partition.isoformat(),
                "rows": self.rows,
                "max_event_ts": self.max_event_ts.isoformat() if self.max_event_ts else None,
                "fingerprint": self.fingerprint,
                "arrived_at": self.arrived_at.isoformat() if self.arrived_at else None,
                "columns": [
                    {"name": c.name, "types": c.types, "nulls": c.nulls, "numeric": c.numeric}
                    for c in self.columns.values()
                ],
            }
        )

    @classmethod
    def from_json(cls, text: str) -> Profile:
        raw: dict[str, Any] = json.loads(text)
        columns = {
            c["name"]: ColumnProfile(c["name"], dict(c["types"]), c["nulls"], tuple(c["numeric"]))
            for c in raw["columns"]
        }
        return cls(
            dataset=raw["dataset"],
            partition=dt.date.fromisoformat(raw["partition"]),
            rows=raw["rows"],
            columns=columns,
            max_event_ts=(
                dt.datetime.fromisoformat(raw["max_event_ts"]) if raw["max_event_ts"] else None
            ),
            fingerprint=raw["fingerprint"],
            arrived_at=dt.datetime.fromisoformat(raw["arrived_at"]) if raw["arrived_at"] else None,
        )


@dataclass(frozen=True)
class Finding:
    """One thing a monitor found wrong with one partition."""

    monitor: str  # freshness, volume, schema, nulls, drift
    kind: str  # e.g. missing_partition, row_count, column_removed
    column: str | None
    severity: Severity
    observed: str
    expected: str
    detail: str

    @property
    def key(self) -> str:
        """Identity of the underlying problem: the same key on consecutive days is one alert."""
        return f"{self.monitor}/{self.kind}/{self.column or '-'}"


@dataclass(frozen=True)
class Evaluation:
    """The result of checking one partition date."""

    dataset: str
    partition: dt.date
    findings: tuple[Finding, ...]
    # Finding keys, or key prefixes ending in "/", that were really checked this run. An open
    # alert can only be resolved by a run that checked it.
    checked: frozenset[str] = field(default_factory=frozenset)
    skipped: tuple[str, ...] = ()  # human-readable notes on checks that could not run

    def was_checked(self, key: str) -> bool:
        return any(key == c or (c.endswith("/") and key.startswith(c)) for c in self.checked)

    @property
    def worst(self) -> Severity | None:
        return max((f.severity for f in self.findings), default=None)
