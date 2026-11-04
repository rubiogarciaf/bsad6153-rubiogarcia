# Data dictionary

**Source:** Riot Games API (league-v4, match-v5) and Data Dragon (champion names). Region: North America.
**Population:** ranked solo queue (queue 420), players on the Challenger, Grandmaster, Master, and Diamond I ladders.
**Key limits, read from response headers on 2026-11-03:** `X-App-Rate-Limit: 20:1,100:120` (20 calls per second, 100 per 2 minutes). `ingest/riot.py` reads this header and paces itself to it.

## Resources and how they relate

| Resource | Endpoint (host) | Size on 2026-11-04 | One record means |
|---|---|---|---|
| Apex leagues | `/lol/league/v4/{challenger,grandmaster,master}leagues/by-queue/RANKED_SOLO_5x5` (`na1`) | 300 / 700 / 10,000 | One player's place on that ladder. One call returns the whole league. |
| Diamond I entries | `/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I?page=N` (`na1`) | ~8,960 across 44 pages of 205 | Same, one page at a time |
| Match IDs | `/lol/match/v5/matches/by-puuid/{puuid}/ids?startTime=&queue=420&start=&count=100` (`americas`) | Top player: 8 games in 24 hours | One game ID this player played after `startTime` |
| Match | `/lol/match/v5/matches/{matchId}` (`americas`) | ~156 fields per participant | **One finished game**: two teams, ten players, bans |
| Champions | Data Dragon `champion.json` (no key) | 173 (version 16.22.1) | One champion |

How they connect: a ladder entry's `puuid` → that player's match IDs → each match lists ten `puuid`s and ten `championId`s → `championId` = Data Dragon's `key`.

**Two hosts.** league-v4 uses the platform host (`na1`); match-v5 uses the regional host (`americas`). Using the wrong one returns an error.

## Fields we keep

### Ladder entry → `players`

| Field | Meaning | Notes |
|---|---|---|
| `puuid` | Player ID | **Encrypted per API key.** A different key (e.g. switching from a development to a production key) gives different PUUIDs for the same people. Don't mix data from two keys. |
| `tier` | CHALLENGER, GRANDMASTER, MASTER, DIAMOND | Apex leagues give it once per league; Diamond pages give it per entry |
| `rank` → `division` | I–IV | Always I in apex tiers |
| `leaguePoints` | LP | Challenger LP reached 4,317 on 2026-11-04 |
| `wins`, `losses` | This season's ranked record | |
| `hotStreak`, `veteran`, `freshBlood`, `inactive` | Riot's flags | |

### Match → `matches`

| Field | Meaning | Notes |
|---|---|---|
| `metadata.matchId` | Game ID, e.g. `NA1_5672929226` | Primary key |
| `info.gameVersion` | e.g. `16.21.834.2754` | We derive `patch` = `16.21` |
| `info.queueId` | 420 = ranked solo/duo | Only 420 is stored |
| `info.gameStartTimestamp` | Milliseconds since 1970, UTC | `startTime` in the match-ID query is in **seconds**. Mixing them up returns nothing. |
| `info.gameDuration` | Seconds | Under 300 = remake, not stored |
| `info.endOfGameResult` | `GameComplete` for a normal finish | |
| `teams[].win` for team 100 | → `blue_win` | |

### Match participant → `participants` (14 of ~156 fields)

`puuid`, `teamId` (100 blue, 200 red), `teamPosition` (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY = support), `championId`, `win`, `kills`, `deaths`, `assists`, `champLevel`, `goldEarned`, `visionScore`, and `creep_score` = `totalMinionsKilled` + `neutralMinionsKilled`.

**Deliberately not kept:** `riotIdGameName`, `riotIdTagline`, `summonerName`, `summonerId`, `profileIcon`. The decision doesn't need to know who anyone is. Also not kept: items, runes, pings, and the ~140 other stats; add them only when a question needs one.

### Team bans → `bans`

`championId` and `pickTurn` (1–10) per team. A `championId` of `-1` means the team chose not to ban and isn't stored.

## One record, read in full

From `data/sample/match_sample.json`, participant 1:

```json
{"teamId": 100, "teamPosition": "TOP", "championId": 799, "championName": "Ambessa", "win": false,
 "kills": 2, "deaths": 4, "assists": 2, "champLevel": 13, "goldEarned": 8375,
 "totalMinionsKilled": 192, "neutralMinionsKilled": 0, "visionScore": 32}
```

A blue-side (team 100) top laner played Ambessa (champion 799) and lost (blue's `win` is false; red won). They finished 2/4/2 at level 13 with 8,375 gold and 192 CS. The game was ranked solo (queue 420), patch 16.21, about 20 minutes long (`gameDuration` 1226 s). All ten players in this game were on that day's high-elo ladder.

## Surprises (the data didn't match what we assumed)

1. **Champion level goes above 18.** The schema first had `check (champion_level between 1 and 18)`. The first real sync failed on it (`sync_runs` run 1, 2026-11-04). The highest level we've stored is 20.
2. **Data Dragon is ahead of the games.** Champion names came from version 16.22.1 while every stored game was patch 16.21. Names and games can come from different patches.
3. **PUUIDs depend on the key.** The same player has a different PUUID under a different API key, so changing keys means starting the database over.
