# Automated Riot match collection

## Schedule and scope

GitHub Actions runs `.github/workflows/sync.yml` daily at **09:17 UTC**
(3:17 a.m. Central Standard Time / 4:17 a.m. Central Daylight Time).
The workflow must be on the repository's default branch (`main`) for the schedule
to run. GitHub may delay scheduled jobs. Your computer can be turned off.

Each invocation starts a fresh, private collection directory and saves a fixed
48-hour UTC window. The overlap helps with delayed runs and matches that were
unfinished during the previous collection. This is not a complete backfill after
a multi-day outage, nor complete population coverage. The daily profile limits
collection to 1,200 Riot calls, 300 player histories, and 550 downloaded details.
The smoke profile uses 80 calls, 20 histories, and 40 details. Retries count toward
the call budget. Runtime depends on API pacing; the job stops after 45 minutes.
One workflow run at a time prevents overlapping scheduled/manual writers.

The source remains NA ranked solo, discovered through Challenger, Grandmaster,
and Master ladders with interleaved tier sampling. At least one selected ladder
player must appear in each accepted game; all ten participants are retained.

## Credentials

In GitHub **Settings → Secrets and variables → Actions**, configure repository
secrets named `RIOT_API_KEY` and `DATABASE_URL`. Never paste values into an issue,
chat, workflow YAML, screenshot, or committed file.

- `RIOT_API_KEY`: use an approved personal/production key. Development keys
  deactivate after 24 hours and cannot sustain unattended collection.
- `DATABASE_URL`: use the Supabase **Session pooler** PostgreSQL URI (port 5432)
  for the existing five-table database. Use a database writer account, not
  `app_reader` and not a Supabase Data API key. GitHub-hosted IPv4 runners need
  the pooler when the direct endpoint is IPv6-only. TLS is required by the job.

The workflow does not create or reset any tables. The existing Supabase database
must contain `players`, `champions`, `ladder_snapshots`, `matches`, and
`participants` with the columns expected by `ingest/load.py`. These tables and
the champion audit trigger were checked read-only during setup. Older repository
schema files should not be reapplied to this live database.

## First run and monitoring

1. Open **Actions → Daily Riot match collection → Run workflow**.
2. Select `main`, choose `smoke`, then run the workflow.
3. Confirm the run is green. Its Summary shows the UTC window, API calls,
   histories checked, new matches/participants, and total matches.
4. Download the `riot-sync-report` artifact for aggregate coverage and integrity
   measurements. Reports are retained for 14 days. No raw inputs, manifests,
   player IDs, key fingerprints, or credentials are uploaded.
5. Check the next automatically triggered run to confirm the schedule itself.

API request errors, database constraint failures, missing secrets, and stored
games without ten participants cause a red run. Budget-limited sampling and
out-of-window filtering are expected; zero new matches alone is not a failure.
For `AuthError`, verify the Riot secret. For database errors, verify the session
pooler secret, database availability, and writer permissions. Configure GitHub
Actions notifications in your account if you want failure emails.

## Duplicate handling, references, and recovery

Completed stored matches are skipped before detail downloads. Natural keys and
parameterized conflict handling prevent duplicate match/participant rows.
Each accepted match and its participants are committed together. Match facts
and historical ladder observations are immutable; each new run adds a new
timestamped ladder snapshot. Champion reference facts refresh only when changed;
the existing database audit trigger preserves insertion time and updates the
change timestamp. Reference counts in reports mean inserted or changed rows.

The historical M2 replay command still defaults to its saved 24-hour window and
insert-only behavior. Replay cannot be combined with reference refresh.

Raw cache files exist only on the ephemeral runner. A rerun starts a new
observation, skips complete games already committed, and recollects within the
new window. It does not resume the exact failed run's manifest. Local collection
with a retained directory still supports exact resume/replay. Longer outages
require a separately planned backfill; increasing the window also changes the
sampling and call-budget requirements.

To change the schedule, edit the workflow's cron expression. To pause, use
**Actions → Daily Riot match collection → … → Disable workflow**. Public
repositories can have scheduled workflows disabled after 60 days without
repository activity; check Actions and re-enable when needed.

References: [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets),
[Riot key types](https://support-developer.riotgames.com/hc/en-us/articles/22698431229203-Developer-Portal-Overview),
[Supabase connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres).
