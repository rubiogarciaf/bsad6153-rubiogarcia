-- Five instructor-example decision queries. Executed by scripts/run_queries.py.
-- Values use psycopg named parameters, not string interpolation.
-- Cohort: selected players at ONE snapshot, API patch and fixed 24-hour window.
-- Counts are selected-player performances, not independent human experiments.
-- These questions were agreed in the walkthrough; the personal baseline is still blank.

-- QUERY Q1
-- Within each role, which eligible champion has the highest observed win rate?
-- Keep short games here; Q5 separately measures their effect. Unknown roles omitted.
with cohort as (
    select p.*, c.name as champion, s.tier, m.duration_seconds
    from public.participants p
    join public.matches m using (match_id)
    join public.champions c using (champion_id)
    join public.ladder_snapshots s on s.puuid=p.puuid and s.snapshot_at=%(snapshot_at)s
    where m.patch=%(patch)s
      and m.game_start >= %(snapshot_at)s::timestamptz-interval '24 hours'
      and m.game_start < %(snapshot_at)s::timestamptz
), stats as (
    select team_position as role, champion, count(*) as picks,
           sum(win::int) as wins, count(distinct puuid) as distinct_players,
           avg(win::int) as win_rate
    from cohort where team_position is not null
    group by team_position, champion having count(*) >= %(min_picks)s
), ranked as (
    select *, row_number() over (
        partition by role order by win_rate desc, picks desc, champion
    ) as position from stats
)
select role, champion, picks, wins, distinct_players,
       round(100*win_rate,2) as win_pct
from ranked where position=1 order by role;

-- QUERY Q2
-- Which champions are selected most, and how do their observed win rates compare?
-- Pick share uses ALL selected-player performances as denominator, not game count.
with cohort as (
    select p.*, c.name as champion, s.tier, m.duration_seconds
    from public.participants p
    join public.matches m using (match_id)
    join public.champions c using (champion_id)
    join public.ladder_snapshots s on s.puuid=p.puuid and s.snapshot_at=%(snapshot_at)s
    where m.patch=%(patch)s
      and m.game_start >= %(snapshot_at)s::timestamptz-interval '24 hours'
      and m.game_start < %(snapshot_at)s::timestamptz
)
select champion, count(*) as picks, sum(win::int) as wins,
       count(distinct puuid) as distinct_players,
       round(100.0*count(*)/(select count(*) from cohort),2) as pick_share_pct,
       round(100*avg(win::int),2) as win_pct
from cohort group by champion
order by picks desc, champion limit %(top_n)s;

-- QUERY Q3
-- What are the three most-picked champions in each observed tier, and their outcomes?
-- Tier is at collection, not at game time; no minimum for reporting popularity.
with cohort as (
    select p.*, c.name as champion, s.tier, m.duration_seconds
    from public.participants p
    join public.matches m using (match_id)
    join public.champions c using (champion_id)
    join public.ladder_snapshots s on s.puuid=p.puuid and s.snapshot_at=%(snapshot_at)s
    where m.patch=%(patch)s
      and m.game_start >= %(snapshot_at)s::timestamptz-interval '24 hours'
      and m.game_start < %(snapshot_at)s::timestamptz
), stats as (
    select tier, champion, count(*) as picks, sum(win::int) as wins,
           count(distinct puuid) as distinct_players,
           avg(win::int) as win_rate,
           100.0*count(*)/sum(count(*)) over (partition by tier) as tier_pick_share
    from cohort group by tier, champion
), ranked as (
    select *, row_number() over (partition by tier order by picks desc,champion) as position
    from stats
)
select tier, champion, picks, wins, distinct_players,
       round(tier_pick_share,2) as tier_pick_share_pct,
       round(100*win_rate,2) as win_pct,
       picks >= %(min_picks)s as passes_count_screen
from ranked where position <= 3
order by case tier when 'CHALLENGER' then 1 when 'GRANDMASTER' then 2 else 3 end,position;

-- QUERY Q4
-- Which same-role opponent matchups have the most observations? Are any at least 10?
-- Opponent need not be in selected tiers. Missing or ambiguous same-role pairs omitted.
-- Count screen is exploratory; it is not a statistical sufficiency guarantee.
with cohort as (
    select p.*, c.name as champion, s.tier, m.duration_seconds
    from public.participants p
    join public.matches m using (match_id)
    join public.champions c using (champion_id)
    join public.ladder_snapshots s on s.puuid=p.puuid and s.snapshot_at=%(snapshot_at)s
    where m.patch=%(patch)s
      and m.game_start >= %(snapshot_at)s::timestamptz-interval '24 hours'
      and m.game_start < %(snapshot_at)s::timestamptz
), paired as (
    select a.*, o.champion_id as opponent_id,
           count(*) over (partition by a.match_id,a.puuid) as opponent_count
    from cohort a join public.participants o
      on o.match_id=a.match_id and o.team_id<>a.team_id and o.team_position=a.team_position
    where a.team_position is not null
      and 1=(select count(*) from public.participants t
             where t.match_id=a.match_id and t.team_id=a.team_id and t.team_position=a.team_position)
), stats as (
    select p.team_position as role, p.champion, c.name as opponent,
           count(*) as games, sum(p.win::int) as wins,
           count(distinct p.puuid) as distinct_players
    from paired p join public.champions c on c.champion_id=p.opponent_id
    where opponent_count=1 group by p.team_position,p.champion,c.name
)
select role, champion, opponent, games, wins, distinct_players,
       round(100.0*wins/games,2) as win_pct,
       games >= %(min_matchups)s as passes_count_screen
from stats order by games desc,role,champion,opponent limit %(top_n)s;

-- QUERY Q5
-- How do champion rankings change when games under 300 seconds are excluded?
-- Observed 2026-11-06: Gwen had one observation removed, rate 58.06% -> 60.00%,
-- and rank 16 -> 11 in the common eligible set. All champions were recalculated,
-- so this is not an isolated-one-row causal claim. This sensitivity was an
-- unexpected assistant finding; the instructor's personal reaction is pending.
-- Overall champion ranking, not role-adjusted. Compare the SAME eligible champions:
-- require 30 selected-player observations both before and after the duration filter.
with cohort as (
    select p.*, c.name as champion, s.tier, m.duration_seconds
    from public.participants p
    join public.matches m using (match_id)
    join public.champions c using (champion_id)
    join public.ladder_snapshots s on s.puuid=p.puuid and s.snapshot_at=%(snapshot_at)s
    where m.patch=%(patch)s
      and m.game_start >= %(snapshot_at)s::timestamptz-interval '24 hours'
      and m.game_start < %(snapshot_at)s::timestamptz
), stats as (
    select champion,count(*) as all_picks,sum(win::int) as all_wins,
           count(*) filter (where duration_seconds>=300) as filtered_picks,
           sum(win::int) filter (where duration_seconds>=300) as filtered_wins
    from cohort group by champion
), eligible as (
    select *,1.0*all_wins/all_picks as all_rate,
           1.0*filtered_wins/filtered_picks as filtered_rate
    from stats where all_picks >= %(min_picks)s and filtered_picks >= %(min_picks)s
), ranked as (
    select *,rank() over (order by all_rate desc) as all_rank,
           rank() over (order by filtered_rate desc) as filtered_rank
    from eligible
)
select champion,all_picks,filtered_picks,all_picks-filtered_picks as excluded_picks,
       round(100*all_rate,2) as all_win_pct,
       round(100*filtered_rate,2) as filtered_win_pct,
       all_rank,filtered_rank,filtered_rank-all_rank as rank_change,
       round(100*(filtered_rate-all_rate),2) as rate_change_pp
from ranked
order by abs(filtered_rank-all_rank) desc,abs(filtered_rate-all_rate) desc,all_rank,champion
limit %(top_n)s;
