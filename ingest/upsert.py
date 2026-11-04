"""Turn raw Riot records into table rows, and write them with an upsert.

The upsert does three things in one statement:
  * a new natural ID         -> INSERT the row
  * a known ID, new values   -> UPDATE it and set last_updated_at = now()
  * a known ID, same values  -> do nothing, so last_updated_at stays put
That third case is what makes a rerun produce "0 inserted, 0 updated".
"""

from datetime import datetime, timezone

from psycopg import sql

POSITIONS = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
MIN_REAL_GAME_SECONDS = 300   # shorter games are remakes: someone never loaded in


# --- Transformations: raw API record -> one row ------------------------------

def player_row(entry, tier):
    """One ladder entry. Apex leagues give the tier once for the whole league; Diamond pages give it per entry."""
    return {"puuid": entry["puuid"], "tier": tier, "division": entry["rank"],
            "league_points": entry["leaguePoints"], "wins": entry["wins"], "losses": entry["losses"],
            "hot_streak": entry["hotStreak"], "veteran": entry["veteran"],
            "fresh_blood": entry["freshBlood"], "inactive": entry["inactive"]}


def champion_row(c, version):
    return {"champion_id": int(c["key"]), "champion_key": c["id"], "name": c["name"],
            "title": c["title"], "tags": c.get("tags") or [], "ddragon_version": version}


def match_row(m):
    info = m["info"]
    blue = next(t for t in info["teams"] if t["teamId"] == 100)
    return {"match_id": m["metadata"]["matchId"], "game_version": info["gameVersion"],
            "patch": ".".join(info["gameVersion"].split(".")[:2]),
            "queue_id": info["queueId"],
            "game_start": datetime.fromtimestamp(info["gameStartTimestamp"] / 1000, timezone.utc),
            "duration_seconds": info["gameDuration"], "blue_win": blue["win"]}


def participant_rows(m):
    match_id = m["metadata"]["matchId"]
    return [{"match_id": match_id, "puuid": p["puuid"], "team_id": p["teamId"],
             "team_position": p["teamPosition"] if p.get("teamPosition") in POSITIONS else None,
             "champion_id": p["championId"], "win": p["win"],
             "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
             "champion_level": p["champLevel"], "gold_earned": p["goldEarned"],
             "creep_score": p["totalMinionsKilled"] + p["neutralMinionsKilled"],
             "vision_score": p["visionScore"]}
            for p in m["info"]["participants"]]


def ban_rows(m):
    match_id = m["metadata"]["matchId"]
    return [{"match_id": match_id, "team_id": t["teamId"], "pick_turn": b["pickTurn"], "champion_id": b["championId"]}
            for t in m["info"]["teams"] for b in t.get("bans", [])
            if b["championId"] > 0]   # -1 means the team chose not to ban


def why_filtered(m, ladder_puuids):
    """Return a reason this match should not be stored, or None if it should."""
    info = m["info"]
    if info.get("queueId") != 420:
        return f"queue {info.get('queueId')}"
    if info.get("endOfGameResult") not in (None, "GameComplete"):
        return f"ended: {info.get('endOfGameResult')}"
    if info["gameDuration"] < MIN_REAL_GAME_SECONDS:
        return "remake"
    outside = sum(p["puuid"] not in ladder_puuids for p in info["participants"])
    if outside:
        return f"{outside} of 10 players not on the ladder"
    return None


# --- The upsert ----------------------------------------------------------------

def upsert(cur, table, key_columns, rows):
    """Upsert rows (a list of dicts with the same keys). Returns (inserted, updated).

    Identifiers are quoted with psycopg.sql and every value is a parameter, so
    nothing from the API is ever pasted into the SQL text.
    """
    if not rows:
        return 0, 0
    columns = list(rows[0].keys())
    value_columns = [c for c in columns if c not in key_columns]
    ident = sql.Identifier

    query = sql.SQL("""
        insert into {table} ({cols}) values ({params})
        on conflict ({keys}) do update
            set {assignments}, last_updated_at = now()
            where ({current}) is distinct from ({incoming})
        returning (xmax = 0) as inserted
    """).format(
        table=ident(table),
        cols=sql.SQL(", ").join(map(ident, columns)),
        params=sql.SQL(", ").join(sql.Placeholder(c) for c in columns),
        keys=sql.SQL(", ").join(map(ident, key_columns)),
        assignments=sql.SQL(", ").join(sql.SQL("{c} = excluded.{c}").format(c=ident(c)) for c in value_columns),
        current=sql.SQL(", ").join(sql.SQL("{t}.{c}").format(t=ident(table), c=ident(c)) for c in value_columns),
        incoming=sql.SQL(", ").join(sql.SQL("excluded.{c}").format(c=ident(c)) for c in value_columns),
    )
    # "returning" gives one row per insert or real update; unchanged rows return nothing.
    # xmax = 0 is PostgreSQL's way of saying "this row version was just inserted".
    cur.executemany(query, rows, returning=True)
    inserted = updated = 0
    while True:
        for (was_insert,) in cur.fetchall():
            inserted += was_insert
            updated += not was_insert
        if not cur.nextset():
            break
    return inserted, updated
