"""League Lens: read-only champion research. Run with streamlit run app/streamlit_app.py."""  # Document the purpose or public-safety contract of the code immediately below/around this string.
from contextlib import contextmanager  # Turn reader() into a with-block that always closes its database connection.
from datetime import datetime, timedelta, timezone  # Work with collection times, time windows, and explicit UTC timestamps.
from decimal import Decimal  # Recognize PostgreSQL decimal values before preparing chart-friendly numbers.
from hashlib import sha256  # Build a connection-specific cache key without using the raw credential as an argument.
from html import escape  # Escape database text before inserting it into our HTML cards.
import os  # Read private configuration from environment variables.
from pathlib import Path  # Resolve project files independently of the current working directory.
import re  # Parse query labels, remove SQL comments, and validate artwork identifiers.

from dotenv import load_dotenv  # Load local private settings from .env without printing them.
import pandas as pd  # Prepare tables, filters, CSV downloads, and chart data.
import psycopg  # Connect to PostgreSQL and execute parameterized queries.
from psycopg.conninfo import conninfo_to_dict  # Parse the connection configuration to check its login name.
from psycopg.rows import dict_row  # Return database rows as dictionaries keyed by column name.
import streamlit as st  # Create the dashboard, widgets, messages, and data caches.

ROOT = Path(__file__).resolve().parents[1]  # Locate the repository root one directory above app/.
CACHE_TTL = 300  # Five minutes; scheduled ingestion is daily (09:17 UTC).
ROLES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}  # Map stored Riot role codes to the readable labels used by cards, plots, and filters.
VIEWS = {  # Map each visible navigation label to its existing SQL query identifier.
    "Role leaders": "Q1", "Matchups": "Q4", "Most picked": "Q2",  # Associate these navigation labels with their SQL query identifiers.
    "Tier favorites": "Q3", "Short-game sensitivity": "Q5",  # Associate these navigation labels with their SQL query identifiers.
}  # Close this expression after its preceding values or arguments have been supplied.


class DataUnavailable(Exception):  # Define an exception with no sensitive public error details.
    """A deliberately generic public error; never contains connection details."""  # Document the purpose or public-safety contract of the code immediately below/around this string.


def database_url():  # Read and validate only the private reader connection configuration.
    # Never fall back to the owner's DATABASE_URL. dotenv does not override hosting env.
    load_dotenv(ROOT / ".env", override=False)  # Read local settings while preserving variables already supplied by the host.
    url = os.getenv("APP_DATABASE_URL", "")  # Use only the reader URL; a missing variable becomes an empty string.
    if not url:  # Treat a missing reader URL as a recoverable configuration failure.
        raise DataUnavailable()  # Stop this operation using a generic error with no credential or driver message.
    try:  # Start a protected operation whose failure is handled by the corresponding except block.
        username = conninfo_to_dict(url).get("user", "")  # Extract just the configured database username for validation.
    except Exception:  # Catch a failure here so raw exception details are not sent to the public interface.
        raise DataUnavailable() from None  # Replace the original exception and suppress its traceback context.
    if username != "app_reader" and not username.startswith("app_reader."):  # Allow the reader login or its pooler suffix, rejecting owner credentials.
        raise DataUnavailable()  # Stop this operation using a generic error with no credential or driver message.
    return url  # Return the validated reader configuration only to the connection code.


@contextmanager  # Ensure the generator-based reader function supports safe with-block cleanup.
def reader():  # Manage a verified read-only PostgreSQL session and sanitize database failures.
    """Reject misconfigured credentials before reading any application tables."""  # Document the purpose or public-safety contract of the code immediately below/around this string.
    try:  # Start a protected operation whose failure is handled by the corresponding except block.
        with psycopg.connect(  # Open the connection inside a context manager so it closes on success or failure.
            database_url(), connect_timeout=10, autocommit=True, row_factory=dict_row,  # Use the validated reader URL, a 10-second connection timeout, and dictionary rows.
            sslmode="require", options="-c default_transaction_read_only=on -c statement_timeout=15000",  # Require TLS, default to read-only transactions, and limit each statement to 15 seconds.
        ) as conn:  # Bind the open connection to conn only for this with-block.
            with conn.transaction():  # Group related reads in a transaction with automatic cleanup.
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")  # Make reads consistent within this transaction and explicitly prohibit writes.
                identity = conn.execute("SELECT session_user, current_user").fetchone()  # Check the actual login and effective PostgreSQL role before reading application tables.
                if identity != {"session_user": "app_reader", "current_user": "app_reader"}:  # Reject impersonation or an unexpectedly privileged connection.
                    raise DataUnavailable()  # Stop this operation using a generic error with no credential or driver message.
                yield conn  # Temporarily give the caller the verified connection; resume cleanup when its block ends.
    except Exception:  # Catch a failure here so raw exception details are not sent to the public interface.
        # Do not print or log the underlying exception: drivers may include a DSN.
        raise DataUnavailable() from None  # Replace the original exception and suppress its traceback context.


def records(cursor):  # Convert returned rows into simple, chart-friendly, cacheable dictionaries.
    return [  # Build a list of plain row dictionaries for the interface and data cache.
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}  # Convert decimal numbers to floats while preserving other column values.
        for row in cursor.fetchall()  # Read each result row from the cursor, without selecting additional data.
    ]  # Close this expression after its preceding values or arguments have been supplied.


def query_source():  # Load and validate the five existing SQL definitions.
    text = (ROOT / "db/queries.sql").read_text(encoding="utf-8")  # Read the existing query definitions instead of duplicating their calculations.
    parts = re.split(r"^-- QUERY (Q[1-5])\s*$", text, flags=re.MULTILINE)  # Split SQL at the five full-line QUERY labels and retain their identifiers.
    queries = dict(zip(parts[1::2], parts[2::2]))  # Pair each retained query identifier with the SQL following it.
    if list(queries) != ["Q1", "Q2", "Q3", "Q4", "Q5"]:  # Fail safely if a required query label is missing, duplicated, or out of order.
        raise DataUnavailable()  # Stop this operation using a generic error with no credential or driver message.
    # psycopg also parses percent signs inside SQL comments as placeholders.
    # Keep the executable SQL intact, excluding full-line explanatory comments.
    return {key: re.sub(r"(?m)^\s*--[^\n]*(?:\n|$)", "", sql) for key, sql in queries.items()}  # Remove full-line SQL comments so literal percent signs do not confuse psycopg placeholders.


@st.cache_data(ttl=CACHE_TTL, max_entries=8, show_spinner=False)  # Cache successful returned data for 300 seconds, with at most 8 argument combinations and no built-in cache spinner.
def metadata(connection_key):  # Cache snapshot choices and collection freshness separately from query results.
    with reader() as conn:  # Acquire a verified read-only connection only when this function actually runs.
        snapshots = records(conn.execute(  # Read snapshot timestamps and convert the returned rows into cacheable records.
            "SELECT DISTINCT snapshot_at FROM public.ladder_snapshots ORDER BY snapshot_at DESC LIMIT 30"  # Offer the latest 30 unique collection anchors in newest-first order.
        ))  # Close this expression after its preceding values or arguments have been supplied.
        load = conn.execute("SELECT max(first_seen_at) AS loaded_at FROM public.matches").fetchone()  # Read the latest game insertion time, which differs from when a game was played.
        return {"snapshots": [row["snapshot_at"] for row in snapshots], **load,  # Return snapshot choices and insertion freshness as ordinary cached data.
                "checked_at": datetime.now(timezone.utc)}  # Record when this metadata cache entry was created, rather than pretending every hit rereads the database.


@st.cache_data(ttl=CACHE_TTL, max_entries=64, show_spinner=False)  # Cache successful returned data for 300 seconds, with at most 64 argument combinations and no built-in cache spinner.
def patches(connection_key, snapshot):  # Cache available patches and cohort counts for one snapshot.
    with reader() as conn:  # Acquire a verified read-only connection only when this function actually runs.
        return records(conn.execute((  # Execute the following unchanged SQL and convert its results to plain records.
            '\n'  # Preserve the original newline/indentation in the combined stylesheet or SQL string.
            '            SELECT m.patch, count(DISTINCT m.match_id) AS games, count(*) AS performances,\n'  # Count unique games and selected-player performances for each observed patch.
            '                   count(DISTINCT p.puuid) AS players\n'  # Count distinct selected players in the same cohort.
            '            FROM public.participants p JOIN public.matches m USING(match_id)\n'  # Join participant observations to their game facts.
            '            JOIN public.ladder_snapshots s ON s.puuid=p.puuid AND s.snapshot_at=%(snapshot_at)s\n'  # Include only players belonging to the exact selected ladder snapshot.
            "            WHERE m.game_start >= %(snapshot_at)s::timestamptz - interval '24 hours'\n"  # Include games starting at or after the fixed 24-hour window’s lower boundary.
            '              AND m.game_start < %(snapshot_at)s::timestamptz\n'  # Exclude games starting at or after the snapshot anchor.
            '            GROUP BY m.patch\n'  # Aggregate these coverage measurements separately for each API patch.
            "            ORDER BY split_part(m.patch,'.',1)::int DESC, split_part(m.patch,'.',2)::int DESC\n"  # Sort patch numbers numerically so the newest major/minor patch comes first.
            '        '  # Preserve the original newline/indentation in the combined stylesheet or SQL string.
        ), {"snapshot_at": snapshot}))  # Bind the selected snapshot through psycopg parameters and finish this query call.


@st.cache_data(ttl=CACHE_TTL, max_entries=8, show_spinner=False)  # Cache successful returned data for 300 seconds, with at most 8 argument combinations and no built-in cache spinner.
def champion_art(connection_key):  # Cache the optional public champion-name-to-art-key mapping.
    """Use Riot's canonical asset keys, including names with spaces/apostrophes."""  # Document the purpose or public-safety contract of the code immediately below/around this string.
    with reader() as conn:  # Acquire a verified read-only connection only when this function actually runs.
        return {row["name"]: row["champion_key"] for row in records(conn.execute(  # Build the optional champion-name-to-artwork-key lookup from reference data.
            "SELECT name, champion_key FROM public.champions"  # Read only the two public champion fields required to locate official artwork.
        )) if re.fullmatch(r"[A-Za-z0-9]+", row["champion_key"])}  # Keep only safe alphanumeric image identifiers in the lookup.


def splash_url(key):  # Create an official static-artwork URL from a validated champion identifier.
    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{key}_0.jpg"  # Construct Riot Data Dragon’s public URL for the champion’s default splash art.


@st.cache_data(ttl=CACHE_TTL, max_entries=128, show_spinner=False)  # Cache successful returned data for 300 seconds, with at most 128 argument combinations and no built-in cache spinner.
def result(connection_key, sql, snapshot, patch, min_picks, min_matchups, top_n):  # Cache the aggregate rows returned by a parameterized decision query.
    with reader() as conn:  # Acquire a verified read-only connection only when this function actually runs.
        return records(conn.execute(sql, {  # Run the selected parameterized query and return only its result data.
            "snapshot_at": snapshot, "patch": patch, "min_picks": min_picks,  # Bind the cohort anchor, API patch, and champion eligibility threshold.
            "min_matchups": min_matchups, "top_n": top_n,  # Bind the matchup count screen and maximum number of returned rows.
        }))  # Close the parameter dictionary, execute call, and result conversion.


def utc(value):  # Format timestamps consistently for the public interface.
    return value.astimezone(timezone.utc).strftime("%b %d, %Y · %H:%M UTC") if value else "No games loaded"  # Format present timestamps in labeled UTC; use a friendly message for a missing timestamp.


def style():  # Define the light theme, responsive layouts, and artwork presentation.
    st.markdown((  # Send the following adjacent CSS string literals to Streamlit as one unchanged stylesheet.
        '<style>\n'  # Open the app-authored stylesheet embedded in this Markdown element.
        '    .stApp { background: #f3f7fb; color: #17324d; }\n'  # Give the entire page its pale-blue background and readable dark text.
        '    [data-testid="stHeader"] { background: #f3f7fb; }\n'  # Match the top toolbar’s background to the light page.
        '    [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #d8e3ed; }\n'  # Use a white sidebar separated by a subtle border.
        '    .block-container { padding-top: 2rem; max-width: 1440px; }\n'  # Constrain dashboard width and set space above its content.
        '    h1, h2, h3 { color: #17324d; letter-spacing: -.025em; }\n'  # Use consistent dark heading text and slightly tighter letter spacing.
        '    .eyebrow { color: #076c78; font-size: .875rem; letter-spacing: .14em; font-weight: 700; }\n'  # Style the small teal section label above the hero headline.
        '    .hero { padding: 2rem; min-height: 260px; border: 1px solid #d4e0e9; border-radius: 18px;\n'  # Set banner spacing, minimum height, rounded corners, and border.
        '            display: flex; align-items: center; background-color: #e5edf5;\n'  # Center banner content vertically over a light fallback background.
        '            background-image: linear-gradient(90deg, #ffffff 0%, rgba(255,255,255,.96) 30%, rgba(255,255,255,.5) 58%, rgba(255,255,255,0) 100%),\n'  # Fade the left side to white so headline text remains readable over the artwork.
        "            url('https://ddragon.leagueoflegends.com/cdn/img/champion/splash/Lux_0.jpg');\n"  # Use Lux’s actual default splash art from Riot’s public Data Dragon CDN.
        '            background-size: cover; background-position: center, 70% 30%; }\n'  # Cover the banner while positioning the artwork’s visible subject.
        '    .hero h1 { max-width: 520px; font-size: clamp(2rem, 4vw, 3rem); line-height: 1.1; margin: .65rem 0; }\n'  # Scale the hero headline responsively and limit its width.
        '    .hero p { max-width: 460px; color: #344e67; font-size: 1rem; line-height: 1.6; margin-bottom: 0; }\n'  # Keep the banner explanation readable over the light gradient.
        '    .art-credit { color: #51697f; font-size: .875rem; margin: .4rem 0 1rem; }\n'  # Style the visible artwork attribution as secondary text.
        '    .freshness { padding: 1rem 1.2rem; background: #e5f3f2; border: 1px solid #b7d8d4;\n'  # Give collection freshness a distinct pale-teal information panel.
        '                 border-left: 4px solid #087f8c; border-radius: 10px; margin: 1rem 0; color: #214c53; }\n'  # Add a teal accent and readable text to the freshness panel.
        '    .champion-card { background: #ffffff; border: 1px solid #d4e0e9; border-radius: 14px;\n'  # Create white champion cards with a subtle border and rounded corners.
        '                     overflow: hidden; box-shadow: 0 5px 14px rgba(23,50,77,.05); }\n'  # Clip artwork to card corners and add a restrained card shadow.
        '    .champion-art { height: 150px; background-size: cover; background-position: 55% 24%; background-color: #dbe9f1; }\n'  # Crop splash art into a fixed-height card image with a fallback color.
        '    .champion-details { padding: 1rem; }\n'  # Pad the text portion of each champion card.
        '    .champion-card .role { color: #52677b; font-size: .875rem; letter-spacing: .08em; }\n'  # Style the small role label above a champion’s name.
        '    .champion-card h3 { margin: .35rem 0; font-size: 1.4rem; padding: 0; }\n'  # Size and space champion names within their cards.
        '    .champion-card .rate { color: #087a79; font-size: 1.9rem; font-weight: 700; }\n'  # Emphasize the observed win rate using bold teal text.
        '    .champion-card .detail { color: #52677b; font-size: .875rem; margin-top: .4rem; }\n'  # Style pick counts, player counts, and explanatory card text.
        '    .champion-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 1rem; margin-bottom: .75rem; }\n'  # Wrap champion cards into as many readable columns as the available width supports.
        '    [data-testid="stMetricLabel"] p { white-space: normal; overflow-wrap: anywhere; }\n'  # Allow long metric labels to wrap instead of being truncated.
        '    [data-testid="stMetric"] { background: #ffffff; border: 1px solid #d4e0e9;\n'  # Give cohort metrics white surfaces and subtle borders.
        '                              border-radius: 10px; padding: 1rem; }\n'  # Round and pad each metric panel.
        '    @media (max-width: 700px) { .hero { padding: 1.25rem; background-position: center, 65% center; }\n'  # Adjust banner spacing and artwork position for narrow screens.
        '        .hero p { max-width: 75%; } .hero h1 { max-width: 85%; } }\n'  # Keep the banner explanation readable over the light gradient.
        '    </style>'  # Close the app-authored stylesheet without changing its original text.
    ), unsafe_allow_html=True)  # Finish rendering this app-authored stylesheet as HTML.


def plot(data, spec, height=310):  # Apply shared display settings before rendering one Vega-Lite chart.
    spec = {"height": height, "background": "#ffffff",  # Start the shared chart settings with a requested height and white background.
            "padding": {"left": 18, "right": 18, "top": 18, "bottom": 18},  # Use per-edge padding compatible with Streamlit’s chart renderer.
            "config": {"view": {"stroke": None},  # Remove the default chart outline while configuring readable axes and legends.
                       "axis": {"labelColor": "#344e67", "titleColor": "#17324d",  # Use high-contrast blue-gray axis labels and dark axis titles.
                                "gridColor": "#e6edf3", "labelFontSize": 13, "titleFontSize": 14},  # Keep grid lines subtle and specify readable chart label sizes.
                       "legend": {"labelColor": "#344e67", "titleColor": "#17324d", "labelFontSize": 13}},  # Match legend text to the light dashboard palette.
            **spec}  # Apply the view-specific chart specification over these shared defaults.
    st.vega_lite_chart(pd.DataFrame(data), spec, width="stretch", theme=None)  # Render the provided records responsively using our explicit chart colors.


def charts(rows, query):  # Select the appropriate visual comparison for the requested query.
    """Charts use the same aggregate query rows as the table, never invented data."""  # Document the purpose or public-safety contract of the code immediately below/around this string.
    if not rows:  # Skip plotting when the query or count filter leaves no observations.
        return  # Exit this function now; the surrounding page can continue or show its empty-state message.
    st.markdown("#### At a glance")  # Label the visual comparison section above the chart.
    color = {"field": "role", "type": "nominal", "legend": None,  # Define a consistent categorical color for each role without a redundant default legend.
             "scale": {"domain": list(ROLES.values()),  # Keep the role-to-color mapping stable across charts.
                       "range": ["#087f8c", "#c08b30", "#536bc3", "#299f83", "#aa608b"]}}  # Assign teal, gold, blue, green, and purple to the five roles.
    if query == "Q1":  # Build the role-leader win-rate comparison for query Q1.
        data = [{**row, "role": ROLES[row["role"]], "label": f'{ROLES[row["role"]]} · {row["champion"]}'} for row in rows]  # Copy each role-leader row and create readable role/champion labels.
        plot(data, {"mark": {"type": "bar", "cornerRadiusEnd": 5}, "encoding": {  # Render this query’s records with the following bar, point, or layered chart specification.
            "y": {"field": "label", "type": "nominal", "title": None, "sort": "-x"},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
            "x": {"field": "win_pct", "type": "quantitative", "title": "Observed win rate (%)", "scale": {"domain": [0, 100]}},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
            "color": color,  # Apply categorical colors and configure the visible legend for this comparison.
            "tooltip": [{"field": "champion"}, {"field": "role"}, {"field": "win_pct", "format": ".2f", "title": "Win rate (%)"},  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
                        {"field": "picks"}, {"field": "distinct_players", "title": "Distinct players"}]}})  # Complete the hover details with the remaining counts or outcome values.
        st.caption("Compare role leaders by observed win rate. Hover for pick and player counts.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    elif query == "Q4":  # Build the same-role matchup observation chart for query Q4.
        data = [{**row, "role": ROLES[row["role"]], "matchup": f'{row["champion"]} vs {row["opponent"]}'} for row in rows]  # Copy matchup rows and add readable role and opponent-pair labels.
        plot(data, {"layer": [  # Render this query’s records with the following bar, point, or layered chart specification.
            {"mark": {"type": "rule", "color": "#9a742e", "strokeDash": [5, 5]},  # Draw a dashed reference line for the ten-observation matchup count screen.
             "encoding": {"x": {"datum": 10}}},  # Position that count-screen reference line at exactly ten observations.
            {"mark": {"type": "circle", "opacity": .85, "stroke": "white", "strokeWidth": 1}, "encoding": {  # Draw matchup bubbles with visible outlines and enough transparency to reveal overlap.
                "x": {"field": "games", "type": "quantitative", "title": "Matchup observations", "scale": {"domainMin": 0}},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
                "y": {"field": "win_pct", "type": "quantitative", "title": "Observed win rate (%)", "scale": {"domain": [0, 100]}},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
                "size": {"field": "distinct_players", "type": "quantitative", "title": "Distinct players", "scale": {"range": [60, 650]}},  # Scale matchup bubbles by distinct-player count, with a bounded visible size range.
                "color": {**color, "legend": {"title": "Role"}},  # Apply categorical colors and configure the visible legend for this comparison.
                "tooltip": [{"field": "matchup"}, {"field": "role"}, {"field": "games", "title": "Observations"},  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
                            {"field": "win_pct", "format": ".2f", "title": "Win rate (%)"}, {"field": "distinct_players"}]}}]})  # Complete the hover details with the remaining counts or outcome values.
        st.caption("The dashed line marks 10 observations. Bubble size shows distinct players. Hover to identify matchups; overlapping bubbles can share the same result.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    elif query == "Q2":  # Build the two popularity/outcome charts for query Q2.
        left, right = st.columns(2)  # Place popularity and win-rate comparisons side by side.
        with left:  # Render the pick-count bar chart in the left column.
            plot(rows, {"mark": {"type": "bar", "color": "#087f8c", "cornerRadiusEnd": 4}, "encoding": {  # Render this query’s records with the following bar, point, or layered chart specification.
                "y": {"field": "champion", "type": "nominal", "sort": "-x", "title": None},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
                "x": {"field": "picks", "type": "quantitative", "title": "Selected-player picks"},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
                "tooltip": [{"field": "champion"}, {"field": "picks"}, {"field": "pick_share_pct", "title": "Pick share (%)"}]}}  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
                 , height=max(310, min(620, len(rows) * 23)))  # Adjust the bar chart height to the number of displayed champions, within these bounds.
        with right:  # Render the pick-share versus win-rate scatterplot in the right column.
            plot(rows, {"mark": {"type": "circle", "size": 140, "color": "#b5842a", "opacity": .8}, "encoding": {  # Render this query’s records with the following bar, point, or layered chart specification.
                "x": {"field": "pick_share_pct", "type": "quantitative", "title": "Pick share (%)"},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
                "y": {"field": "win_pct", "type": "quantitative", "title": "Observed win rate (%)", "scale": {"domain": [0, 100]}},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
                "tooltip": [{"field": "champion"}, {"field": "picks"}, {"field": "win_pct", "format": ".2f"}, {"field": "pick_share_pct"}]}})  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
        st.caption("Popularity and observed win rate describe different things. Both plots show the returned most-picked champions.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    elif query == "Q3":  # Build the tier-specific popularity comparison for query Q3.
        data = [{**row, "label": f'{row["tier"].title()} · {row["champion"]}'} for row in rows]  # Create a separate readable tier/champion label for each popularity row.
        plot(data, {"mark": {"type": "bar", "cornerRadiusEnd": 4}, "encoding": {  # Render this query’s records with the following bar, point, or layered chart specification.
            "y": {"field": "label", "type": "nominal", "sort": None, "title": None},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
            "x": {"field": "tier_pick_share_pct", "type": "quantitative", "title": "Pick share within tier (%)"},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
            "color": {"field": "tier", "type": "nominal", "title": "Observed tier",  # Apply categorical colors and configure the visible legend for this comparison.
                      "scale": {"domain": ["CHALLENGER", "GRANDMASTER", "MASTER"], "range": ["#b5842a", "#087f8c", "#7862af"]}},  # Define a stable category order and matching colors for this chart’s legend.
            "tooltip": [{"field": "champion"}, {"field": "tier"}, {"field": "picks"}, {"field": "tier_pick_share_pct"},  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
                        {"field": "win_pct", "title": "Win rate (%)"}, {"field": "passes_count_screen"}]}}, height=350)  # Complete the hover details with the remaining counts or outcome values.
        st.caption("Share is calculated within each tier, so differences in tier sample size do not determine bar length.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    else:  # Handle the alternative case when the preceding condition does not apply.
        rates = [{"champion": row["champion"], "sample": label, "win_rate": row[field]}  # Reshape each sensitivity row into a plotted champion/sample/win-rate record.
                 for row in rows for label, field in [("All games", "all_win_pct"), ("Games ≥ 5 minutes", "filtered_win_pct")]]  # Create one point for all games and another for games lasting at least five minutes.
        plot(rates, {"mark": {"type": "point", "filled": True, "size": 110}, "encoding": {  # Render this query’s records with the following bar, point, or layered chart specification.
            "y": {"field": "champion", "type": "nominal", "title": None},  # Choose the vertical labels or numeric outcome axis, including its ordering and scale when specified.
            "x": {"field": "win_rate", "type": "quantitative", "title": "Observed win rate (%)", "scale": {"domain": [0, 100]}},  # Choose the horizontal metric and label; keep percentage axes on the explicit 0–100 scale when specified.
            "color": {"field": "sample", "type": "nominal", "title": None,  # Apply categorical colors and configure the visible legend for this comparison.
                      "scale": {"domain": ["All games", "Games ≥ 5 minutes"], "range": ["#b5842a", "#087f8c"]}},  # Define a stable category order and matching colors for this chart’s legend.
            "shape": {"field": "sample", "type": "nominal", "legend": None},  # Distinguish the two sensitivity samples by point shape as well as color.
            "tooltip": [{"field": "champion"}, {"field": "sample"}, {"field": "win_rate", "format": ".2f"}]}},  # Expose the underlying champion, count, and outcome values when a viewer hovers over a mark.
             height=max(310, min(650, len(rows) * 24)))  # Size the before/after comparison to the number of champions while limiting chart height.
        st.caption("Compare the same eligible champions with and without games shorter than five minutes. Identical rates overlap; hover for exact values and use the table for rank changes.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    st.caption("Plots show the query results before the table’s search and role filters. Counts are observations, not independent experiments.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.


def table(rows, key):  # Filter and render returned rows, with friendly empty states and a CSV export.
    frame = pd.DataFrame(rows)  # Create a local table from the query result, leaving cached records untouched.
    if frame.empty:  # Handle a legitimate empty query or filtered table without raising an exception.
        st.info("No results for these settings. Try a lower count threshold or another snapshot.")  # Display a plain-language explanation or recovery hint instead of exception details.
        return  # Exit this function now; the surrounding page can continue or show its empty-state message.
    left, right = st.columns([2, 1])  # Give the search box twice the horizontal space of the role selector.
    search = left.text_input("Search champion or opponent", key=f"search_{key}", placeholder="e.g. Gwen")  # Collect a literal champion/opponent search term with a widget key unique to the view.
    role = right.selectbox("Role", ["All roles", *ROLES.values()], key=f"role_{key}") if "role" in frame else None  # Offer role filtering only for queries that actually return a role column.
    if search:  # Apply the in-memory name filter only when the user enters search text.
        names = [column for column in ("champion", "opponent") if column in frame]  # Search only the name columns provided by this query.
        matches = pd.Series(False, index=frame.index)  # Start with every row excluded until a name column matches.
        for column in names:  # Check each available name column without issuing another database query.
            matches |= frame[column].str.contains(search.strip(), case=False, regex=False, na=False)  # Match case-insensitive literal text and safely treat missing names as nonmatches.
        frame = frame[matches]  # Retain rows matching at least one searched name column.
    if role and role != "All roles":  # Apply role filtering when the user selects one specific role.
        frame = frame[frame["role"].map(ROLES) == role]  # Compare friendly role labels and keep only the selected role.
    if "role" in frame:  # Translate stored role codes only when this result includes them.
        frame["role"] = frame["role"].map(ROLES)  # Show readable role names in place of database role codes.
    if frame.empty:  # Handle a legitimate empty query or filtered table without raising an exception.
        st.info("No rows match your filters. Clear the search or choose another role.")  # Display a plain-language explanation or recovery hint instead of exception details.
        return  # Exit this function now; the surrounding page can continue or show its empty-state message.
    configs = {}  # Collect display labels and column-format instructions for the table.
    for column in frame:  # Choose a display configuration for each returned table column.
        label = column.replace("_", " ").capitalize().replace(" pct", " (%)").replace(" pp", " (pp)")  # Turn database column names into readable labels, including percentages and percentage points.
        if column.endswith("pct"):  # Recognize columns whose values are already percentages on a 0–100 scale.
            configs[column] = st.column_config.NumberColumn(label, format="%.2f%%")  # Display those values to two decimals with a percent sign, without rescaling them.
        elif column == "passes_count_screen":  # Recognize the boolean indicating whether the observation threshold is met.
            configs[column] = st.column_config.CheckboxColumn("Meets count threshold")  # Display the count-screen result as a clearly labeled checkbox.
        else:  # Handle the alternative case when the preceding condition does not apply.
            configs[column] = label  # Use the friendly label for other columns without changing their values.
    st.dataframe(frame, hide_index=True, width="stretch", column_config=configs)  # Render a sortable table that fills its container and hides the pandas index.
    st.caption(f"{len(frame):,} rows shown. Click a column header to sort. Search and role filters apply to the returned results.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    st.download_button("Download filtered CSV", frame.to_csv(index=False).encode("utf-8"),  # Prepare a UTF-8 CSV of exactly the table rows remaining after filtering.
                       file_name=f"league_lens_{key}.csv", mime="text/csv", key=f"download_{key}")  # Give the export a query-specific filename, content type, and widget key.


def main():  # Assemble navigation, freshness, cached data loading, views, and public error messages.
    st.set_page_config(page_title="League Lens | Champion research", page_icon="⚔", layout="wide")  # Set the browser title, icon, and wide dashboard layout before rendering the page.
    st.set_option("client.showErrorDetails", False)  # Disable detailed public Python errors; config.toml also sets showErrorDetails to none.
    style()  # Apply the light dashboard stylesheet defined above.
    with st.sidebar:  # Place the following navigation, controls, or collection settings in the sidebar.
        st.markdown("## ⚔ League Lens")  # Display the app’s own name at the top of the sidebar.
        st.caption("A clearer view of your next pick.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
        st.divider()  # Separate adjacent page sections with a horizontal rule.
        view = st.radio("Explore", list(VIEWS), index=2)  # Choose a query view, defaulting to Most picked so a small cohort can still have plots.
        st.divider()  # Separate adjacent page sections with a horizontal rule.
        st.markdown("**NA · Ranked solo**")  # Label the collection’s North America ranked-solo scope.
        st.caption("Challenger / Grandmaster / Master")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
        if st.button("Refresh data", width="stretch"):  # Let the user bypass the TTL by explicitly clearing this process’s data caches.
            metadata.clear()  # Invalidate cached snapshot choices and freshness timestamps for all argument combinations.
            patches.clear()  # Invalidate cached patch choices and cohort counts.
            champion_art.clear()  # Invalidate the optional champion artwork-key lookup.
            result.clear()  # Invalidate cached query results so the active view reads them again on this rerun.
        st.caption("Results cached for up to 5 minutes. Collection scheduled daily at 09:17 UTC.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
    st.markdown('<section class="hero" aria-label="Champion research with Lux splash artwork">'  # Open the accessible hero banner backed by official Lux artwork.
                '<div><div class="eyebrow">LEAGUE OF LEGENDS · CHAMPION RESEARCH</div>'  # Identify the page as a League of Legends champion-research dashboard.
                '<h1>Find your next champion.</h1>'  # Provide the page’s primary heading within the artwork banner.
                '<p>Explore role leaders, compare matchups, and see the numbers behind your next pick.</p>'  # Explain what the dashboard helps players compare.
                '</div></section><div class="art-credit">Lux splash art · Riot Games / Data Dragon</div>',  # Close the banner and credit Riot Games/Data Dragon for the splash art.
                unsafe_allow_html=True)  # Render only our app-authored HTML; database champion names are escaped before inclusion.
    try:  # Start a protected operation whose failure is handled by the corresponding except block.
        # Credential digest separates caches when the configured database changes.
        connection_key = sha256(database_url().encode()).hexdigest()  # Hash the reader URL so a connection change separates cached data without a raw secret argument.
        with st.spinner("Checking the latest collection…"):  # Show progress while loading collection metadata on a cache miss.
            meta = metadata(connection_key)  # Reuse metadata while its five-minute cache entry is valid; otherwise read it again.
        if not meta["snapshots"]:  # Detect a database that has no collection snapshots to choose from.
            st.info("No ladder snapshots are available yet. Please return after the first collection.")  # Display a plain-language explanation or recovery hint instead of exception details.
            return  # Exit this function now; the surrounding page can continue or show its empty-state message.
        with st.sidebar:  # Place the following navigation, controls, or collection settings in the sidebar.
            snapshot = st.selectbox("Collection snapshot (UTC)", meta["snapshots"], format_func=utc)  # Choose a cached snapshot anchor and show each option with its UTC timestamp.
        age = datetime.now(timezone.utc) - meta["snapshots"][0]  # Measure the age of the newest available snapshot for the freshness banner.
        state = "Historical data · latest snapshot is over 26 hours old" if age > timedelta(hours=26) else "Recent collection"  # Flag an old collection using the daily schedule plus a two-hour grace period.
        if snapshot != meta["snapshots"][0]:  # Detect when the viewer intentionally chooses an older collection.
            state = f"Historical selection · {utc(snapshot)}"  # Make the selected historical date explicit instead of labeling those results as recent.
        st.markdown(f'<div class="freshness"><strong>{state}</strong><br>'  # Open the freshness banner and show whether the selection is recent or historical.
                    f'Latest snapshot: {utc(meta["snapshots"][0])} &nbsp; | &nbsp; '  # Show the newest snapshot anchor separately from the currently selected historical window.
                    f'Last game loaded: {utc(meta["loaded_at"])}</div>', unsafe_allow_html=True)  # Show the latest game insertion time and finish the freshness banner.
        st.caption("Snapshot time marks the start of collection. Collection may be partial or still running; it is not a successful-sync timestamp.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
        with st.spinner("Loading the selected cohort…"):  # Show progress while the selected snapshot’s patch/count cache is loaded.
            available = patches(connection_key, snapshot)  # Reuse or fetch the available patches and counts for this one snapshot.
        if not available:  # Handle a snapshot with no selected-player games in the required time window.
            st.info("No selected-player games fall in the 24 hours before this snapshot yet. Try an earlier snapshot or refresh after collection.")  # Display a plain-language explanation or recovery hint instead of exception details.
            return  # Exit this function now; the surrounding page can continue or show its empty-state message.
        with st.sidebar:  # Place the following navigation, controls, or collection settings in the sidebar.
            patch = st.selectbox("API patch", [row["patch"] for row in available])  # Restrict patch choices to patches present in the selected cohort.
            min_picks = st.number_input("Minimum champion picks", min_value=1, max_value=1000, value=30, step=5,  # Choose the champion count screen, defaulting to 30 observations.
                                       help="Used for role leaders, tier count flags, and short-game sensitivity.")  # Explain which query results are affected by the minimum-picks setting.
            top_n = st.slider("Maximum returned rows", min_value=5, max_value=100, value=20, step=5)  # Bound the returned result size to control table and query workload.
        coverage = next(row for row in available if row["patch"] == patch)  # Find the cached cohort counts for the chosen patch.
        columns = st.columns(4)  # Arrange the four coverage metrics in one row.
        for col, label, value in zip(columns, ["API patch", "Games in cohort", "Player performances", "Distinct selected players"],  # Pair each metric column with its label and corresponding cohort value.
                                     [patch, f'{coverage["games"]:,}', f'{coverage["performances"]:,}', f'{coverage["players"]:,}']):  # Format cohort counts with thousands separators while leaving the patch as text.
            col.metric(label, value)  # Display this labeled cohort metric without changing the underlying data.
        st.caption(f"Selected window: {utc(snapshot - timedelta(hours=24))} to {utc(snapshot)} (end excluded). Data checked {utc(meta['checked_at'])}.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
        st.divider()  # Separate adjacent page sections with a horizontal rule.
        query = VIEWS[view]  # Translate the selected navigation label into the existing SQL query identifier.
        st.subheader(view)  # Show the current query view’s name above its description and results.
        descriptions = {  # Provide an explanation of each query’s scope and interpretation.
            "Q1": f"Highest observed win rate in each role among champions with at least {min_picks} picks. Short games are included.",  # Explain this existing query’s population, count screen, or interpretation in the selected view.
            "Q2": "Most selected champions and their outcomes. Pick share uses all selected-player performances as its denominator.",  # Explain this existing query’s population, count screen, or interpretation in the selected view.
            "Q3": "The three most-picked champions in each observed tier. Tier is recorded at collection, not at game time.",  # Explain this existing query’s population, count screen, or interpretation in the selected view.
            "Q4": "Most-observed same-role opponents. Missing or ambiguous role pairs are excluded; opponents may come from outside the selected tiers.",  # Explain this existing query’s population, count screen, or interpretation in the selected view.
            "Q5": f"Overall rankings before and after removing games under 300 seconds. The same champions must have at least {min_picks} picks in both sets. Negative rank change means a better rank.",  # Explain this existing query’s population, count screen, or interpretation in the selected view.
        }  # Close this expression after its preceding values or arguments have been supplied.
        st.write(descriptions[query])  # Display the explanation belonging to the selected query.
        with st.spinner("Reading champion results…"):  # Show progress when the chosen query needs to read the database.
            rows = result(connection_key, query_source()[query], snapshot, patch, min_picks, 10, top_n)  # Reuse or fetch aggregate rows keyed by connection, SQL, snapshot, patch, and count/limit settings.
        if query == "Q1" and rows:  # Build champion cards only when role-leader results actually exist.
            # Artwork is optional: an asset lookup problem must not hide valid statistics.
            try:  # Start a protected operation whose failure is handled by the corresponding except block.
                art = champion_art(connection_key)  # Reuse or read optional canonical artwork identifiers.
            except DataUnavailable:  # Recover from optional artwork lookup failure while preserving valid statistics.
                art = {}  # Use an empty artwork lookup so the cards still show names and results.
            by_role = {row["role"]: row for row in rows}  # Index the role-leader result rows by their stored role codes.
            cards = []  # Accumulate the app-authored HTML for the role-leader card grid.
            for role, label in ROLES.items():  # Render the five roles in the dashboard’s fixed display order.
                row = by_role.get(role)  # Look up the eligible winner for this role, if one exists.
                if row:  # Show a champion’s artwork and statistics only when this role has an eligible result.
                    key = art.get(row["champion"])  # Look up the champion’s optional validated Riot artwork key.
                    artwork = (f'<div class="champion-art" role="img" aria-label="{escape(row["champion"], quote=True)} splash art by Riot Games" '  # Prepare an accessible image region and escape the champion name in its HTML attribute.
                               f'style="background-image:url({splash_url(key)})"></div>') if key else ''  # Use the validated official art URL when available; otherwise omit this optional image region.
                    cards.append(f'<div class="champion-card">{artwork}<div class="champion-details"><div class="role">{label.upper()}</div>'  # Start this role’s HTML card, using artwork or an empty-role explanation as appropriate.
                                 f'<h3>{escape(row["champion"])}</h3><div class="rate">{row["win_pct"]:.2f}%</div>'  # Escape the champion name and format the observed win rate to two decimals.
                                 f'<div class="detail">observed win rate<br>{row["picks"]:,} picks · {row["distinct_players"]:,} players</div></div></div>')  # Show the pick and distinct-player counts supporting the card’s observed win rate.
                else:  # Handle the alternative case when the preceding condition does not apply.
                    cards.append(f'<div class="champion-card"><div class="champion-details"><div class="role">{label.upper()}</div>'  # Start this role’s HTML card, using artwork or an empty-role explanation as appropriate.
                                 '<p>No champion meets the pick threshold.</p></div></div>')  # Explain a role without an eligible champion instead of inventing a winner.
            st.markdown('<div class="champion-grid">' + ''.join(cards) + '</div>', unsafe_allow_html=True)  # Join the app-authored cards and render them inside the responsive grid.
            st.write("")  # Add a little vertical breathing room below the card grid.
        if query == "Q4":  # Explain and optionally filter the matchup count screen in the matchup view.
            # The SQL sorts by games descending, so the first row also answers
            # whether ANY matchup meets ten, even when the result is limited.
            if rows and rows[0]["games"] >= 10:  # Use the first count-sorted row to determine whether any matchup reaches ten observations.
                st.success(f'Yes — the most-observed matchup has {rows[0]["games"]:,} observations (threshold: 10).')  # Report that at least one returned matchup reaches the ten-observation screen.
            elif rows:  # Distinguish a nonempty sample below the screen from a completely empty result.
                st.info("No same-role matchup reaches 10 observations in this cohort.")  # Display a plain-language explanation or recovery hint instead of exception details.
            count_only = st.checkbox("Only show matchups with at least 10 observations")  # Let the viewer hide returned matchups that fail the fixed ten-observation screen.
            if count_only:  # Apply the optional count filter before drawing the chart or table.
                rows = [row for row in rows if row["passes_count_screen"]]  # Keep only returned rows already marked as meeting the SQL count screen.
        charts(rows, query)  # Draw the selected view’s charts only when observations are available.
        st.markdown("#### Explore the data")  # Label the table and its local filtering controls.
        table(rows, query)  # Show an interactive table, or a friendly message if its rows are empty.
        with st.expander("How to read these results"):  # Keep detailed sample/interpretation limits available without crowding the main view.
            st.write("These are selected-player performances, not independent experiments. One game can contribute multiple observations. Sampling is bounded and does not cover every high-ranked player’s games.")  # Explain the selected sample’s limitations and how to interpret its observed results.
            st.write("Observed win rate does not establish champion strength or predict your result. Count thresholds are exploratory screens, not guarantees of statistical reliability. Results may not transfer to other ranks, regions, or patches.")  # Explain the selected sample’s limitations and how to interpret its observed results.
            st.write("The app uses the query definitions in db/queries.sql and a fixed 24-hour window, even when the collector fetches a wider window. Unknown roles are omitted from role comparisons. Ties in role leaders favor more picks, then champion name.")  # Explain the selected sample’s limitations and how to interpret its observed results.
        st.divider()  # Separate adjacent page sections with a horizontal rule.
        st.caption("Data and champion artwork: Riot Games API / Data Dragon. League Lens is an independent educational fan project, not endorsed by Riot Games. League of Legends and its associated properties belong to Riot Games.")  # Explain the displayed data, freshness, chart behavior, attribution, or interpretation limit in secondary text.
        st.markdown("[Artwork source](https://developer.riotgames.com/docs/lol#data-dragon) · [Riot Games fan-content policy](https://www.riotgames.com/en/legal)")  # Link to Riot’s official artwork documentation and fan-content policy.
    except Exception:  # Catch a failure here so raw exception details are not sent to the public interface.
        st.error("Champion data is temporarily unavailable. Please try Refresh data in a moment.")  # Show a friendly retry message without the original error, SQL text, or connection credentials.
        st.info("If this continues, the site operator should check the private reader connection and database availability.")  # Display a plain-language explanation or recovery hint instead of exception details.


if __name__ == "__main__":  # Launch the dashboard only when this file is the entry script, not when tests import it.
    main()  # Render the Streamlit application for this run or user interaction.
