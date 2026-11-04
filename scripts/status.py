"""Week 3: when was my data last updated, and what changed in the last 7 days?

    python scripts/status.py

Connects as app_reader (APP_DATABASE_URL): reading the status never needs write access.
In Week 4 the website shows this; in Week 5 the chatbot uses it to warn when data is stale.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

STALE_AFTER_HOURS = int(os.environ.get("FRESHNESS_HOURS", "36"))  # daily schedule + GitHub delays

load_dotenv()
url = os.environ.get("APP_DATABASE_URL")
if not url:
    sys.exit("[FAIL] APP_DATABASE_URL is not set. Add the app_reader connection string to .env.")

with psycopg.connect(url, connect_timeout=10) as conn:
    last = conn.execute("""
        select finished_at, round(extract(epoch from now() - finished_at) / 3600, 1) as hours_ago
        from sync_runs where status in ('success', 'partial') order by finished_at desc limit 1""").fetchone()
    if last is None:
        print("No successful sync yet.")
    else:
        flag = "  ** STALE **" if last[1] > STALE_AFTER_HOURS else ""
        print(f"Last successful update: {last[0]:%Y-%m-%d %H:%M} UTC ({last[1]} hours ago){flag}")

    print("\nRuns in the last 7 days:")
    print(f"  {'started (UTC)':17} {'trigger':8} {'status':8} {'calls':>6} {'fetched':>8} {'inserted':>9} "
          f"{'updated':>8} {'queued':>7}  error")
    for r in conn.execute("""
            select started_at, trigger, status, api_calls, records_fetched, records_inserted,
                   records_updated, queue_pending, coalesce(error_message, '')
            from sync_runs where started_at > now() - interval '7 days' order by started_at"""):
        print(f"  {r[0]:%Y-%m-%d %H:%M} {r[1]:8} {r[2]:8} {r[3]:6} {r[4]:8} {r[5]:9} {r[6]:8} "
              f"{r[7] if r[7] is not None else '':>7}  {r[8][:60]}")

    stored, newest, patches = conn.execute("""
        select count(*), max(game_start), string_agg(distinct patch, ', ')
        from matches where first_seen_at > now() - interval '7 days'""").fetchone()
    moved = conn.execute("select count(*) from players where last_updated_at > now() - interval '7 days' "
                         "and last_updated_at > first_seen_at").fetchone()[0]
    joined = conn.execute("select count(*) from players where first_seen_at > now() - interval '7 days'").fetchone()[0]
    print("\nWhat changed in the last 7 days:")
    print(f"  {stored} matches stored" + (f", newest game {newest:%Y-%m-%d %H:%M} UTC, patches {patches}" if stored else ""))
    print(f"  {joined} players added to the ladder; {moved} players' rank, LP, or record changed")
