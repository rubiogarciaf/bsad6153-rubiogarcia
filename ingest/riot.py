"""A small, polite client for the Riot Games API and Data Dragon.

sync.py uses it. The rules for talking to Riot live here, in one place:

* The key goes in the X-Riot-Token header. It is read from RIOT_API_KEY and
  never printed, logged, or put in a URL.
* Two kinds of host: league-v4 uses a PLATFORM host (na1), match-v5 uses a
  REGIONAL host (americas). Mixing them up returns 404 or 403.
* Pacing comes from Riot's own answer. Every response carries X-App-Rate-Limit,
  e.g. "20:1,100:120" (20 per second and 100 per 2 minutes). We space calls so
  the strictest window is never exceeded. A production key gets bigger numbers,
  and this code speeds up by itself.
* 429 (too many requests) and 5xx (server trouble) are temporary: wait (using
  Retry-After when Riot sends it), double the wait, retry, then give up.
  Any other 4xx means our request is wrong: fail now, don't retry.
* A budget caps the calls per run, so one run can't run past GitHub's time limit.
"""

import os
import time

import requests

MAX_ATTEMPTS = 5
TIMEOUT_SECONDS = 30
DEFAULT_SPACING = 1.25          # safe for a development/personal key until Riot tells us more
DDRAGON = "https://ddragon.leagueoflegends.com"


class ApiError(Exception):
    """Riot refused a request, or kept failing after every retry."""


class AuthError(ApiError):
    """401/403: the key is missing, expired, or not allowed. No point continuing."""


class BudgetExhausted(Exception):
    """This run has used all the calls it was allowed."""


def spacing_from_header(value):
    """'20:1,100:120' -> 1.2 seconds between calls (the strictest window wins)."""
    try:
        windows = [tuple(map(int, part.split(":"))) for part in value.split(",")]
        return max(seconds / limit for limit, seconds in windows)
    except (ValueError, ZeroDivisionError, AttributeError):
        return None


class RiotClient:
    def __init__(self, api_key=None, platform="na1", region="americas", max_calls=2400, sleep=time.sleep):
        key = api_key if api_key is not None else os.environ.get("RIOT_API_KEY", "")
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = key.strip()
        self.session.headers["User-Agent"] = "bsad6153-course-project"
        self.platform_url = f"https://{platform}.api.riotgames.com"
        self.region_url = f"https://{region}.api.riotgames.com"
        self.max_calls = max_calls
        self.calls_made = 0
        self.spacing = DEFAULT_SPACING
        self.sleep = sleep            # replaceable in tests, so tests don't really wait
        self._last_call = 0.0

    @property
    def calls_left(self):
        return self.max_calls - self.calls_made

    def _get(self, url, params=None, label=""):
        wait = 2.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.calls_made >= self.max_calls:
                raise BudgetExhausted(f"used all {self.max_calls} calls")
            gap = time.monotonic() - self._last_call
            if gap < self.spacing:
                self.sleep(self.spacing - gap)
            self._last_call = time.monotonic()
            self.calls_made += 1

            try:
                response = self.session.get(url, params=params, timeout=TIMEOUT_SECONDS)
            except (requests.ConnectionError, requests.Timeout) as error:
                problem, retry_after = f"network error ({type(error).__name__})", None
            else:
                learned = spacing_from_header(response.headers.get("X-App-Rate-Limit"))
                if learned:
                    self.spacing = learned * 1.05   # 5% margin for clock differences
                if response.status_code == 200:
                    return response.json()
                if response.status_code in (401, 403):
                    raise AuthError(f"{label} returned {response.status_code}: key missing, expired, or not allowed")
                if response.status_code != 429 and response.status_code < 500:
                    raise ApiError(f"{label} returned {response.status_code}; not retrying")
                problem = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After")

            if attempt == MAX_ATTEMPTS:
                raise ApiError(f"{label} failed after {MAX_ATTEMPTS} attempts (last: {problem})")
            delay = max(wait, float(retry_after)) if retry_after and retry_after.isdigit() else wait
            print(f"  {problem} on {label}; retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.0f}s")
            self.sleep(delay)
            wait *= 2

    # --- league-v4 (platform host) ------------------------------------------

    def apex_league(self, tier):
        """tier: 'challenger', 'grandmaster', or 'master'. The whole league in one call."""
        url = f"{self.platform_url}/lol/league/v4/{tier}leagues/by-queue/RANKED_SOLO_5x5"
        return self._get(url, label=f"{tier} league")["entries"]

    def tier_entries(self, tier, division):
        """Every page of one tier/division, e.g. DIAMOND I. Pages hold up to 205 entries."""
        entries, page = [], 1
        while True:
            url = f"{self.platform_url}/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}"
            batch = self._get(url, params={"page": page}, label=f"{tier} {division} page {page}")
            if not batch:
                return entries
            entries.extend(batch)
            page += 1

    # --- match-v5 (regional host) -------------------------------------------

    def match_ids_since(self, puuid, start_time):
        """Ranked solo (queue 420) match IDs that started after start_time (epoch seconds).
        Pages of 100 with start/count; a daily window rarely needs a second page."""
        ids, start = [], 0
        while True:
            url = f"{self.region_url}/lol/match/v5/matches/by-puuid/{puuid}/ids"
            batch = self._get(url, params={"startTime": int(start_time), "queue": 420,
                                           "start": start, "count": 100}, label="match ids")
            ids.extend(batch)
            if len(batch) < 100:
                return ids
            start += 100

    def match(self, match_id):
        return self._get(f"{self.region_url}/lol/match/v5/matches/{match_id}", label=f"match {match_id}")


def data_dragon_champions():
    """Champion names from Data Dragon (no key, not rate-limited like the API, not counted).
    Returns (version, champions)."""
    versions = requests.get(f"{DDRAGON}/api/versions.json", timeout=TIMEOUT_SECONDS)
    versions.raise_for_status()
    version = versions.json()[0]
    response = requests.get(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return version, list(response.json()["data"].values())
