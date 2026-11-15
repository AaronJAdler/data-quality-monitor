"""Reads a partition and reduces it to a Profile."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dqmon.config import Source
from dqmon.models import ColumnProfile, Profile

Record = Mapping[str, Any]


def json_type(value: object) -> str | None:
    """The JSON type of a value, or None for null.

    Integers and floats are both "number": JSON does not distinguish them, and a latitude of
    exactly 39 is not a schema change.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def parse_ts(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def profile_records(
    dataset: str,
    partition: dt.date,
    records: Sequence[Record],
    *,
    event_time_column: str | None = None,
    arrived_at: dt.datetime | None = None,
) -> Profile:
    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        for name in record:
            if name not in seen:
                seen.add(name)
                names.append(name)

    columns: dict[str, ColumnProfile] = {}
    for name in names:
        types: dict[str, int] = {}
        nulls = 0
        numeric: list[float] = []
        for record in records:
            value = record.get(name)
            kind = json_type(value)
            if kind is None:
                nulls += 1
                continue
            types[kind] = types.get(kind, 0) + 1
            if kind == "number":
                numeric.append(float(value))  # type: ignore[arg-type]
        columns[name] = ColumnProfile(name, types, nulls, tuple(numeric))

    stamps = [
        ts
        for record in records
        if event_time_column and (ts := parse_ts(record.get(event_time_column))) is not None
    ]
    canonical = sorted(json.dumps(r, sort_keys=True, default=str) for r in records)
    fingerprint = hashlib.sha256("\n".join(canonical).encode()).hexdigest()[:16]
    return Profile(
        dataset=dataset,
        partition=partition,
        rows=len(records),
        columns=columns,
        max_event_ts=max(stamps, default=None),
        fingerprint=fingerprint,
        arrived_at=arrived_at,
    )


def partition_path(source: Source, partition: dt.date) -> Path:
    return Path(source.path) / f"{partition.isoformat()}.json.gz"


def list_partitions(source: Source) -> list[dt.date]:
    return sorted(
        dt.date.fromisoformat(p.name.removesuffix(".json.gz"))
        for p in Path(source.path).glob("*.json.gz")
    )


def read_partition(
    source: Source, partition: dt.date
) -> tuple[list[dict[str, Any]], dt.datetime | None] | None:
    """The partition's records and arrival time, or None if no file exists for that date."""
    path = partition_path(source, partition)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    arrived_at = None
    if isinstance(payload, dict):
        if source.arrived_at_key:
            arrived_at = parse_ts(payload.get(source.arrived_at_key))
        payload = payload[source.records_key] if source.records_key else payload
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a list of records")
    return payload, arrived_at
