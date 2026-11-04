# BSAD 6153 Project: League of Legends high-elo champion tracker (instructor reference build)

**Student:** Fernando Rubio Garcia
**My decision:** Which champion I should play next, based on what is winning right now in high-elo
North America (Diamond I and above, ranked solo queue)
**Data:** Riot Games API with an approved key, plus Data Dragon. Students: Riot is allowed only if you
already hold an approved key; otherwise pick from the Milestone 1 API menu.
**Website:** &lt;your domain, from Week 4&gt;

This repository is one complete AI system, built over eight weeks: a real API, synced on a schedule into a
PostgreSQL database, shown on a public website, with a chatbot and an agent that answer and act from your
own data. You build it with an AI coding agent, and you read and verify everything it writes.

---

## Setting up your computer (Week 1 Setup)

You need a **Windows 10/11 or macOS computer where you can install software**, Git, Python 3.12, and your AI
coding agent (Claude Code, Codex, Antigravity, or Copilot). Let your agent help with the installs, but read
every command before you approve it, and keep auto-approve off.

1. **Clone this repository** into a short folder path that isn't synced by OneDrive or Dropbox, for example
   `C:\dev\bsad6153` on Windows or `~/dev/bsad6153` on macOS.
2. **Create and activate a virtual environment** from the project folder:

   | | Windows (PowerShell) | macOS (Terminal) |
   |---|---|---|
   | Create | `py -3.12 -m venv .venv` | `python3.12 -m venv .venv` |
   | Activate | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

   You'll see `(.venv)` at the start of your prompt when it's active. Activate it every time you open a new terminal.
3. **Install the packages:** `pip install -r requirements.txt`
4. **Create your secrets file:** copy `.env.example` to `.env`, and put a fake value on the `TEST_SECRET=` line.
5. **Check everything:** `python scripts/check_setup.py` should end with `Setup OK`.
6. **Try the website:** `streamlit run app/streamlit_app.py` opens it in your browser. Press Ctrl+C to stop it.

### Working with your AI agent

- **You approve every command.** Read it first, and if you don't understand it, ask the agent what it does.
- **Ask for a plan before code:** "Before writing anything, tell me your plan."
- **Never paste a real key into the chat,** and never ask the agent to print your `.env` file.
- **The agent never gets the owner database password or deploy tokens.** That's how agents delete databases.
- **Record what you directed, read, verified, and decided** in each pull request's AI review log.

---

## Project layout

| Folder | What goes there | When |
|---|---|---|
| `ingest/` | Fetch, load, and sync code | Weeks 1&ndash;3 |
| `db/` | `schema.sql`, `roles.sql`, `queries.sql` | Weeks 2&ndash;6 |
| `docs/` | Data dictionary, logs, reports (see `docs/README.md`) | Every week |
| `evals/` | Golden questions and evaluation runs | Weeks 3&ndash;7 |
| `app/` | Your website (Streamlit) | Weeks 4&ndash;6 |
| `examples/` | A reference tool-using chatbot loop | Week 5 |
| `scripts/` | Setup and database checks | Weeks 0&ndash;2 |
| `data/sample/` | A small sample of raw API data (large downloads go in `data/raw/`, which Git ignores) | Week 1 |

## Secrets: where each one lives

| Secret | On your computer | On Render (website) | In GitHub Actions (sync) |
|---|---|---|---|
| `DATABASE_URL` (owner) | `.env` | **never** | Actions secret |
| `APP_DATABASE_URL` (read-only) | `.env` | environment setting | if your checks need it |
| `AGENT_DATABASE_URL` | `.env` | environment setting | no |
| LLM key, `APP_PASSWORD` | `.env` | environment setting | no |
| Data API key | `.env` | no | Actions secret |

`.env.example` lists the names only. If a secret ever leaks, revoke or rotate it first, then tell the instructor.

---

## Deploying on Render (Week 4)

1. Claim your free domain through the GitHub Student Developer Pack.
2. In Render, choose **New &gt; Blueprint**, pick this repository, and enter each secret when asked.
   (`render.yaml` describes the service.) Or create a **Web Service** by hand with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `streamlit run app/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
   - Environment variables: `PYTHON_VERSION=3.12.4`, `APP_DATABASE_URL`, and later your LLM key and `APP_PASSWORD`
3. Under **Settings &gt; Custom Domains**, add your domain, then add the DNS records Render shows you at your
   registrar. Ask your agent to explain each record before you add it. HTTPS turns on automatically.
4. Free services sleep when idle and take about a minute to wake. Say so on your site.

---

## Troubleshooting

- **`pip install` fails with "No such file or directory" on Windows:** the folder path is too long. Move the
  project to a short path such as `C:\dev\bsad6153` and recreate `.venv`.
- **PowerShell says running scripts is disabled when you activate:** use Command Prompt instead and run
  `.venv\Scripts\activate.bat`, or ask the instructor. Don't change system security settings without asking.
- **`python` opens the Microsoft Store or isn't found:** use `py -3.12` on Windows or `python3.12` on macOS.
- **The database check can't connect:** use Supabase's Session pooler string, check the password, and check
  that your Supabase project isn't paused.

---

## Project proposal (Week 1)

<!-- About 300 words: data source and backup API, your decision and workflow (from your baseline log),
     your first agent idea, and risks. See the Week 1 page. -->

## How to run this project

Activate `.venv` first. Put `RIOT_API_KEY`, `DATABASE_URL`, and `APP_DATABASE_URL` in `.env`.

| Step | Command | What it does |
|---|---|---|
| Create tables | Paste `db/schema.sql`, then `db/roles.sql`, into the Supabase SQL editor | Seven tables and the read-only role; safe to rerun |
| Sync (manual) | `python ingest/sync.py --max-calls 150` | A small real run, about 3 minutes. Drop the flag for a full run (~50 minutes). |
| Sync (daily) | `.github/workflows/sync.yml` | GitHub runs it at 09:17 UTC; the Actions tab also has a **Run workflow** button |
| Status | `python scripts/status.py` | Last successful update, runs in the last 7 days, what changed |
| Golden questions | `python evals/check_reference.py --save` | Runs every reference query as `app_reader`, saves the answers, reports what changed |
| Check the role | `python scripts/check_reader.py` | As `app_reader`: SELECT works, INSERT and DELETE are denied |
| Business questions | `db/queries.sql` | Five queries answering questions from the decision |
| Quality checks | `db/quality_checks.sql` | Nulls, duplicates, anomalies, queue health, coverage, patch drift |
| Offline tests | `python -m unittest discover tests` | Rate limits, retries, budget, and the match filter, with no network |

**Sync frequency: once a day.** The ladder changes daily, and a personal key allows about 3,000 calls an hour
(100 per 2 minutes). A 2,400-call run takes about 50 minutes: the whole ladder (~47 calls), new match IDs for
about 950 players (highest tier first, then whoever was checked longest ago), and about 1,400 match details.
Anything the budget can't reach waits in `match_queue` for the next run.

**Worst-case freshness:** about 26 hours (a day between runs, plus a 50-minute run, plus GitHub's schedule delay).
If a run fails, the next one catches up from each player's own watermark, so a failed day delays data but
doesn't lose it.

Docs: `docs/data_dictionary.md`, `docs/schema.md`, `docs/data_quality.md`, `docs/sync_tests.md`.

## Credits

Data from the Riot Games API and Data Dragon. This project isn't endorsed by Riot Games and doesn't reflect the
views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties.
Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.
