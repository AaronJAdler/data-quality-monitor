from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FreshnessConfig(_Strict):
    enabled: bool = True
    # A second day in a row with no partition is critical.
    critical_after_missing: int = Field(default=2, ge=1)
    # Report a partition whose newest event timestamp has not moved since the last one.
    check_event_time: bool = True


class VolumeConfig(_Strict):
    enabled: bool = True
    window: int = Field(default=14, ge=3)
    min_history: int = Field(default=7, ge=3)
    z_warning: float = 3.5  # the cut-off Iglewicz and Hoaglin recommend for the modified z-score
    z_critical: float = 7.0
    min_mad_rows: float = 1.0
    min_mad_fraction: float = 0.01  # of the median, so large tables do not alert on tiny changes


class SchemaConfig(_Strict):
    enabled: bool = True
    window: int = Field(default=14, ge=1)


class NullsConfig(_Strict):
    enabled: bool = True
    window: int = Field(default=14, ge=3)
    min_history: int = Field(default=7, ge=3)
    alpha: float = 0.001
    min_change: float = 0.25  # absolute change in null rate that is worth an alert


class DriftConfig(_Strict):
    enabled: bool = True
    window: int = Field(default=14, ge=3)
    min_history: int = Field(default=7, ge=3)
    min_values: int = Field(default=8, ge=2)  # non-null values needed in the partition
    min_baseline_values: int = Field(default=30, ge=2)
    alpha: float = 0.001
    min_psi: float = 0.25  # practical-significance floor, on top of the p-value
    max_bins: int = Field(default=10, ge=2)
    values_per_bin: int = Field(default=5, ge=1)
    # A value further outside the baseline's range than this many range-widths is an outlier.
    range_factor: float = 3.0
    columns: list[str] | None = None  # None means every numeric column


class Monitors(_Strict):
    freshness: FreshnessConfig = FreshnessConfig()
    volume: VolumeConfig = VolumeConfig()
    schema_: SchemaConfig = Field(default=SchemaConfig(), alias="schema")
    nulls: NullsConfig = NullsConfig()
    drift: DriftConfig = DriftConfig()


class Source(_Strict):
    path: str  # directory of <YYYY-MM-DD>.json.gz files
    records_key: str | None = None  # key holding the list of records, if the file is an object
    arrived_at_key: str | None = None
    event_time_column: str | None = None


class Match(_Strict):
    dataset: list[str] | None = None
    monitor: list[str] | None = None
    severity: list[Literal["info", "warning", "critical"]] | None = None


class Route(_Strict):
    match: Match = Match()
    channels: list[str]


class Channel(_Strict):
    type: Literal["file", "webhook"]
    path: str | None = None
    url_env: str | None = None  # the webhook URL is read from this environment variable


class Routing(_Strict):
    routes: list[Route] = []
    channels: dict[str, Channel] = {}
    renotify_after_days: int = Field(default=3, ge=1)


class DatasetConfig(_Strict):
    dataset: str
    owner: str = "unowned"
    source: Source
    monitors: Monitors = Monitors()
    routing: Routing = Routing()

    @classmethod
    def load(cls, path: Path) -> DatasetConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
