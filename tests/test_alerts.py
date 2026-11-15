from __future__ import annotations

import datetime as dt

from dqmon import alerts
from dqmon.config import Channel, Match, Route, Routing
from dqmon.models import Evaluation, Finding, Severity

DAY = dt.date(2024, 3, 1)


def finding(severity: Severity = Severity.WARNING, monitor: str = "volume") -> Finding:
    return Finding(monitor, "row_count", None, severity, "5 rows", "about 40 rows", "z = -9")


def evaluation(day: int, *findings: Finding, checked: tuple[str, ...] = ("volume/",)) -> Evaluation:
    return Evaluation("orders", DAY + dt.timedelta(days=day), findings, frozenset(checked))


def run(evaluations: list[Evaluation], renotify: int = 3) -> list[list[str]]:
    state: dict[str, alerts.Alert] = {}
    kinds = []
    for e in evaluations:
        state, events = alerts.step(state, e, (), renotify)
        kinds.append([ev.kind for ev in events])
    return kinds


def test_a_repeating_finding_is_one_alert_with_a_reminder() -> None:
    days = [evaluation(d, finding()) for d in range(5)] + [evaluation(5)]
    assert run(days) == [["opened"], [], [], ["reminder"], [], ["resolved"]]


def test_escalation_notifies_again() -> None:
    days = [evaluation(0, finding()), evaluation(1, finding(Severity.CRITICAL))]
    assert run(days) == [["opened"], ["escalated"]]


def test_a_run_that_could_not_check_does_not_resolve() -> None:
    missing = Finding("freshness", "missing_partition", None, Severity.WARNING, "-", "-", "-")
    days = [
        evaluation(0, finding()),
        evaluation(1, missing, checked=("freshness/missing_partition/-",)),
        evaluation(2, checked=("volume/", "freshness/missing_partition/-")),
    ]
    assert run(days) == [["opened"], ["opened"], ["resolved", "resolved"]]


def test_informational_findings_are_notices_not_alerts() -> None:
    state, events = alerts.step({}, evaluation(0, finding(Severity.INFO)), (), 3)
    assert state == {} and [e.kind for e in events] == ["notice"]


def test_adopted_alert_says_so_when_it_closes() -> None:
    state, _ = alerts.step({}, evaluation(0, finding()), (), 3)
    state, events = alerts.step(state, evaluation(1, finding()), {"volume/row_count/-"}, 3)
    assert [e.kind for e in events] == ["adopted"]
    state, events = alerts.step(state, evaluation(2), (), 3)
    assert state == {} and "baseline was adopted" in events[0].text


def test_event_ids_are_stable_and_distinct() -> None:
    _, first = alerts.step({}, evaluation(0, finding()), (), 3)
    _, again = alerts.step({}, evaluation(0, finding()), (), 3)
    _, other = alerts.step({}, evaluation(1, finding()), (), 3)
    assert first[0].id == again[0].id != other[0].id


def test_routing_groups_events_per_channel() -> None:
    routing = Routing(
        channels={
            "pager": Channel(type="webhook", url_env="X"),
            "owners": Channel(type="file", path="o"),
        },
        routes=[
            Route(match=Match(severity=["critical"]), channels=["pager", "owners"]),
            Route(match=Match(severity=["warning"]), channels=["owners"]),
            Route(match=Match(monitor=["schema"]), channels=["owners"]),
        ],
    )
    schema = Finding("schema", "column_removed", "id", Severity.CRITICAL, "-", "-", "-")
    _, events = alerts.step({}, evaluation(0, finding(), schema), (), 3)
    sent = {n.channel: n for n in alerts.route(routing, "orders", "sales", DAY, events)}
    assert set(sent) == {"owners", "pager"}
    assert len(sent["owners"].events) == 2  # both events, each listed once
    assert len(sent["pager"].events) == 1
    assert sent["owners"].id != sent["pager"].id
    assert "owner: sales" in sent["owners"].text


def test_unmatched_events_go_nowhere() -> None:
    routing = Routing(routes=[Route(match=Match(dataset=["other"]), channels=["x"])])
    _, events = alerts.step({}, evaluation(0, finding()), (), 3)
    assert alerts.route(routing, "orders", "sales", DAY, events) == []
