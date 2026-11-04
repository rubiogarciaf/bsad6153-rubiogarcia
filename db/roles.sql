-- db/roles.sql: who may do what in your database (Week 2, extended in Weeks 3 and 6).
--
-- NEVER PUT A PASSWORD IN THIS FILE. It is committed to GitHub.
-- Run this file as the owner, then set the password separately in the Supabase
-- SQL editor (and nowhere in your repository):
--
--     alter role app_reader with password '...long random password...';
--
-- Then put the role's connection string in .env as APP_DATABASE_URL.
-- Safe to rerun.

-- Week 2: a read-only role for the website, chatbot, and golden-question checker.
do $$
begin
    if not exists (select from pg_roles where rolname = 'app_reader') then
        create role app_reader login;
    end if;
end
$$;

grant usage on schema public to app_reader;
grant select on players, champions, matches, participants, bans to app_reader;

-- Week 3: the app and chatbot read the run history (to show "last updated" and warn
-- when data is stale), but only the sync, as the owner, may write to it.
grant select on sync_runs, match_queue to app_reader;

-- Supabase only: by default its Data API roles (anon, authenticated) can read and write
-- new tables in "public". We don't use the Data API, so take that access away.
-- On a plain PostgreSQL server these roles don't exist, and this block does nothing.
do $$
begin
    if exists (select from pg_roles where rolname = 'anon') then
        revoke all on players, champions, matches, participants, bans, match_queue, sync_runs
            from anon, authenticated;
    end if;
end
$$;

-- Week 6: the agent's own role (see the Week 6 page for the exact grants).
-- create role agent_writer login;
-- grant usage on schema public to agent_writer;
-- grant select on <your data tables>, sync_runs, agent_runs, agent_proposals to agent_writer;
-- grant insert on agent_runs, agent_proposals to agent_writer;
-- grant update on agent_runs to agent_writer;
--
-- Week 6: the one write your website may do, which covers only the review columns.
-- grant select on agent_runs, agent_proposals to app_reader;
-- grant update (status, reviewed_at, reviewer_note) on agent_proposals to app_reader;
