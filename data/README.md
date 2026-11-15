# Data

`ca-fires-2021/` holds one gzipped JSON file per UTC day, from 2021-07-15 to 2021-10-15. Each
file is the last state that day of CAL FIRE's public incident feed, as recorded by
[simonw/ca-fires-history](https://github.com/simonw/ca-fires-history), a Git-scraping project
that stored each change to the feed as a commit.

The files are copied unchanged from
[ca-wildfire-elt](https://github.com/AaronJAdler/ca-wildfire-elt) (`data/replay/`), where
`scripts/build_replay_fixture.py` builds them from a clone of the source repository. That
project's notes apply here too:

- There are 90 files in a 93-day window. No commits exist for October 4 to 6, 2021, so those
  days are a real source outage and were not removed.
- Narrative HTML fields were dropped to keep the files small: `ConditionStatement`,
  `ControlStatement`, `SearchDescription`, `SearchKeywords` and `ExternalIncidentLink`. All
  other fields are as published.
- Each file is an object with `incidents` (the records), `source_commit`,
  `source_committed_at` (when the snapshot was recorded) and `feed_all_acres`.

Nothing in this directory is generated or altered by this project. The backtest injects its
defects in memory and stores none of them.
