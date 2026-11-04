-- db/queries.sql: business questions answered with SQL (Week 2).
--
-- Each query answers a question I ask before choosing a champion.
-- Requirements covered: JOINs in Q1-Q3 and Q5; GROUP BY in Q1-Q4; time filter in Q1, Q2, Q4;
-- the surprise is Q3. Results change as the sync brings in new games.

-- Q1. Which champions are winning the most in high-elo NA this week (at least 30 games)?
--     The minimum stops a 3-0 champion from topping the list.
select c.name                                 as champion,
       count(*)                               as games,
       round(100.0 * avg(p.win::int), 1)      as win_pct
from participants p
join matches m   using (match_id)
join champions c using (champion_id)
where m.game_start > now() - interval '7 days'
group by c.name
having count(*) >= 30
order by win_pct desc, games desc
limit 10;

-- Q2. For each role, which champion is picked most this week?
select distinct on (p.team_position)
       p.team_position                        as role,
       c.name                                 as champion,
       count(*)                               as games
from participants p
join matches m   using (match_id)
join champions c using (champion_id)
where m.game_start > now() - interval '7 days'
  and p.team_position is not null
group by p.team_position, c.name
order by p.team_position, games desc;

-- Q3. Are the most-banned champions also the ones that win the most?
--     SURPRISE: <!-- INSTRUCTOR: run this after a few full syncs and write, in your own
--     words, what surprised you. Note the date and the number of games. -->
with banned as (
    select b.champion_id, count(*) as bans
    from bans b join matches m using (match_id)
    where m.game_start > now() - interval '7 days'
    group by b.champion_id
), played as (
    select p.champion_id, count(*) as games, avg(p.win::int) as win_rate
    from participants p join matches m using (match_id)
    where m.game_start > now() - interval '7 days'
    group by p.champion_id
)
select c.name, banned.bans, played.games, round(100.0 * played.win_rate, 1) as win_pct
from banned
join champions c using (champion_id)
left join played using (champion_id)
order by banned.bans desc
limit 10;

-- Q4. Does blue side or red side win more often, by patch?
select patch,
       count(*)                               as games,
       round(100.0 * avg(blue_win::int), 1)   as blue_win_pct
from matches
where game_start > now() - interval '7 days'
group by patch
order by patch desc;

-- Q5. What do Challenger players play, compared with Diamond I players?
--     Tier is the player's tier TODAY (from the ladder), not at the time of the game.
select pl.tier,
       c.name                                 as champion,
       count(*)                               as games
from participants p
join players pl  using (puuid)
join champions c using (champion_id)
where pl.tier in ('CHALLENGER', 'DIAMOND')
group by pl.tier, c.name
order by pl.tier, games desc;
