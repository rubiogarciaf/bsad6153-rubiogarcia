"""M2 bounded collection and fixed-input loading. Reuses riot.py and archived transforms.

Collect/resume: python ingest/load.py --run-dir data/raw/m2_demo
Replay only:   python ingest/load.py --run-dir data/raw/m2_demo --replay

The ignored cache preserves the collection anchor and inputs. A resumed run keeps
that anchor; a new directory starts a new observation. Logs contain aggregates,
never credentials, connection strings, or player identifiers.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys

import psycopg
from psycopg import sql
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from riot import RiotClient, AuthError, ApiError, BudgetExhausted, data_dragon_champions

ROOT = Path(__file__).resolve().parents[1]
TIERS = ('CHALLENGER', 'GRANDMASTER', 'MASTER')
POSITIONS = {'TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY'}
TABLES = ('players', 'champions', 'ladder_snapshots', 'matches', 'participants')
PART_FIELDS = ('puuid', 'teamId', 'teamPosition', 'championId', 'win', 'kills',
               'deaths', 'assists', 'champLevel', 'goldEarned', 'totalMinionsKilled',
               'neutralMinionsKilled', 'visionScore')


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def save_json(path, obj):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def insert_rows(cur, table, rows):
    """Trusted identifiers are quoted; every API value uses a placeholder."""
    if table not in TABLES:
        raise ValueError('Unknown destination table')
    if not rows:
        return 0
    columns = list(rows[0])
    statement = sql.SQL('INSERT INTO public.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns))
    cur.executemany(statement, [tuple(row[c] for c in columns) for row in rows])
    return cur.rowcount


def discovery_order(ladder, anchor):
    """Seeded shuffle within tiers, then interleave; never sort by highest LP."""
    rng = random.Random(anchor)
    groups = [[p['puuid'] for p in ladder if p['tier'] == tier] for tier in TIERS]
    for group in groups:
        rng.shuffle(group)
    return [group[i] for i in range(max(map(len, groups), default=0))
            for group in groups if i < len(group)]


def trim_match(payload):
    info = payload['info']
    return {'metadata': {'matchId': payload['metadata']['matchId']}, 'info': {
        **{k: info.get(k) for k in ('gameVersion', 'queueId', 'gameStartTimestamp',
                                   'gameDuration', 'endOfGameResult')},
        'teams': [{k: t[k] for k in ('teamId', 'win')} for t in info['teams']],
        'participants': [{k: p.get(k) for k in PART_FIELDS} for p in info['participants']]}}


def validate_match(payload, selected, anchor, champions, window_hours=24):
    """Validate game eligibility and cross-row rules before any game write."""
    info = payload['info']
    people = info['participants']
    game_id = payload['metadata']['matchId']
    if not re.fullmatch(r'NA1_[0-9]+', game_id):
        return 'outside_NA'
    if info['queueId'] != 420:
        return 'wrong_queue'
    started = datetime.fromtimestamp(info['gameStartTimestamp'] / 1000, timezone.utc)
    if not anchor - timedelta(hours=window_hours) <= started < anchor:
        return 'outside_window'
    if len(people) != 10 or len({p['puuid'] for p in people}) != 10 or any(not p['puuid'] for p in people):
        return 'invalid_participant_count_or_identity'
    if not any(p['puuid'] in selected for p in people):
        return 'no_selected_player'
    if Counter(p['teamId'] for p in people) != {100: 5, 200: 5}:
        return 'invalid_team_sizes'
    teams = {t['teamId']: t['win'] for t in info['teams']}
    if set(teams) != {100, 200} or any(type(v) is not bool for v in teams.values()) or sum(teams.values()) != 1:
        return 'invalid_team_outcomes'
    if any(type(p['win']) is not bool or p['win'] != teams[p['teamId']] for p in people):
        return 'inconsistent_participant_outcomes'
    if any(p['championId'] not in champions for p in people):
        return 'unknown_champion'
    if info.get('endOfGameResult') not in (None, 'GameComplete'):
        return 'not_completed'
    return None


def match_rows(payload):
    """Adapted from archived upsert.py; PostgreSQL now generates patch."""
    info = payload['info']
    game_id = payload['metadata']['matchId']
    match = {'match_id': game_id, 'game_version': info['gameVersion'],
             'queue_id': info['queueId'],
             'game_start': datetime.fromtimestamp(info['gameStartTimestamp'] / 1000, timezone.utc),
             'duration_seconds': info['gameDuration'],
             'blue_win': next(t['win'] for t in info['teams'] if t['teamId'] == 100)}
    participants = [{'match_id': game_id, 'puuid': p['puuid'], 'team_id': p['teamId'],
                     'team_position': p['teamPosition'] if p.get('teamPosition') in POSITIONS else None,
                     'champion_id': p['championId'], 'win': p['win'],
                     'kills': p['kills'], 'deaths': p['deaths'], 'assists': p['assists'],
                     'champion_level': p['champLevel'], 'gold_earned': p['goldEarned'],
                     'creep_score': p['totalMinionsKilled'] + p['neutralMinionsKilled'],
                     'vision_score': p['visionScore']} for p in info['participants']]
    return match, participants


def refresh_champions(cur, rows):
    """Refresh current reference facts only when changed; preserve audit times on replay."""
    columns = list(rows[0]) if rows else []
    if not columns:
        return 0
    changed = [c for c in columns if c != 'champion_id']
    statement = sql.SQL(
        'INSERT INTO public.champions AS stored ({}) VALUES ({}) '
        'ON CONFLICT (champion_id) DO UPDATE SET {} WHERE ({}) IS DISTINCT FROM ({})'
    ).format(
        sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns),
        sql.SQL(',').join(sql.SQL('{} = EXCLUDED.{}').format(sql.Identifier(c), sql.Identifier(c)) for c in changed),
        sql.SQL(',').join(sql.SQL('stored.{}').format(sql.Identifier(c)) for c in changed),
        sql.SQL(',').join(sql.SQL('EXCLUDED.{}').format(sql.Identifier(c)) for c in changed))
    cur.executemany(statement, [tuple(row[c] for c in columns) for row in rows])
    return cur.rowcount


def load_reference(conn, state, refresh=False):
    champion_rows = [{'champion_id': int(c['key']), 'champion_key': c['id'],
                      'name': c['name'], 'title': c['title'], 'tags': c.get('tags') or [],
                      'ddragon_version': state['reference_version']} for c in state['champions']]
    snapshots = [{'snapshot_at': state['snapshot_at'], **p} for p in state['ladder']]
    with conn.transaction():
        cur = conn.cursor()
        return {
            'players': insert_rows(cur, 'players', [{'puuid': p['puuid']} for p in state['ladder']]),
            'champions': refresh_champions(cur, champion_rows) if refresh else insert_rows(cur, 'champions', champion_rows),
            'ladder_snapshots': insert_rows(cur, 'ladder_snapshots', snapshots)}


def complete_match_ids(conn):
    return {row[0] for row in conn.execute(
        'SELECT m.match_id FROM public.matches m JOIN public.participants p USING(match_id) '
        'GROUP BY m.match_id HAVING count(*)=10')}


def load_cached(conn, directory, state, paths=None):
    anchor = datetime.fromisoformat(state['snapshot_at'])
    selected = {p['puuid'] for p in state['ladder']}
    champions = {int(c['key']) for c in state['champions']}
    inserted = Counter()
    rejected = Counter()
    accepted = 0
    null_roles = 0
    # Completed immutable game IDs need no further writes on resume/replay.
    # Incomplete games still go through the atomic insertion and count check.
    complete = complete_match_ids(conn)
    for path in sorted(paths if paths is not None else (directory / 'matches').glob('NA1_*.json')):
        payload = read_json(path)
        reason = validate_match(payload, selected, anchor, champions, state.get('window_hours', 24))
        if reason:
            rejected[reason] += 1
            continue
        match, people = match_rows(payload)
        if match['match_id'] in complete:
            accepted += 1
            null_roles += sum(p['team_position'] is None for p in people)
            continue
        try:
            with conn.transaction():
                cur = conn.cursor()
                added_players = insert_rows(cur, 'players', [{'puuid': p['puuid']} for p in people])
                added_matches = insert_rows(cur, 'matches', [match])
                added_people = insert_rows(cur, 'participants', people)
                count = cur.execute('SELECT count(*) FROM public.participants WHERE match_id=%s', (match['match_id'],)).fetchone()[0]
                if count != 10:
                    raise ValueError('Stored game does not have ten participants')
        except psycopg.IntegrityError:
            rejected['database_constraint'] += 1
            continue
        inserted.update(players=added_players, matches=added_matches, participants=added_people)
        accepted += 1
        null_roles += sum(p['team_position'] is None for p in people)
    return {'accepted_cached_matches': accepted, 'rejected_cached_matches': dict(rejected),
            'normalized_null_roles': null_roles,
            'inserted': {t: inserted[t] for t in ('players', 'matches', 'participants')}}


def aggregate_report(conn, state, outcome, inserted_reference, calls):
    ladder_counts = Counter(p['tier'] for p in state['ladder'])
    checked = set(state['checked_players'])
    checked_counts = Counter(p['tier'] for p in state['ladder'] if p['puuid'] in checked)
    return {
        'measured_at_utc': utcnow().isoformat(), 'snapshot_at_utc': state['snapshot_at'],
        'window_start_utc': (datetime.fromisoformat(state['snapshot_at']) - timedelta(hours=state.get('window_hours', 24))).isoformat(),
        'window_hours': state.get('window_hours', 24),
        'ladder_observation_started_utc': state['ladder_observation_started'],
        'ladder_observation_finished_utc': state['ladder_observation_finished'],
        'reference_version': state['reference_version'], 'api_calls_this_invocation': calls,
        'discovered_unique_match_ids': len(state['match_ids']),
        'discovery_complete': len(checked) == len(state['ladder']),
        'tier_coverage': {tier: {'selected': ladder_counts[tier], 'checked': checked_counts[tier],
                               'checked_percent': round(100 * checked_counts[tier] / ladder_counts[tier], 2)
                               if ladder_counts[tier] else None} for tier in TIERS},
        'inserted_reference_rows': inserted_reference, **outcome,
        'table_counts': {t: conn.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(t))).fetchone()[0] for t in TABLES},
        'database_patch_counts': [{'patch': p, 'matches': n} for p, n in conn.execute('SELECT patch,count(*) FROM public.matches GROUP BY patch ORDER BY patch')],
        'game_start_range_utc': [v.isoformat() if v else None for v in conn.execute('SELECT min(game_start),max(game_start) FROM public.matches').fetchone()],
        'duplicate_matches': conn.execute('SELECT count(*)-count(DISTINCT match_id) FROM public.matches').fetchone()[0],
        'games_without_ten_participants': conn.execute('SELECT count(*) FROM (SELECT m.match_id FROM public.matches m LEFT JOIN public.participants p USING(match_id) GROUP BY m.match_id HAVING count(p.puuid)<>10) x').fetchone()[0],
        'short_games_under_300_seconds': conn.execute('SELECT count(*) FROM public.matches WHERE duration_seconds<300').fetchone()[0],
        'cautions': ['A bounded teaching batch is not complete population coverage.',
                     'Equal-tier interleaving oversamples smaller tiers relative to Master.',
                     'Rank is observed at collection, not necessarily at game time.',
                     'Match versions and champion-reference version are separate evidence.',
                     'Results do not establish causal strength or transfer to other player populations.']}


def collect(client, directory, args):
    manifest = directory / 'manifest.json'
    fingerprint = hashlib.sha256(client.session.headers['X-Riot-Token'].encode()).hexdigest()
    if manifest.exists():
        state = read_json(manifest)
        if state['key_fingerprint'] != fingerprint:
            raise ValueError('API key changed; do not mix this cache with a different key')
        if state.get('window_hours', 24) != args.window_hours:
            raise ValueError('Window changed; use a new cache directory')
    else:
        anchor = utcnow()
        version, champions = data_dragon_champions()
        ladder = {}
        observed_started = utcnow().isoformat()
        duplicates = 0
        for tier in TIERS:
            for entry in client.apex_league(tier.lower()):
                puuid = entry['puuid']
                if puuid in ladder:
                    duplicates += 1
                    continue  # retain first observed tier if a player moves during collection
                ladder[puuid] = {'puuid': puuid, 'tier': tier,
                                 'league_points': entry['leaguePoints'],
                                 'wins': entry['wins'], 'losses': entry['losses']}
        state = {'snapshot_at': anchor.isoformat(), 'window_hours': args.window_hours, 'reference_version': version,
                 'champions': champions, 'ladder': list(ladder.values()),
                 'ladder_observation_started': observed_started,
                 'ladder_observation_finished': utcnow().isoformat(),
                 'duplicate_ladder_entries': duplicates, 'key_fingerprint': fingerprint,
                 'checked_players': [], 'match_ids': [], 'detail_failures': {}, 'discovery_errors': 0}
        save_json(manifest, state)
        print(json.dumps({'stage': 'ladder_saved', 'selected_players': len(ladder),
                          'reference_version': version, 'snapshot_at': state['snapshot_at']}), flush=True)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--max-calls', type=int, default=1200)
    parser.add_argument('--max-players', type=int, default=300, help='Discovery players per invocation; resume checks new players')
    parser.add_argument('--max-matches', type=int, default=550, help='Maximum cached match details; raise to continue')
    parser.add_argument('--window-hours', type=int, default=24, help='Lookback saved in the manifest; scheduled runs use 48 for overlap')
    parser.add_argument('--refresh-reference', action='store_true', help='Update changed champion-reference facts (not allowed for fixed-input replay)')
    parser.add_argument('--replay', action='store_true', help='Load the exact saved input without calling Riot')
    args = parser.parse_args()
    if min(args.max_calls, args.max_players, args.max_matches, args.window_hours) < 1:
        parser.error('Limits must be positive')
    if args.replay and args.refresh_reference:
        parser.error('Fixed-input replay must not refresh champion references')
    directory = (ROOT / args.run_dir).resolve()
    if not directory.is_relative_to((ROOT / 'data/raw').resolve()):
        parser.error('Private cache must be inside ignored data/raw')
    load_dotenv(ROOT / '.env')
    if not os.environ.get('DATABASE_URL'):
        parser.error('DATABASE_URL is missing; configure it privately')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'matches').mkdir(exist_ok=True)
    client = None
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=15, autocommit=True) as conn:
        conn.execute("SET statement_timeout='30s'")
        if args.replay:
            state = read_json(directory / 'manifest.json')
        else:
            if not os.environ.get('RIOT_API_KEY'):
                parser.error('RIOT_API_KEY is missing; configure it privately')
            client = RiotClient(max_calls=args.max_calls)
            state = collect(client, directory, args)
        reference = load_reference(conn, state, refresh=args.refresh_reference)
        # Replay/resume cached inputs once, then load only newly downloaded chunks.
        outcome = load_cached(conn, directory, state)
        pending_paths = []
        def merge_progress(progress):
            for key in ('accepted_cached_matches', 'normalized_null_roles'):
                outcome[key] += progress[key]
            for key in ('inserted', 'rejected_cached_matches'):
                combined = Counter(outcome[key])
                combined.update(progress[key])
                outcome[key] = dict(combined)
        if client:
            anchor = datetime.fromisoformat(state['snapshot_at'])
            checked = set(state['checked_players'])
            ids = set(state['match_ids'])
            discovered = 0
            # Keep most of the call budget available for complete match details.
            for puuid in discovery_order(state['ladder'], state['snapshot_at']):
                if puuid in checked:
                    continue
                if discovered >= args.max_players or client.calls_left <= args.max_matches + 10:
                    break
                try:
                    found = client.match_ids_since(puuid, (anchor-timedelta(hours=state.get('window_hours', 24))).timestamp(), anchor.timestamp())
                except AuthError:
                    raise
                except BudgetExhausted:
                    break
                except ApiError:
                    state['discovery_errors'] += 1
                    continue
                ids.update(i for i in found if re.fullmatch(r'NA1_[0-9]+', i))
                checked.add(puuid)
                discovered += 1
                state['checked_players'] = sorted(checked)
                state['match_ids'] = sorted(ids)
                save_json(directory / 'manifest.json', state)
                if discovered % 25 == 0:
                    print(json.dumps({'stage':'discover','players_checked':len(checked),'unique_match_ids':len(ids),'calls':client.calls_made}), flush=True)
            rng = random.Random(state['snapshot_at'])
            pending = sorted(ids)
            rng.shuffle(pending)
            cached_count = len(list((directory / 'matches').glob('NA1_*.json')))
            already_stored = complete_match_ids(conn)
            for match_id in pending:
                if match_id in already_stored:
                    continue
                path = directory / 'matches' / (match_id + '.json')
                if path.exists():
                    continue
                if cached_count >= args.max_matches or client.calls_left <= 0:
                    break
                try:
                    payload = trim_match(client.match(match_id))
                    if payload['metadata']['matchId'] != match_id:
                        raise ValueError('Returned match ID differs from requested ID')
                    save_json(path, payload)
                except AuthError:
                    raise
                except BudgetExhausted:
                    break
                except ApiError:
                    state['detail_failures'][match_id] = 'API request failed'
                    save_json(directory / 'manifest.json', state)
                    continue
                cached_count += 1
                pending_paths.append(path)
                # Periodically load durable cached inputs so progress is visible.
                if cached_count % 50 == 0:
                    progress = load_cached(conn, directory, state, paths=pending_paths)
                    merge_progress(progress)
                    pending_paths.clear()
                    print(json.dumps({'stage':'download_and_load','cached_matches':cached_count,
                                      'accepted_matches':outcome['accepted_cached_matches'],
                                      'rejected':outcome['rejected_cached_matches'],'calls':client.calls_made}), flush=True)
        if pending_paths:
            merge_progress(load_cached(conn, directory, state, paths=pending_paths))
        report = aggregate_report(conn, state, outcome, reference, client.calls_made if client else 0)
        report['mode'] = 'fixed_input_replay' if args.replay else 'bounded_collection'
        report['discovery_errors'] = state['discovery_errors']
        report['detail_request_failures'] = len(state['detail_failures'])
        report['duplicate_ladder_entries'] = state['duplicate_ladder_entries']
        report['reference_refresh_enabled'] = args.refresh_reference
        report['reference_row_counts_meaning'] = 'inserted or changed' if args.refresh_reference else 'inserted only'
        report_path = directory / ('replay_report.json' if args.replay else 'collection_report.json')
        save_json(report_path, report)
        print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Exception strings from drivers/HTTP clients can contain connection data.
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,
                          'sqlstate':getattr(exc,'sqlstate',None),
                          'next_step':'Inspect privately; rerun with the same cache directory to resume.'}), flush=True)
        raise SystemExit(1)
