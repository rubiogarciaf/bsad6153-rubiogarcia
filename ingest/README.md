# ingest/

Code that moves data from your API into your database. You build it with your AI agent, one piece per week:

| Week | File | What it does |
|---|---|---|
| 1 | `fetch.py` | Calls your API: authentication, pagination, rate limits, retries. Saves raw JSON to `data/raw/`. |
| 2 | `load.py` | Turns the raw JSON into rows in your tables, using parameterized queries. Running it twice must not create duplicates. |
| 3 | `sync.py` | Combines fetch and load into one incremental, idempotent sync that GitHub Actions runs on a schedule and records in `sync_runs`. |

Only the sync connects as the database owner (`DATABASE_URL`). Everything else uses a limited role.

**In this reference build** the Week 3 sync is complete, so fetch and load live inside it:

| File | What it does |
|---|---|
| `riot.py` | The API client: header auth, platform vs regional hosts, pacing from Riot's rate-limit headers, retries, a call budget |
| `upsert.py` | Raw record → table row, the "only if changed" upsert, and the match filter (all ten on the ladder, no remakes) |
| `sync.py` | The daily run: champions → ladder → discover new match IDs → download details. One `sync_runs` row per run. |
