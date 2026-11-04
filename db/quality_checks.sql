-- db/quality_checks.sql: data-quality measurements (Weeks 2-3). Run as app_reader or owner.
-- Each check returns a number to copy into docs/data_quality.md with the date.
-- A check that "runs fine" isn't a pass: read the number and decide what it means.

-- 1. Nulls in fields a question depends on.
select 'participants with no team_position' as check_name, count(*) as n from participants where team_position is null
union all
select 'players never checked for matches', count(*) from players where matches_checked_at is null;

-- 2. Duplicates: primary keys forbid them, so 0 is expected. Not 0 means the schema is wrong.
select 'duplicate match_id' as check_name, count(*) - count(distinct match_id) as n from matches
union all
select 'matches without exactly 10 participants', count(*)
from (select match_id from matches left join participants using (match_id)
      group by match_id having count(participants.puuid) <> 10) t;

-- 3. Anomalies: allowed but suspicious.
select 'games under 15 minutes (early surrender?)' as check_name, count(*) as n from matches where duration_seconds < 900
union all
select 'participants above level 18', count(*) from participants where champion_level > 18
union all
select 'games where a team made fewer than 5 bans', count(*)
from (select match_id, team_id from bans group by 1, 2 having count(*) < 5) t;

-- 4. Queue health (Week 3): what the sync found but didn't store, and why.
select status, coalesce(left(reason, 40), '') as reason, count(*) as n
from match_queue group by 1, 2 order by n desc;

-- 5. Coverage: is the capture even across days? Low days usually mean a short or failed run.
select game_start::date as day, count(*) as games
from matches group by 1 order by 1;

-- 6. Coverage: which tiers do our checks reach? The budget favors higher tiers.
select tier, count(*) as players,
       count(*) filter (where matches_checked_at is not null) as checked,
       round(100.0 * count(*) filter (where matches_checked_at is not null) / count(*), 1) as pct_checked
from players group by tier order by min(case tier when 'CHALLENGER' then 1 when 'GRANDMASTER' then 2
                                                  when 'MASTER' then 3 else 4 end);

-- 7. Drift: do the champion names come from the same patch as the games?
select (select max(ddragon_version) from champions) as names_from_patch,
       (select string_agg(distinct patch, ', ') from matches
        where game_start > now() - interval '2 days') as recent_game_patches;
