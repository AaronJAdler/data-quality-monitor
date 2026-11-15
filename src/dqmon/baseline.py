"""Which partitions may serve as the baseline.

A partition with a warning or critical finding is quarantined: it is profiled and stored, but
later partitions are not compared with it, so one bad day cannot teach the monitors that bad is
normal. If the same finding then repeats on `adopt_after` consecutive partitions, it is treated
as a lasting change: those partitions are adopted into the baseline and the alert is closed as
"adopted", which is a different outcome from "resolved".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from dqmon.models import Evaluation, Profile, Severity


@dataclass
class Baseline:
    adopt_after: int = 3
    profiles: list[Profile] = field(default_factory=list)  # every partition that arrived
    quarantined: dict[dt.date, frozenset[str]] = field(default_factory=dict)
    missing_streak: int = 0

    def history(self) -> list[Profile]:
        return [p for p in self.profiles if p.partition not in self.quarantined]

    @property
    def previous(self) -> Profile | None:
        return self.profiles[-1] if self.profiles else None

    def record(self, profile: Profile | None, evaluation: Evaluation) -> frozenset[str]:
        """Store the outcome of one date. Returns the finding keys adopted as the new normal."""
        if profile is None:
            self.missing_streak += 1
            return frozenset()
        self.missing_streak = 0
        self.profiles.append(profile)
        keys = frozenset(
            f.key
            for f in evaluation.findings
            if f.severity >= Severity.WARNING and f.monitor != "freshness"
        )
        if not keys:
            return frozenset()
        self.quarantined[profile.partition] = keys

        recent = self.profiles[-self.adopt_after :]
        if len(recent) < self.adopt_after:
            return frozenset()
        if any(p.partition not in self.quarantined for p in recent):
            return frozenset()
        lasting = frozenset.intersection(*(self.quarantined[p.partition] for p in recent))
        if not lasting:
            return frozenset()
        for p in recent:
            remaining = self.quarantined[p.partition] - lasting
            if remaining:
                self.quarantined[p.partition] = remaining
            else:
                del self.quarantined[p.partition]
        return lasting
