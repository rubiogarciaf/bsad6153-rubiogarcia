"""Offline checks of the Riot client's rules and the match filter. No network, no waiting.

    python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
from riot import MAX_ATTEMPTS, ApiError, AuthError, BudgetExhausted, RiotClient, spacing_from_header  # noqa: E402
from upsert import ban_rows, why_filtered  # noqa: E402


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def client_with(responses, max_calls=100):
    waits = []
    client = RiotClient(api_key="test", max_calls=max_calls, sleep=waits.append)
    queue = list(responses)
    client.session.get = lambda *a, **k: queue.pop(0)
    return client, waits


class RateLimits(unittest.TestCase):
    def test_spacing_uses_the_strictest_window(self):
        self.assertAlmostEqual(spacing_from_header("20:1,100:120"), 1.2)    # personal/dev key
        self.assertAlmostEqual(spacing_from_header("500:10,30000:600"), 0.02)  # production key
        self.assertIsNone(spacing_from_header(None))

    def test_retries_429_using_retry_after(self):
        client, waits = client_with([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, [1])])
        self.assertEqual(client._get("u"), [1])
        self.assertIn(7.0, waits)

    def test_gives_up_after_max_attempts(self):
        client, _ = client_with([FakeResponse(503)] * MAX_ATTEMPTS)
        with self.assertRaises(ApiError):
            client._get("u")

    def test_bad_key_stops_immediately(self):
        client, _ = client_with([FakeResponse(401)])
        with self.assertRaises(AuthError):
            client._get("u")
        self.assertEqual(client.calls_made, 1)

    def test_budget_is_enforced(self):
        client, _ = client_with([FakeResponse(200, [])] * 3, max_calls=2)
        client._get("u")
        client._get("u")
        with self.assertRaises(BudgetExhausted):
            client._get("u")


def fake_match(puuids, duration=1800, queue=420, result="GameComplete"):
    return {"metadata": {"matchId": "NA1_1"},
            "info": {"queueId": queue, "gameDuration": duration, "endOfGameResult": result,
                     "participants": [{"puuid": p} for p in puuids],
                     "teams": [{"teamId": 100, "bans": [{"championId": 1, "pickTurn": 1},
                                                        {"championId": -1, "pickTurn": 2}]},
                               {"teamId": 200, "bans": []}]}}


class Filter(unittest.TestCase):
    ladder = {f"p{i}" for i in range(10)}

    def test_keeps_a_game_with_all_ten_on_the_ladder(self):
        self.assertIsNone(why_filtered(fake_match([f"p{i}" for i in range(10)]), self.ladder))

    def test_drops_a_game_with_an_outsider(self):
        reason = why_filtered(fake_match([f"p{i}" for i in range(9)] + ["outsider"]), self.ladder)
        self.assertEqual(reason, "1 of 10 players not on the ladder")

    def test_drops_remakes(self):
        self.assertEqual(why_filtered(fake_match(list(self.ladder), duration=200), self.ladder), "remake")

    def test_no_ban_is_not_stored(self):
        self.assertEqual([b["champion_id"] for b in ban_rows(fake_match(list(self.ladder)))], [1])


if __name__ == "__main__":
    unittest.main()
