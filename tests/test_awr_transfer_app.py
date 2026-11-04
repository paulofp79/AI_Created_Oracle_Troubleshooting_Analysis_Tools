"""Streamlit workflow tests with a synthetic repository, never a live database."""
import copy
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from test_awr_transfer import DBID, SCOPE, START, fixture
from awr_transfer_data import Repository

try:
    import oracledb
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): return False


@unittest.skipIf(AppTest is None, 'Install requirements.txt to run Streamlit workflow tests')
class AppWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.calls = []
        self.inventory = [dict(dbid=DBID, first_time=START - timedelta(days=10), last_time=self.f['end']),
                          dict(dbid=67890, first_time=START, last_time=self.f['end'])]
        self.patches = []
        methods = {
            'dbids': lambda: self.inventory,
            'snapshot_contexts': lambda dbid: [0, 3],
            'snapshots': lambda *args: copy.deepcopy(self.f['snapshots']),
            'scopes': lambda *args: [SCOPE, {'con_dbid': 45678, 'con_id': 3}],
            'scoped_rows': lambda kind, *args: copy.deepcopy(self.f[kind]),
            'instances': lambda dbid: copy.deepcopy(self.f['instances']),
        }
        for name, function in methods.items():
            def recorded(repo, *args, _name=name, _function=function):
                self.calls.append((_name, args))
                return _function(*args)
            self.patches.append(patch.object(Repository, name, recorded))
        self.patches.append(patch.object(oracledb, 'connect', return_value=Connection()))
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'python/AWR_RAC_Transfer_Explorer.py'), default_timeout=15)

    def button(self, label):
        return next(widget for widget in self.app.button if widget.label == label)

    def select(self, label):
        return next(widget for widget in self.app.selectbox if widget.label == label)

    def login(self):
        self.app.run()
        for key, value in {'host': 'synthetic-host', 'service': 'synthetic-service', 'username': 'synthetic-user', 'password': 'SESSION-ONLY-SECRET'}.items():
            self.app.text_input(key='rac_transfer_' + key).set_value(value)
        self.button('Connect / refresh DBIDs').click().run()
        self.assertFalse(self.app.exception)

    def generate(self):
        self.button('Load snapshots and available scopes').click().run()
        self.assertFalse(self.app.exception)
        self.button('Generate / refresh dashboard').click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.error)

    def test_connect_defaults_generate_refresh_reconcile_disconnect(self):
        self.login()
        expected = self.f['end'] - timedelta(days=7)
        self.assertEqual(self.app.session_state['rac_transfer_start_date'], expected.date())
        self.assertEqual(self.app.session_state['rac_transfer_start_time'], expected.time())
        self.generate()
        dataset = self.app.session_state['rac_transfer_dataset']
        self.assertEqual(dataset['dbid'], DBID)
        self.assertIsNone(dataset['timeUnit'])
        self.assertNotIn('SESSION-ONLY-SECRET', self.app.session_state['rac_transfer_html'])
        self.assertNotIn(b'SESSION-ONLY-SECRET', self.app.session_state['rac_transfer_zip'])
        queries = len(self.calls)
        self.select('Snapshot to validate').select_index(1).run()
        self.assertEqual(len(self.calls), queries, 'Widget navigation must not fetch Oracle data')
        route = next(row for row in dataset['frames'][2]['routes'] if row['valid'])
        result = dict(cr=route['cr'], current_blocks=route['current'], congested=route['congested'],
                      seconds=route['seconds'], total_rate=route['totalRate'], time_us=None, avg_us=None)
        with patch.object(Repository, 'reconcile', return_value=[result]) as reconcile:
            self.button('Run independent SQL comparison').click().run()
            self.assertEqual(reconcile.call_count, 1)
            self.assertFalse(self.app.exception)
            self.assertTrue(self.app.session_state['rac_transfer_dataset']['reconciliations'][0]['passed'])
        self.button('Generate / refresh dashboard').click().run()
        self.assertGreater(len(self.calls), queries)
        self.assertNotIn('reconciliations', self.app.session_state['rac_transfer_dataset'])
        self.button('Disconnect / clear session').click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn('rac_transfer_config', self.app.session_state)
        self.assertNotIn('rac_transfer_dataset', self.app.session_state)
        self.assertEqual(self.app.text_input(key='rac_transfer_password').value, '')

    def test_scope_change_resets_results_and_timing_evidence(self):
        self.login()
        self.generate()
        self.app.selectbox(key='rac_transfer_timing_unit').select('microseconds')
        self.app.text_input(key='rac_transfer_timing_evidence').set_value('Synthetic verification')
        self.app.checkbox(key='rac_transfer_timing_confirmed').check().run()
        self.button('Generate / refresh dashboard').click().run()
        self.assertEqual(self.app.session_state['rac_transfer_dataset']['timeUnit'], 'microseconds')
        queries = len(self.calls)
        self.select('Transfer and cache scope').select_index(1).run()
        self.assertFalse(self.app.exception)
        self.assertNotIn('rac_transfer_dataset', self.app.session_state)
        self.assertIsNone(self.app.selectbox(key='rac_transfer_timing_unit').value)
        self.assertEqual(self.app.text_input(key='rac_transfer_timing_evidence').value, '')
        self.assertEqual(len(self.calls), queries)
        self.select('Customer DBID').select(67890).run()
        self.assertNotIn('rac_transfer_loaded', self.app.session_state)
        self.assertNotIn('rac_transfer_dataset', self.app.session_state)
        self.assertEqual(len(self.calls), queries)

    def test_optional_cache_failure_does_not_block_transfers(self):
        self.login()
        self.button('Load snapshots and available scopes').click().run()
        def optional_failure(repo, kind, *args):
            if kind != 'transfers':
                raise RuntimeError('Synthetic optional view unavailable')
            return copy.deepcopy(self.f[kind])
        with patch.object(Repository, 'scoped_rows', optional_failure):
            self.button('Generate / refresh dashboard').click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.error)
        data = self.app.session_state['rac_transfer_dataset']
        self.assertTrue(any(route['valid'] for frame in data['frames'] for route in frame['routes']))
        self.assertTrue(all(node['cacheGiB'] is None for frame in data['frames'] for node in frame['nodes']))
        self.assertTrue(any('sga unavailable' in warning for warning in data['warnings']))

    def test_reconnect_failure_clears_previous_connection_results(self):
        self.login()
        self.generate()
        self.app.text_input(key='rac_transfer_host').set_value('different-repository')
        with patch.object(oracledb, 'connect', side_effect=RuntimeError('Synthetic connection failed')):
            self.button('Connect / refresh DBIDs').click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn('rac_transfer_config', self.app.session_state)
        self.assertNotIn('rac_transfer_dataset', self.app.session_state)
        self.assertNotIn('rac_transfer_loaded', self.app.session_state)
        self.assertTrue(self.app.error)


if __name__ == '__main__':
    unittest.main()
