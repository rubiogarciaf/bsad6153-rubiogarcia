# League Lens draft

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

Open the local URL printed by Streamlit. Configure `APP_DATABASE_URL` privately in
the existing `.env` or hosting environment. The app never falls back to
`DATABASE_URL`. It accepts only reader credentials, verifies both PostgreSQL
identities as `app_reader`, requires TLS, and uses read-only transactions with
connection and statement timeouts. Underlying exceptions are never displayed or
logged by the app.

The sidebar provides one view for each query in `db/queries.sql`: role leaders,
matchups, most picked, tier favorites, and short-game sensitivity. Choose among
the latest 30 snapshots and patches present in the selected cohort. Minimum picks
defaults to 30; matchups retain the query's 10-observation count screen.
Search and role filters narrow the returned rows, and CSV exports reflect those
filters. Raise Maximum returned rows to search a larger result set (up to 100).
The landing view is Most picked, which can show charts even when a recent small
cohort has no role leader meeting the minimum pick threshold.

The interface uses a light white/pale-blue theme with teal and gold accents.
The banner shows Lux's official default splash art. Role-leader cards use each
champion's canonical `champion_key` from `public.champions` to request the correct
default splash art from Riot's public Data Dragon CDN. No API key is needed for
these static images. If the image host is unavailable, card names and statistics
remain readable; if the optional asset-key lookup fails, statistics still render.
No image download, database write, or generated imitation artwork is involved.

Six hoverable plots accompany the five views: role win-rate bars, matchup
observations versus win-rate bubbles (with a 10-observation reference line),
popularity bars plus a pick-share/win-rate scatterplot, within-tier popularity
bars, and before/after short-game win-rate points. They use the returned query
rows, with observed counts in tooltips. Charts are explicitly labeled as showing
results before table-only search/role filters. Matchup count filtering applies
to both its chart and table. Rates use a 0–100% axis, and tier bars compare shares
within tiers rather than differently sized raw tier samples.

Artwork attribution and fan-project identification appear in the interface.
Sources: [Riot Data Dragon](https://developer.riotgames.com/docs/lol#data-dragon)
and [Riot fan-content policy](https://www.riotgames.com/en/legal).

Each query keeps the specified fixed 24-hour window before the selected snapshot.
The daily collector's wider retrieval window does not change that definition.
Snapshot time, last game insertion time, and cache check time are distinct labels.
Snapshot time is the collection anchor, not proof of a completed sync. The banner
flags the latest snapshot as historical after 26 hours; an empty latest cohort
offers an earlier snapshot instead of silently substituting old data.

`st.cache_data` expires after 300 seconds, shorter than the scheduled daily sync.
Refresh data clears the app's cached metadata and results. Expiration takes effect
on the next interaction, rather than polling continuously. No player identities
are selected for display. The interface states the sample and interpretation limits.

Reader boundary checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_streamlit_app.py
```
