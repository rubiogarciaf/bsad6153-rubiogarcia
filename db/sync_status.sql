-- Run these read-only queries in Supabase > SQL Editor.
-- Re-run while a GitHub collection is active to see newly committed batches.
-- UTC timestamps below are deliberately labeled; game time and load time differ.

-- 1. Current totals and the last time a match was actually inserted.
select
    now() at time zone 'UTC' as checked_at_utc,
    (select count(*) from public.matches) as total_matches,
    (select count(*) from public.participants) as total_participant_rows,
    (select max(first_seen_at) at time zone 'UTC' from public.matches) as latest_match_loaded_at_utc,
    (select max(game_start) at time zone 'UTC' from public.matches) as latest_game_started_at_utc;

-- 2. When matches arrived today/recently, grouped by their insertion minute.
-- A run can load games played yesterday, so group by first_seen_at, not game_start.
select
    date_trunc('minute', first_seen_at at time zone 'UTC') as loaded_at_minute_utc,
    count(*) as matches_inserted
from public.matches
where first_seen_at >= now() - interval '24 hours'
group by 1
order by 1 desc;

-- 3. The most recently inserted matches. No player identities are selected.
select
    match_id,
    patch,
    game_start at time zone 'UTC' as game_started_at_utc,
    first_seen_at at time zone 'UTC' as loaded_at_utc
from public.matches
order by first_seen_at desc, match_id
limit 20;
