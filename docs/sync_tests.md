# Sync tests (Week 3)

**These are dry runs on a local test database, 2026-11-04 (UTC), with the real Riot API.**
Repeat them on Supabase and in GitHub Actions for the real M3 evidence, and replace this section with those results.

## Test 1. Bad credentials

**Did:** a manual run with an invalid key (`RIOT_API_KEY=RGAPI-0000…`), budget 20 calls.
**Expected:** stops at the first Riot call, exit code 1, a `failed` row, no secret in the message.
**Happened:** as expected. It stopped after 1 call: `challenger league returned 401: key missing, expired, or not allowed`. Exit code 1.

| run_id | status | api_calls | records_inserted | error_message |
|---|---|---|---|---|
| 2 | failed | 1 | 0 | AuthError: challenger league returned 401: key missing, expired, or not allowed |

## Test 2. Interrupted run

**Did:** started a 400-call run and force-killed the process after 150 seconds (like a cancelled GitHub job), during discovery.
**Expected:** the run's row says it didn't finish; no half-written match; the next run recovers.
**Happened:**

| run_id | status | finished_at | error_message |
|---|---|---|---|
| 4 | failed | (empty) | run did not finish |

Before and after the next run (run 5): 0 duplicate `match_id`s, and 0 matches without exactly 10 participants. Run 5 finished `success`. Why it holds: the run's row is committed as "failed / did not finish" before any work starts. Each player's watermark moves in the same transaction as their match IDs, and each match is stored in one transaction with its ten players and bans.

## Test 3. Source change

**Did:** ran the sync again about 20 minutes after the previous run.
**Expected:** some ladder players' LP, wins, or losses changed in between, so they show up as updates, and only those players.
**Happened:** run 3 reported 151 updated; run 5 reported 207 updated. Champion names, which hadn't changed, showed "0 new, 0 changed" on every rerun.

## Test 4. Rerun

A strict "0 inserted, 0 updated" rerun doesn't happen with this source: the ladder moves every few minutes (test 3), and each run checks different players. What *is* idempotent:
- Champions: 0 new, 0 changed on every rerun.
- Matches: a match ID already stored is never downloaded or inserted again (`match_queue` primary key). Duplicates after 5 runs, including a killed one: 0.

## Golden-question check

| Saved run | Matches in DB | Queries OK | Changed vs previous |
|---|---|---|---|
| 2026-11-04 01:41 | 424 | 14 of 14 | (baseline) |
| 2026-11-04 01:47 | 425 | 14 of 14 | 9: Q01, Q02, Q03, Q04, Q07, Q09, Q10, Q11, Q14 |

Example: Q01's top champion stayed Camille, but games went 59 → 60 and win rate 62.7% → 61.7%. Q05, Q06, Q08, Q12, and Q13 didn't change: one new match didn't touch those champions or roles. For the real check, the second run must come **after at least two more scheduled syncs**.

## Bugs these tests caught before Supabase

1. **The champion-level rule.** Run 1 failed: `participants_champion_level_check`. Real games reach level 20. Fixed the rule. One bad match now fails only itself and makes the run `partial`, instead of failing the whole run.
2. **"Success" that saved nothing.** A run reported 62 matches kept, but the tables were empty. The first SELECT had silently opened a transaction, every later block became a savepoint inside it, and the final rollback erased everything. Fixed with `autocommit=True`. Lesson: check the tables, not the log.
