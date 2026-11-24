"""Check failure signaling and fresh-window orchestration without network access."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scheduled_sync', ROOT / 'ingest/sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def healthy_report():
    return {'discovery_errors': 0, 'detail_request_failures': 0,
            'duplicate_matches': 0, 'games_without_ten_participants': 0,
            'rejected_cached_matches': {'outside_window': 1},
            'tier_coverage': {'MASTER': {'checked': 20}}}


class ScheduledCollection(unittest.TestCase):
    def test_expected_filtering_and_no_new_matches_are_not_errors(self):
        self.assertEqual(sync.report_issues(healthy_report()), [])

    def test_api_and_database_failures_do_not_pass_as_success(self):
        for field in ('discovery_errors', 'detail_request_failures',
                      'duplicate_matches', 'games_without_ten_participants'):
            with self.subTest(field=field):
                report = healthy_report()
                report[field] = 1
                self.assertIn(field, sync.report_issues(report))
        report = healthy_report()
        report['rejected_cached_matches']['database_constraint'] = 1
        self.assertIn('database_constraint', sync.report_issues(report))

    def test_no_histories_checked_is_a_failure(self):
        report = healthy_report()
        report['tier_coverage']['MASTER']['checked'] = 0
        self.assertIn('no_player_histories_checked', sync.report_issues(report))

    def test_runner_uses_fresh_attempt_window_and_fails_on_collector_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            with patch.object(sync, 'ROOT', root), patch.object(sync, 'load_dotenv'), \
                 patch.dict(os.environ, {'DATABASE_URL': 'placeholder', 'RIOT_API_KEY': 'placeholder',
                                         'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2',
                                         'SYNC_PROFILE': 'smoke', 'GITHUB_STEP_SUMMARY': ''}, clear=True), \
                 patch.object(sync.subprocess, 'run') as run:
                run.return_value.returncode = 1
                self.assertEqual(sync.main(), 1)
                command = run.call_args.args[0]
                self.assertEqual(command[command.index('--window-hours')+1], '48')
                self.assertIn('--refresh-reference', command)
                self.assertIn(str(Path('data/raw/sync_123_2')), command)
                self.assertEqual(os.environ['PGSSLMODE'], 'require')
                report = json.loads((root / 'data/raw/sync_123_2/sync_report.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['issues'], ['collector_failed'])

    def test_missing_secrets_fail_before_any_network_call(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            with patch.object(sync, 'ROOT', Path(directory)), patch.object(sync, 'load_dotenv'), \
                 patch.dict(os.environ, {}, clear=True), patch.object(sync.subprocess, 'run') as run:
                self.assertEqual(sync.main(), 1)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
