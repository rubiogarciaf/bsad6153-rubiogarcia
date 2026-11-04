-- db/schema.sql: instructor reference design, Riot Games API (Weeks 2 and 3).
--
-- Decision this database supports: which champion should I play next, based on what is
-- winning right now in high-elo North America (Diamond I and above, ranked solo queue)?
--
-- One row means:
--   players          one player on today's high-elo ladder (Challenger, Grandmaster, Master, Diamond I)
--   champions        one champion, from Riot's Data Dragon
--   matches          one finished ranked solo game in which all ten players were on the ladder
--   participants     one player's performance in one of those games
--   bans             one champion banned by one team in one game
--   match_queue      one match ID the sync has discovered, and what happened to it   (Week 3)
--   sync_runs        one run of ingest/sync.py                                       (Week 3)
--
-- Keys: Riot's own IDs are the primary keys (a primary key is already unique).
-- sync_runs is the one table whose ID we create ourselves, so it uses an identity column.
--
-- Audit columns: first_seen_at is set once. last_updated_at changes only when a tracked
-- value really changes; the upsert in ingest/upsert.py does that, because a default can't.
--
-- Safe to rerun: "if not exists" skips what is already there.

create table if not exists players (
    puuid                  text primary key,          -- Riot's player ID. Encrypted PER API KEY: a new key, new IDs.
    tier                   text not null check (tier in ('CHALLENGER', 'GRANDMASTER', 'MASTER', 'DIAMOND')),
    division               text not null check (division in ('I', 'II', 'III', 'IV')),
    league_points          integer not null check (league_points >= 0),
    wins                   integer not null check (wins >= 0),
    losses                 integer not null check (losses >= 0),
    hot_streak             boolean not null,
    veteran                boolean not null,
    fresh_blood            boolean not null,
    inactive               boolean not null,
    -- Bookkeeping, set by the sync. Changing these does NOT move last_updated_at.
    last_seen_on_ladder_at timestamptz not null default now(),  -- the latest run that saw them on the ladder
    matches_checked_at     timestamptz,               -- per-player watermark: games after this are new
    first_seen_at          timestamptz not null default now(),
    last_updated_at        timestamptz not null default now()
);

create table if not exists champions (
    champion_id     integer primary key,              -- Data Dragon "key", e.g. 103; matches participants.championId
    champion_key    text not null unique,             -- e.g. Ahri
    name            text not null,
    title           text not null,
    tags            text[] not null default '{}',     -- e.g. {Mage,Assassin}
    ddragon_version text not null,                    -- the patch these names came from
    first_seen_at   timestamptz not null default now(),
    last_updated_at timestamptz not null default now()
);

create table if not exists matches (
    match_id         text primary key,                -- e.g. NA1_5672929226
    game_version     text not null,                   -- e.g. 16.21.834.2754
    patch            text not null,                   -- e.g. 16.21, derived from game_version
    queue_id         integer not null check (queue_id = 420),   -- ranked solo/duo only
    game_start       timestamptz not null,
    duration_seconds integer not null check (duration_seconds >= 300),  -- remakes are filtered out
    blue_win         boolean not null,                -- team 100 won
    first_seen_at    timestamptz not null default now(),
    last_updated_at  timestamptz not null default now()
);

create table if not exists participants (
    match_id        text     not null references matches (match_id),
    puuid           text     not null references players (puuid),   -- the filter guarantees all ten are on the ladder
    team_id         smallint not null check (team_id in (100, 200)), -- 100 = blue, 200 = red
    team_position   text     check (team_position in ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')),
    champion_id     integer  not null references champions (champion_id),
    win             boolean  not null,
    kills           smallint not null check (kills >= 0),
    deaths          smallint not null check (deaths >= 0),
    assists         smallint not null check (assists >= 0),
    -- We first assumed a max of 18. The first real sync stored a higher level and failed
    -- this check (run 1, 2026-11-04). The cap is no longer 18, so only a floor is enforced.
    champion_level  smallint not null check (champion_level >= 1),
    gold_earned     integer  not null check (gold_earned >= 0),
    creep_score     integer  not null check (creep_score >= 0),     -- lane minions + jungle monsters
    vision_score    integer  not null check (vision_score >= 0),
    first_seen_at   timestamptz not null default now(),
    last_updated_at timestamptz not null default now(),
    primary key (match_id, puuid)
);

create table if not exists bans (
    match_id        text     not null references matches (match_id),
    team_id         smallint not null check (team_id in (100, 200)),
    pick_turn       smallint not null check (pick_turn between 1 and 10),
    champion_id     integer  not null references champions (champion_id),  -- "no ban" (-1) is not stored
    first_seen_at   timestamptz not null default now(),
    last_updated_at timestamptz not null default now(),
    primary key (match_id, team_id, pick_turn)
);

-- Week 3 ------------------------------------------------------------------------

create table if not exists match_queue (
    match_id        text primary key,
    discovered_at   timestamptz not null default now(),
    status          text not null default 'pending'
                    check (status in ('pending', 'stored', 'filtered_out', 'failed')),
    reason          text,                             -- why it was filtered out or failed
    attempts        smallint not null default 0,
    resolved_at     timestamptz
);

create table if not exists sync_runs (
    run_id            bigint generated always as identity primary key,
    started_at        timestamptz not null default now(),
    finished_at       timestamptz,
    status            text not null check (status in ('success', 'partial', 'failed')),
    trigger           text not null check (trigger in ('schedule', 'manual')),
    records_fetched   integer not null default 0,     -- match details downloaded
    records_inserted  integer not null default 0,     -- new players + new matches stored
    records_updated   integer not null default 0,     -- players whose rank, LP, or record changed
    api_calls         integer not null default 0,
    players_checked   integer not null default 0,
    matches_filtered  integer not null default 0,     -- downloaded but not all ten on the ladder, or a remake
    queue_pending     integer,                        -- match IDs still waiting when the run ended
    error_message     text                            -- never contains a secret
);

create index if not exists participants_champion_idx on participants (champion_id);
create index if not exists matches_game_start_idx on matches (game_start);
create index if not exists match_queue_pending_idx on match_queue (discovered_at) where status = 'pending';
create index if not exists players_check_order_idx on players (matches_checked_at nulls first);
