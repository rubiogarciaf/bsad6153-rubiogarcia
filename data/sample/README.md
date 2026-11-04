A small sample of raw API data, so others can see its shape without calling the API.
Large raw downloads go in data/raw/, which Git ignores.

| File | What it is |
|---|---|
| `match_sample.json` | One real match-v5 response, trimmed to the 14 participant fields we store. Player IDs are replaced with `player-1`…`player-10`, and Riot IDs, names, and summoner IDs are removed. |

These are real players, so the full raw responses stay out of Git. Field meanings are in `docs/data_dictionary.md`.
