"""Week 3 (Milestone 3): one incremental, idempotent sync. GitHub Actions runs it daily.

    python ingest/sync.py                    # a full run (default budget: 2,400 API calls)
    python ingest/sync.py --max-calls 150    # a small test run, a few minutes

What one run does, in order:

  1. Champions   Refresh champion names from Data Dragon (no key, no API calls counted).
  2. Ladder      Read today's high-elo NA ladder: Challenger, Grandmaster, Master, Diamond I.
                 About 20,000 players in ~47 calls. Rank and LP changes become updates.
  3. Discover    For as many players as the budget allows (highest tier first, then whoever
                 was checked longest ago), ask for ranked games since THAT player's last check.
                 New match IDs go into match_queue. Each player's watermark moves only in the
                 same transaction that saves their match IDs, so a crash can't skip games.
  4. Download    Fetch details for queued matches, oldest first. Keep a match only if all ten
                 players are on today's ladder and it isn't a remake. Whatever the budget can't
                 reach stays queued for tomorrow.

Every run writes one row to sync_runs, even if it fails. Exit code 1 on failure, so
GitHub shows a red X and emails you.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from riot import ApiError, AuthError, BudgetExhausted, RiotClient, data_dragon_champions  # noqa: E402
from upsert import (ban_rows, champion_row, match_row, participant_rows, player_row,  # noqa: E402
                    upsert, why_filtered)

APEX_TIERS = ("challenger", "grandmaster", "master")
TIER_ORDER = "case tier when 'CHALLENGER' then 1 when 'GRANDMASTER' then 2 when 'MASTER' then 3 else 4 end"
FIRST_WINDOW = timedelta(hours=24)   # a player we've never checked: look back one day
OVERLAP = timedelta(hours=1)         # a game in progress at the last check finishes later; look back a bit
MAX_DETAIL_ATTEMPTS = 3


def log(message):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {message}", flush=True)


def safe_message(error):
    """First line only. Our own errors never include the key; database errors never include the password."""
    return f"{type(error).__name__}: {str(error).strip().splitlines()[0] if str(error).strip() else ''}"[:500]


# --- sync_runs ------------------------------------------------------------------

def start_run(conn, trigger):
    # Written and committed FIRST, as "failed / did not finish". If the process dies,
    # the row already tells the truth. A normal finish overwrites it.
    run_id = conn.execute(
        "insert into sync_runs (status, trigger, error_message) values ('failed', %s, 'run did not finish') "
        "returning run_id", (trigger,)).fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id, status, stats, error=None):
    conn.rollback()  # throw away anything half-done before writing the result
    pending = conn.execute("select count(*) from match_queue where status = 'pending'").fetchone()[0]
    conn.execute("""
        update sync_runs set finished_at = now(), status = %(status)s, error_message = %(error)s,
            records_fetched = %(fetched)s, records_inserted = %(inserted)s, records_updated = %(updated)s,
            api_calls = %(calls)s, players_checked = %(players_checked)s,
            matches_filtered = %(filtered)s, queue_pending = %(pending)s
        where run_id = %(run_id)s""",
        {**stats, "status": status, "error": error, "pending": pending, "run_id": run_id})
    conn.commit()
    return pending


# --- The four steps ----------------------------------------------------------------

def refresh_champions(conn, stats):
    try:
        version, champions = data_dragon_champions()
    except Exception as error:  # noqa: BLE001 - yesterday's names still work; note it and go on
        stats["errors"] += 1
        log(f"champions: Data Dragon unavailable, keeping stored names ({safe_message(error)})")
        return
    with conn.transaction():
        inserted, updated = upsert(conn.cursor(), "champions", ["champion_id"],
                                   [champion_row(c, version) for c in champions])
    log(f"champions: {len(champions)} from Data Dragon {version} ({inserted} new, {updated} changed)")


def refresh_ladder(conn, client, stats, ladder_time):
    rows = []
    for tier in APEX_TIERS:
        rows += [player_row(e, tier.upper()) for e in client.apex_league(tier)]
    rows += [player_row(e, "DIAMOND") for e in client.tier_entries("DIAMOND", "I")]
    rows = list({r["puuid"]: r for r in rows}.values())  # a player promoted mid-fetch can appear twice

    with conn.transaction():
        cur = conn.cursor()
        inserted, updated = upsert(cur, "players", ["puuid"], rows)
        # Bookkeeping, kept out of the upsert so it doesn't count as "this player changed".
        cur.execute("update players set last_seen_on_ladder_at = %s where puuid = any(%s)",
                    (ladder_time, [r["puuid"] for r in rows]))
    stats["inserted"] += inserted
    stats["updated"] += updated
    log(f"ladder: {len(rows)} players ({inserted} new, {updated} changed rank/LP/record)")
    return {r["puuid"] for r in rows}


def discover(conn, client, stats, ladder_time, call_budget):
    players = conn.execute(f"""
        select puuid, matches_checked_at from players
        where last_seen_on_ladder_at = %s
        order by {TIER_ORDER}, matches_checked_at nulls first, league_points desc
        limit %s""", (ladder_time, call_budget)).fetchall()

    stop_at = client.calls_made + call_budget
    found = 0
    for puuid, checked_at in players:
        if client.calls_made >= stop_at:
            break
        asked_at = datetime.now(timezone.utc)
        since = (checked_at - OVERLAP) if checked_at else (asked_at - FIRST_WINDOW)
        try:
            ids = client.match_ids_since(puuid, since.timestamp())
        except AuthError:
            raise
        except BudgetExhausted:
            break
        except ApiError as error:
            stats["errors"] += 1
            log(f"  skipped one player: {error}")
            continue
        with conn.transaction():  # IDs and the watermark move together, or not at all
            cur = conn.cursor()
            if ids:
                cur.executemany("insert into match_queue (match_id) values (%s) on conflict do nothing",
                                [(i,) for i in ids])
                found += cur.rowcount if cur.rowcount > 0 else 0
            cur.execute("update players set matches_checked_at = %s where puuid = %s", (asked_at, puuid))
        stats["players_checked"] += 1
    log(f"discover: checked {stats['players_checked']} players, {found} new match IDs queued")


def download(conn, client, stats, ladder_puuids):
    known_champions = {r[0] for r in conn.execute("select champion_id from champions")}
    queued = conn.execute("select match_id, attempts from match_queue where status = 'pending' "
                          "order by discovered_at, match_id limit %s", (max(client.calls_left, 0),)).fetchall()
    for match_id, attempts in queued:
        try:
            m = client.match(match_id)
        except (AuthError, BudgetExhausted):
            raise
        except ApiError as error:
            stats["errors"] += 1
            final = "404" in str(error) or attempts + 1 >= MAX_DETAIL_ATTEMPTS
            conn.execute("update match_queue set attempts = attempts + 1, reason = %s, status = %s, "
                         "resolved_at = case when %s then now() end where match_id = %s",
                         (safe_message(error), "failed" if final else "pending", final, match_id))
            conn.commit()
            continue
        stats["fetched"] += 1

        reason = why_filtered(m, ladder_puuids)
        unknown = {p["championId"] for p in m["info"]["participants"]} - known_champions
        if reason is None and unknown:
            # A champion newer than Data Dragon's list. Leave it queued; tomorrow's names will include it.
            conn.execute("update match_queue set attempts = attempts + 1, reason = %s where match_id = %s",
                         (f"unknown champion id(s) {sorted(unknown)}", match_id))
            conn.commit()
            continue

        try:
            with conn.transaction():  # the match, its ten players, its bans, and its queue status: all or nothing
                cur = conn.cursor()
                if reason:
                    cur.execute("update match_queue set status = 'filtered_out', reason = %s, resolved_at = now() "
                                "where match_id = %s", (reason, match_id))
                else:
                    inserted, _ = upsert(cur, "matches", ["match_id"], [match_row(m)])
                    upsert(cur, "participants", ["match_id", "puuid"], participant_rows(m))
                    upsert(cur, "bans", ["match_id", "team_id", "pick_turn"], ban_rows(m))
                    cur.execute("update match_queue set status = 'stored', reason = null, resolved_at = now() "
                                "where match_id = %s", (match_id,))
        except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as error:
            # A value broke one of our CHECK rules: the data surprised us. That match is rolled
            # back and set aside with the reason; the rest of the run goes on, and the run is "partial".
            stats["errors"] += 1
            conn.execute("update match_queue set status = 'failed', reason = %s, attempts = attempts + 1, "
                         "resolved_at = now() where match_id = %s", (safe_message(error), match_id))
            conn.commit()
            log(f"  {match_id} broke a rule and was set aside: {safe_message(error)}")
            continue
        if reason:
            stats["filtered"] += 1
        else:
            stats["inserted"] += inserted
    log(f"download: {stats['fetched']} fetched, {stats['fetched'] - stats['filtered']} kept, "
        f"{stats['filtered']} filtered out")


# --- Entry point ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Daily high-elo NA sync")
    parser.add_argument("--max-calls", type=int, default=int(os.environ.get("SYNC_MAX_CALLS", "2400")),
                        help="API call budget for this run (default 2400, about 50 minutes on a personal key)")
    parser.add_argument("--discover-share", type=float, default=0.4,
                        help="share of the budget left after the ladder that goes to finding new matches")
    parser.add_argument("--trigger", choices=("schedule", "manual"),
                        default=os.environ.get("SYNC_TRIGGER", "manual"))
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("[FAIL] DATABASE_URL is not set (.env locally, an Actions secret on GitHub).")
    if not os.environ.get("RIOT_API_KEY"):
        sys.exit("[FAIL] RIOT_API_KEY is not set (.env locally, an Actions secret on GitHub).")

    client = RiotClient(platform=os.environ.get("RIOT_PLATFORM", "na1"),
                        region=os.environ.get("RIOT_REGION", "americas"), max_calls=args.max_calls)
    stats = dict(fetched=0, inserted=0, updated=0, calls=0, players_checked=0, filtered=0, errors=0)
    started = time.monotonic()

    # autocommit=True: each "with conn.transaction()" block is a real BEGIN...COMMIT.
    # Without it, the first SELECT silently opens a transaction, every block after it
    # becomes a savepoint inside that one, and a single rollback at the end erases them all.
    # (Our first test run reported "success" while saving nothing. Always check the tables.)
    with psycopg.connect(url, connect_timeout=15, autocommit=True) as conn:
        run_id = start_run(conn, args.trigger)
        log(f"run {run_id} started ({args.trigger}), budget {args.max_calls} calls")
        ladder_time = datetime.now(timezone.utc)
        try:
            refresh_champions(conn, stats)
            ladder = refresh_ladder(conn, client, stats, ladder_time)
            discover(conn, client, stats, ladder_time, int(client.calls_left * args.discover_share))
            try:
                download(conn, client, stats, ladder)
            except BudgetExhausted:
                log("download: budget used up; the rest stays queued for the next run")
        except (KeyboardInterrupt, Exception) as error:  # noqa: BLE001 - every failure must be recorded
            stats["calls"] = client.calls_made
            message = "interrupted (Ctrl+C or job cancelled)" if isinstance(error, KeyboardInterrupt) \
                else safe_message(error)
            finish_run(conn, run_id, "failed", stats, message)
            log(f"run {run_id} FAILED after {client.calls_made} calls: {message}")
            sys.exit(1)

        stats["calls"] = client.calls_made
        status = "partial" if stats["errors"] else "success"
        note = f"{stats['errors']} item(s) skipped after errors; see the log" if stats["errors"] else None
        pending = finish_run(conn, run_id, status, stats, note)
    log(f"run {run_id} {status}: {stats['inserted']} inserted, {stats['updated']} updated, "
        f"{client.calls_made} calls, {pending} still queued, {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
