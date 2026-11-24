"""Run a fresh, bounded collection and publish aggregate-only Actions evidence."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'daily': {'max_calls': 1200, 'max_players': 300, 'max_matches': 550},
    'smoke': {'max_calls': 80, 'max_players': 20, 'max_matches': 40},
}


def report_issues(report):
    """Budget limits are expected; API errors and broken stored games are failures."""
    issues = []
    for field in ('discovery_errors', 'detail_request_failures',
                  'duplicate_matches', 'games_without_ten_participants'):
        if report[field]:
            issues.append(field)
    if report['rejected_cached_matches'].get('database_constraint', 0):
        issues.append('database_constraint')
    if not sum(tier['checked'] for tier in report['tier_coverage'].values()):
        issues.append('no_player_histories_checked')
    return issues


def write_summary(report, destination):
    lines = ['## Riot match collection', '', f"Status: **{report['status']}**", '',
             f"Profile: {report['profile']}", '',
             'Bounded NA ranked-solo sample from Challenger, Grandmaster, and Master.', '',
             'A fresh 48-hour window overlaps the previous daily run; it does not backfill an extended outage.', '']
    if report.get('issues'):
        lines += ['Issues: ' + ', '.join(report['issues']), '']
    collection = report.get('collection')
    if collection:
        lines += [f"Window (UTC): {collection['window_start_utc']} to {collection['snapshot_at_utc']}", '',
                  '| Measure | Count |', '|---|---:|',
                  f"| Riot API calls | {collection['api_calls_this_invocation']} |",
                  f"| Player histories checked | {sum(t['checked'] for t in collection['tier_coverage'].values())} |",
                  f"| New matches inserted | {collection['inserted']['matches']} |",
                  f"| New participant rows | {collection['inserted']['participants']} |",
                  f"| Total stored matches | {collection['table_counts']['matches']} |", '',
                  'Champion reference counts include inserted or changed rows. Completed matches stay immutable.', '',
                  'Coverage is limited by the configured player, detail, and API budgets. '
                  'Zero new matches can be valid when discovered matches are already stored.', '']
    else:
        lines += ['Collection did not finish. Check the collector step for a sanitized error type. '
                  'For AuthError, check the Riot key; for database errors, check the Supabase session-pooler secret.', '']
    Path(destination).write_text('\n'.join(lines), encoding='utf-8')


def main():
    load_dotenv(ROOT / '.env')
    profile = os.environ.get('SYNC_PROFILE', 'daily')
    if profile not in PROFILES:
        print('SYNC_PROFILE must be daily or smoke.', flush=True)
        return 1
    # The run ID remains stable for a rerun; attempt creates a fresh observation.
    # No raw inputs, manifests, player IDs, or key fingerprints are uploaded.
    run_id = os.environ.get('GITHUB_RUN_ID', datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    if not all(part.isalnum() for part in (run_id, attempt)):
        print('Invalid run identifier.', flush=True)
        return 1
    directory = ROOT / 'data' / 'raw' / f'sync_{run_id}_{attempt}'
    directory.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'profile': profile, 'issues': []}
    missing = [key for key in ('DATABASE_URL', 'RIOT_API_KEY') if not os.environ.get(key, '').strip()]
    if missing:
        report['issues'] = ['missing_' + key for key in missing]
        print('Missing required secrets: ' + ', '.join(missing), flush=True)
    else:
        # Require TLS without changing or printing the connection URL.
        os.environ['PGSSLMODE'] = 'require'
        command = [sys.executable, str(ROOT / 'ingest' / 'load.py'),
                   '--run-dir', str(directory.relative_to(ROOT)),
                   '--window-hours', '48', '--refresh-reference']
        for name, value in PROFILES[profile].items():
            command.extend(['--' + name.replace('_', '-'), str(value)])
        result = subprocess.run(command, cwd=ROOT, check=False)
        collection_path = directory / 'collection_report.json'
        if result.returncode == 0 and collection_path.exists():
            report['collection'] = json.loads(collection_path.read_text(encoding='utf-8'))
            report['issues'] = report_issues(report['collection'])
            report['status'] = 'failed' if report['issues'] else 'success'
        else:
            report['issues'] = ['collector_failed']
    (directory / 'sync_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        write_summary(report, os.environ['GITHUB_STEP_SUMMARY'])
    print(json.dumps({'sync_status': report['status'], 'issues': report['issues']}), flush=True)
    return 0 if report['status'] == 'success' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print exception strings that could expose database credentials.
        print(json.dumps({'sync_status': 'failed', 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1)
