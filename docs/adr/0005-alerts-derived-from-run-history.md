# 5. Alerts are derived from run history, and missing data never resolves one

- Status: Accepted
- Date: 2026-11-14

## Context

A finding is what a monitor saw on one day. Sending one message per finding per day turns a
three-day outage into three pages and a week-long shift into seven. People need to hear about
problems, once, and about changes to them.

Alert state kept in its own table can also fall out of step with the evaluations it came from,
especially when a day is rerun.

## Decision

- An alert is identified by `monitor/kind/column`. It opens on the first warning or critical
  finding and stays open while the finding repeats.
- Notifications are sent for transitions only: `opened`, `escalated` (severity rose),
  `reminder` (still firing 3 days after the last notification), `adopted` and `resolved`.
- An alert resolves only when a run really checked its key and found nothing. A missing
  partition checks only freshness, so it cannot close a volume or schema alert. A drift check
  that was skipped for lack of values cannot close a drift alert.
- Only `runs` (profile and evaluation per date) and `outbox` are stored. The baseline and the
  open alerts are rebuilt by replaying earlier runs through the same pure `step` function.
- Each event's id is a hash of its kind, key, opening date and partition. A notification's id
  is a hash of its events and channel. The run row and the outbox rows are written in one
  transaction, and the id is sent as `Idempotency-Key`.
- Events are grouped into one notification per channel per run. Routes match on dataset,
  monitor and severity; an event goes to the union of every matching route's channels.

## Consequences

- The real October outage produced three notifications to the owners: opened (warning),
  escalated (critical), resolved. The third missing day sent nothing.
- Rerunning a date with the same outcome inserts no outbox rows.
- Delivery is at-least-once. A failed webhook stays pending and is retried with the same key.
- Rebuilding state costs one pass over the history per run: fine for daily partitions, too slow
  for thousands a day, where a snapshot of the state would be stored and checked against a
  replay.
- Reprocessing an old date does not re-evaluate the dates after it.

## Alternatives considered

- **An alerts table updated in place**: faster, and one more thing that must stay consistent.
- **Resolve when a finding is absent**: simpler, and sends "resolved" during an outage.
