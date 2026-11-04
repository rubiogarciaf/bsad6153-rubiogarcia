# Data quality

**Measured 2026-11-04** with `db/quality_checks.sql`, after five manual sync runs on a local test database
(425 matches, 19,743 ladder players). This was a dry run before Supabase. Rerun the checks on the real
database and update the numbers with the date.

## Measurements

| Check | Result | What it means for the decision |
|---|---|---|
| Duplicate matches | 0 | The natural key prevents them, including after a killed run and a rerun |
| Matches without exactly 10 participants | 0 | Each match is written in one transaction: all ten players or none |
| Participants above level 18 | 159 | Real. The first schema assumed 18 was the cap and the first sync failed on it (see Surprises in the data dictionary). |
| Team-games with fewer than 5 bans | 194 | Teams can skip a ban (`championId` = -1). A ban rate is "% of games banned," not "% of ban slots." |
| Games under 15 minutes | 3 | Early surrenders. Kept: they are real games. Remakes (under 5 minutes) are filtered out. |
| Discovered matches filtered out | 57 of 482 (12%) | 51 had 1–3 players not on the ladder; 6 were remakes |

## Coverage and fairness limitations

1. **The budget reaches the top of the ladder first.** After five runs: Challenger 100% checked, Grandmaster 23%, Master and Diamond I 0%. Stored games come mostly from Challenger players' match histories. Master and Diamond players appear only as the other nine players in those games. A "high-elo win rate" here is really "win rate in games that include a Challenger player," until the rotation reaches lower tiers (about 20 days at ~950 players a day).
2. **The filter drops mixed games.** Requiring all ten players on the ladder removes games with even one lower-ranked player (12% of what we downloaded). This keeps the population clean, but it hides champions that do well against slightly weaker opponents.
3. **A player's tier is today's, not the game's.** `players.tier` is overwritten every day. A Challenger-only question uses current rank, so a player who was Grandmaster at game time counts as Challenger.
4. **Side imbalance.** Blue side won 44.2% of 425 games. That's a large gap. With this few games it may be noise, and it's worth watching as the data grows before anyone says "pick red."
5. **Patches mix over time.** Every game so far is patch 16.21, but Data Dragon already lists 16.22.1. When 16.22 games arrive, any 7-day question will mix two patches. Golden question Q14 reports which patches are in the data.

## Personal data

These are real players. We keep `puuid` (needed to join games to the ladder) and drop Riot IDs, names, and summoner IDs at the transform step (`ingest/upsert.py`). The committed sample replaces PUUIDs with placeholders. PUUIDs are also specific to our API key, so they can't be joined to other services' data.
