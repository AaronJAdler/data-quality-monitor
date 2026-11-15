"""Anomalies to inject into a real partition for the backtest.

Each anomaly is a small, named defect of the kind pipelines really produce: a truncated load, a
double load, a renamed column, a unit change. The row choices are seeded, so a backtest gives
the same result every time.
"""

from __future__ import annotations

import copy
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

Records = list[dict[str, Any]]
# (today's records, the previous partition's records, rng) -> today's records, or None for
# "the partition never arrived".
Apply = Callable[[Records, Records, random.Random], Records | None]


@dataclass(frozen=True)
class Anomaly:
    name: str
    family: str  # groups magnitudes of the same defect in the report
    description: str
    expected: tuple[str, ...]  # finding-key prefixes that count as the right monitor catching it
    apply: Apply


def _pick(n: int, fraction: float, rng: random.Random) -> set[int]:
    count = min(n, max(1, round(n * fraction))) if n else 0
    return set(rng.sample(range(n), count))


def _drop_rows(fraction: float) -> Apply:
    def apply(today: Records, _prev: Records, rng: random.Random) -> Records:
        gone = _pick(len(today), fraction, rng)
        return [r for i, r in enumerate(today) if i not in gone]

    return apply


def _duplicate_rows(fraction: float) -> Apply:
    def apply(today: Records, _prev: Records, rng: random.Random) -> Records:
        again = _pick(len(today), fraction, rng)
        return today + [copy.deepcopy(r) for i, r in enumerate(today) if i in again]

    return apply


def _drop_column(column: str) -> Apply:
    def apply(today: Records, _prev: Records, _rng: random.Random) -> Records:
        return [{k: v for k, v in r.items() if k != column} for r in today]

    return apply


def _rename_column(column: str, new: str) -> Apply:
    def apply(today: Records, _prev: Records, _rng: random.Random) -> Records:
        return [{(new if k == column else k): v for k, v in r.items()} for r in today]

    return apply


def _map_column(column: str, change: Callable[[Any], Any], fraction: float = 1.0) -> Apply:
    def apply(today: Records, _prev: Records, rng: random.Random) -> Records:
        hit = _pick(len(today), fraction, rng) if fraction < 1.0 else set(range(len(today)))
        out = copy.deepcopy(today)
        for i, record in enumerate(out):
            if i in hit and column in record:
                record[column] = change(record[column])
        return out

    return apply


def _swap(a: str, b: str) -> Apply:
    def apply(today: Records, _prev: Records, _rng: random.Random) -> Records:
        out = copy.deepcopy(today)
        for record in out:
            record[a], record[b] = record.get(b), record.get(a)
        return out

    return apply


def _missing(_today: Records, _prev: Records, _rng: random.Random) -> None:
    return None


def _redelivered(_today: Records, prev: Records, _rng: random.Random) -> Records:
    return copy.deepcopy(prev)


def _scaled(factor: float) -> Callable[[Any], Any]:
    return lambda v: None if v is None else v * factor


def _shifted(delta: float) -> Callable[[Any], Any]:
    return lambda v: None if v is None else v + delta


def catalogue() -> Sequence[Anomaly]:
    """Every anomaly the backtest injects, against CAL FIRE's incident feed columns."""
    items: list[Anomaly] = [
        Anomaly(
            "missing_partition",
            "Partition never arrives",
            "the day's file is absent",
            ("freshness/missing_partition",),
            _missing,
        ),
        Anomaly(
            "redelivered_partition",
            "Yesterday's file delivered again",
            "the day's file is a copy of the previous day's",
            ("freshness/duplicate_partition",),
            _redelivered,
        ),
    ]
    for fraction in (0.3, 0.5, 0.8, 1.0):
        items.append(
            Anomaly(
                f"drop_rows_{round(fraction * 100)}",
                "Rows lost",
                f"{fraction:.0%} of rows missing (truncated load)",
                ("volume/row_count",),
                _drop_rows(fraction),
            )
        )
    for fraction in (0.5, 1.0):
        items.append(
            Anomaly(
                f"duplicate_rows_{round(fraction * 100)}",
                "Rows duplicated",
                f"{fraction:.0%} of rows loaded twice",
                ("volume/row_count",),
                _duplicate_rows(fraction),
            )
        )
    for column in ("PercentContained", "UniqueId", "Counties"):
        items.append(
            Anomaly(
                f"drop_column_{column}",
                "Column removed",
                f"{column} no longer delivered",
                (f"schema/column_removed/{column}",),
                _drop_column(column),
            )
        )
    items.append(
        Anomaly(
            "rename_column_AcresBurned",
            "Column renamed",
            "AcresBurned delivered as Acres",
            ("schema/column_removed/AcresBurned",),
            _rename_column("AcresBurned", "Acres"),
        )
    )
    items += [
        Anomaly(
            "type_Latitude_to_string",
            "Type changed",
            "Latitude delivered as text",
            ("schema/type_changed/Latitude",),
            _map_column("Latitude", lambda v: None if v is None else str(v)),
        ),
        Anomaly(
            "type_Counties_to_string",
            "Type changed",
            "Counties delivered as one comma-separated string instead of a list",
            ("schema/type_changed/Counties",),
            _map_column("Counties", lambda v: ", ".join(v) if isinstance(v, list) else v),
        ),
        Anomaly(
            "type_Active_to_string",
            "Type changed",
            'Active delivered as "true"/"false" text',
            ("schema/type_changed/Active",),
            _map_column("Active", lambda v: None if v is None else str(v).lower()),
        ),
    ]
    for column in ("Name", "Latitude"):
        for fraction in (0.3, 0.6, 1.0):
            items.append(
                Anomaly(
                    f"null_{column}_{round(fraction * 100)}",
                    "Populated column goes null",
                    f"{fraction:.0%} of {column} values replaced with null",
                    (f"nulls/null_rate/{column}",),
                    _map_column(column, lambda _v: None, fraction),
                )
            )
    for column in ("AcresBurned", "PersonnelInvolved"):
        items.append(
            Anomaly(
                f"zero_fill_{column}",
                "Nulls replaced with 0",
                f"every null in {column} delivered as 0 (a default leaking through)",
                (f"nulls/null_rate/{column}", f"drift/distribution/{column}"),
                _map_column(column, lambda v: 0 if v is None else v),
            )
        )
    items += [
        Anomaly(
            "swap_lat_lon",
            "Latitude and longitude swapped",
            "the two coordinate columns exchanged",
            ("drift/distribution/Latitude", "drift/distribution/Longitude", "drift/out_of_range"),
            _swap("Latitude", "Longitude"),
        ),
        Anomaly(
            "longitude_sign_lost",
            "Longitude sign lost",
            "Longitude delivered as a positive number",
            ("drift/distribution/Longitude", "drift/out_of_range/Longitude"),
            _map_column("Longitude", lambda v: None if v is None else abs(v)),
        ),
        Anomaly(
            "one_bad_longitude",
            "One absurd coordinate",
            "one Longitude missing its decimal point (the real defect of 2021-07-20)",
            ("drift/out_of_range/Longitude",),
            _map_column("Longitude", lambda v: None if v is None else round(v * 100000), 0.0001),
        ),
    ]
    for delta in (0.5, 2.0):
        items.append(
            Anomaly(
                f"shift_Latitude_{delta:g}",
                "Latitude shifted",
                f"every Latitude moved north by {delta:g} degrees",
                ("drift/distribution/Latitude",),
                _map_column("Latitude", _shifted(delta)),
            )
        )
    for label, factor in (("hectares", 0.4047), ("x10", 10.0), ("x1000", 1000.0)):
        items.append(
            Anomaly(
                f"scale_AcresBurned_{label}",
                "Unit change in AcresBurned",
                f"AcresBurned multiplied by {factor:g}"
                + (" (acres reported as hectares)" if label == "hectares" else ""),
                ("drift/distribution/AcresBurned", "drift/out_of_range/AcresBurned"),
                _map_column("AcresBurned", _scaled(factor)),
            )
        )
    return items
