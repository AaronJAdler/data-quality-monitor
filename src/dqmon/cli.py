from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from dqmon import backtest
from dqmon.config import DatasetConfig
from dqmon.engine import deliver, run_date
from dqmon.profile import list_partitions
from dqmon.store import Store


def _print_run(day: dt.date, events: list[str], skipped: int) -> None:
    note = f"  ({skipped} checks skipped for lack of data)" if skipped else ""
    print(f"== {day.isoformat()}{note}")
    for line in events:
        print(f"   {line}")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = DatasetConfig.load(Path(args.config))
    store = Store(args.state)
    try:
        result = run_date(store, cfg, dt.date.fromisoformat(args.date))
        lines = [f"{e.kind:<9} {e.severity.label:<8} {e.key}: {e.text}" for e in result.events]
        _print_run(result.evaluation.partition, lines, len(result.evaluation.skipped))
        print(f"open alerts: {len(result.open_alerts)}")
        if not args.no_deliver:
            print(f"notifications: {deliver(store, cfg)}")
    finally:
        store.close()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Run every calendar day in a range through the production path and print what changed."""
    cfg = DatasetConfig.load(Path(args.config))
    partitions = list_partitions(cfg.source)
    start = dt.date.fromisoformat(args.start) if args.start else partitions[0]
    end = dt.date.fromisoformat(args.end) if args.end else partitions[-1]
    store = Store(args.state)
    counts: dict[str, int] = {}
    try:
        day = start
        while day <= end:
            result = run_date(store, cfg, day)
            shown = [e for e in result.events if e.kind != "notice" or args.verbose]
            for e in result.events:
                counts[e.kind] = counts.get(e.kind, 0) + 1
            if shown:
                lines = [f"{e.kind:<9} {e.severity.label:<8} {e.key}: {e.text}" for e in shown]
                _print_run(day, lines, 0)
            day += dt.timedelta(days=1)
        if not args.no_deliver:
            print(f"notifications: {deliver(store, cfg)}")
        print(f"events: {dict(sorted(counts.items()))}")
    finally:
        store.close()
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = DatasetConfig.load(Path(args.config))
    summary = backtest.summarise(cfg, backtest.run(cfg))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "backtest.md").write_text(backtest.render_markdown(summary), encoding="utf-8")
    serious = sum(1 for f in summary["history_findings"] if f["severity"] != "info")
    caught = sum(a["caught_by_expected_monitor"] for a in summary["anomalies"])
    print(
        f"{summary['partitions']} partitions: {serious} warning or critical findings on "
        f"unmodified history; {caught} of {summary['trials']} injected defects caught by the "
        f"intended monitor. Wrote {out / 'backtest.md'}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dqmon", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default="datasets/ca_fires.yaml")

    run = sub.add_parser("run", help="check one partition date")
    common(run)
    run.add_argument("--date", required=True)
    run.add_argument("--state", default="state/dqmon.duckdb")
    run.add_argument("--no-deliver", action="store_true")
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="check every day in a range, in order")
    common(replay)
    replay.add_argument("--start")
    replay.add_argument("--end")
    replay.add_argument("--state", default=":memory:")
    replay.add_argument("--no-deliver", action="store_true")
    replay.add_argument("--verbose", action="store_true", help="also print informational notices")
    replay.set_defaults(func=cmd_replay)

    bt = sub.add_parser("backtest", help="inject defects into real history and score the monitors")
    common(bt)
    bt.add_argument("--out", default="docs")
    bt.set_defaults(func=cmd_backtest)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
