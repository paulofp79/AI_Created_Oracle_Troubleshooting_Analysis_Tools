"""Calculation invariants and offline-report checks; no Oracle connection needed."""
import copy
import io
import json
from pathlib import Path
import re
import sys
import unittest
import zipfile
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from awr_transfer_data import (COUNTS, TIMES, STATS, DataUnavailable, Repository,
                               build_dataset, counter_bounds, dataset_csvs, query_ledger_sql)
from awr_transfer_report import render_report, report_bundle

DBID = 12345
SCOPE = {'con_dbid': DBID, 'con_id': 0}
START = datetime(2026, 11, 3, 10)


def fixture(instances=3, frame_count=4):
    snaps, transfers, sga, stats, identities = [], [], [], [], []
    for inst in range(1, instances + 1):
        identities.append({'instance_number': inst, 'startup_time': START - timedelta(days=1),
                           'instance_name': 'DEMO{}'.format(inst), 'host_name': 'synthetic-host-{}'.format(inst), 'db_name': 'SYNTHETIC'})
    for i in range(frame_count + 1):
        for inst in range(1, instances + 1):
            base = {'dbid': DBID, 'snap_id': 100 + i, 'instance_number': inst, **SCOPE}
            snaps.append({**base, 'startup_time': START - timedelta(days=1), 'begin_interval_time': START + timedelta(minutes=i-1),
                          'end_interval_time': START + timedelta(minutes=i), 'snap_timezone': timedelta(hours=-3)})
            sga.append({**base, 'name': 'Database Buffers', 'value': (32 + inst) * 1073741824})
            for name, increment in zip(STATS, [6000, 12000, 1800]):
                stats.append({**base, 'stat_name': name, 'value': 1_000_000 + i * increment})
            for src in range(1, instances + 1):
                if src == inst:
                    continue
                for block_class in ['data block', 'undo block']:
                    scale = src * inst * (1 if block_class == 'data block' else 2)
                    row = {**base, 'instance': src, 'class': block_class}
                    row.update({name: 10_000 + i * amount * scale for name, amount in zip(COUNTS, [100, 10, 2, 200, 20, 4])})
                    row.update({name: 1_000_000 + i * amount * scale for name, amount in zip(TIMES, [20000, 40000, 5000, 1000, 10000, 2000])})
                    transfers.append(row)
    return dict(dbid=DBID, scope=SCOPE, snapshots=snaps, transfers=transfers, sga=sga, sysstat=stats, instances=identities,
                start=START, end=START + timedelta(minutes=frame_count), time_unit='microseconds', time_evidence='Synthetic fixture; not a live Oracle verification')


class TransferTests(unittest.TestCase):
    def test_direction_counts_elapsed_and_total_time(self):
        data = build_dataset(**fixture())
        route = next(row for row in data['frames'][0]['routes'] if row['src'] == 2 and row['dst'] == 1 and row['class'] == 'data block')
        self.assertTrue(route['valid'])
        self.assertEqual(route['cr'], 224)
        self.assertEqual(route['current'], 448)
        self.assertEqual(route['seconds'], 60)
        self.assertEqual(route['totalRate'], 672 / 60)
        self.assertEqual(route['timeUs'], 120000)  # Do not add busy/congested times twice.
        self.assertAlmostEqual(route['avgUs'], 120000 / 672)

    def test_destination_specific_elapsed(self):
        f = fixture(frame_count=1)
        for row in f['snapshots']:
            if row['instance_number'] == 2 and row['snap_id'] == 101:
                row['end_interval_time'] += timedelta(seconds=15)
        data = build_dataset(**f)
        route = next(row for row in data['frames'][0]['routes'] if row['dst'] == 2)
        self.assertEqual(route['seconds'], 75)
        self.assertEqual(route['totalRate'], (route['cr'] + route['current']) / 75)
        self.assertTrue(data['frames'][0]['boundary'])

    def test_restart_and_changing_host_identity(self):
        f = fixture()
        startup = START + timedelta(minutes=1)
        for row in f['snapshots']:
            if row['instance_number'] == 2 and row['snap_id'] >= 102:
                row['startup_time'] = startup
        f['instances'].append({'instance_number': 2, 'startup_time': startup, 'instance_name': 'NEW2', 'host_name': 'new-host'})
        frame = build_dataset(**f)['frames'][1]
        self.assertEqual(next(node for node in frame['nodes'] if node['id'] == 2)['host'], 'new-host')
        self.assertTrue(all(not row['valid'] for row in frame['routes'] if row['src'] == 2 or row['dst'] == 2))
        self.assertTrue(any(row['valid'] for row in frame['routes']))

    def test_missing_snapshot_and_time_gap(self):
        for mode in ('id', 'time'):
            f = fixture()
            if mode == 'id':
                f['snapshots'] = [row for row in f['snapshots'] if row['snap_id'] != 101]
            else:
                for row in f['snapshots']:
                    if row['snap_id'] == 102:
                        row['begin_interval_time'] += timedelta(seconds=5)
            frame = next(row for row in build_dataset(**f)['frames'] if row['snap'] == 102)
            self.assertTrue(all(not row['valid'] for row in frame['routes']))
            self.assertTrue(all(row['totalRate'] is None for row in frame['routes']))

    def test_counter_reset_missing_prior_and_measured_zero(self):
        for mode in ('reset', 'missing', 'zero'):
            f = fixture(frame_count=1)
            target = lambda row: row['instance_number'] == 1 and row['instance'] == 2 and row['class'] == 'data block'
            prior = next(row for row in f['transfers'] if row['snap_id'] == 100 and target(row))
            end = next(row for row in f['transfers'] if row['snap_id'] == 101 and target(row))
            if mode == 'missing':
                f['transfers'].remove(prior)
            elif mode == 'reset':
                end['cr_block'] = 0
            else:
                for key in COUNTS + TIMES:
                    end[key] = prior[key]
            row = next(row for row in build_dataset(**f)['frames'][0]['routes'] if row['src'] == 2 and row['dst'] == 1 and row['class'] == 'data block')
            self.assertEqual(row['valid'], mode == 'zero')
            self.assertEqual(row['totalRate'], 0 if mode == 'zero' else None)
            self.assertIsNone(row['avgUs'])

    def test_container_isolation_and_duplicate_rejection(self):
        f = fixture()
        other = [{**row, 'con_id': 3, 'con_dbid': 99999, 'cr_block': 10**12} for row in f['transfers']]
        expected = build_dataset(**f)
        f['transfers'].extend(other)
        self.assertEqual(build_dataset(**f)['frames'], expected['frames'])
        f['transfers'].append(copy.deepcopy(f['transfers'][0]))
        with self.assertRaisesRegex(DataUnavailable, 'Duplicate'):
            build_dataset(**f)

    def test_optional_cache_absence_and_negative_estimate(self):
        f = fixture()
        f['sga'] = []
        f['sysstat'] = []
        data = build_dataset(**f)
        self.assertTrue(all(node['cacheGiB'] is None and node['activity']['hitPct'] is None for node in data['frames'][0]['nodes']))
        self.assertTrue(data['frames'][0]['routes'][0]['valid'])
        f = fixture(frame_count=1)
        for row in f['sysstat']:
            if row['snap_id'] == 101 and row['stat_name'] == STATS[2]:
                row['value'] += 1_000_000
        node = build_dataset(**f)['frames'][0]['nodes'][0]
        self.assertIsNone(node['activity']['hits'])
        self.assertIsNone(node['activity']['hitPct'])
        self.assertGreater(node['activity']['missRate'], 0)

    def test_cache_math_and_verified_units(self):
        f = fixture()
        node = build_dataset(**f)['frames'][0]['nodes'][0]
        self.assertEqual(node['cacheGiB'], 33)
        self.assertEqual(node['activity']['hitPct'], 90)
        self.assertEqual(node['activity']['hitRate'], 270)
        f['time_unit'] = None
        self.assertIsNone(build_dataset(**f)['frames'][0]['routes'][0]['avgUs'])
        f['time_unit'] = 'centiseconds'
        self.assertEqual(build_dataset(**f)['frames'][0]['routes'][0]['timeUs'], 120000 * 10000)
        f['time_evidence'] = ''
        with self.assertRaisesRegex(DataUnavailable, 'verified unit'):
            build_dataset(**f)

    def test_offline_export_script_safety_and_evidence(self):
        data = build_dataset(**fixture())
        data['warnings'] = ['</script><script>alert(1)</script>']
        ledger = [{'label': 'Reproducible query', 'sql': 'SELECT snap_id FROM DBA_HIST_SNAPSHOT WHERE dbid = :dbid AND end_interval_time > :from_ts', 'binds': {'dbid': DBID, 'from_ts': START}}]
        html = render_report(data, ledger)
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertNotIn('script src=', html)
        self.assertIn('6.1.0', html)
        self.assertIn('TO_TIMESTAMP', query_ledger_sql(ledger))
        with zipfile.ZipFile(io.BytesIO(report_bundle(data, ledger))) as archive:
            self.assertEqual(set(archive.namelist()), {'awr-rac-transfer.html', 'queries.sql', 'metadata.json', 'transfers.csv', 'cache.csv'})
            self.assertIn('scope', json.loads(archive.read('metadata.json')))
        self.assertIn('mean', html.lower())

    def test_dbid_mismatch_rejected(self):
        f = fixture()
        f['transfers'][0]['dbid'] = 9
        with self.assertRaisesRegex(DataUnavailable, 'DBID'):
            build_dataset(**f)

    def test_membership_and_requested_window_coverage(self):
        f = fixture()
        f['snapshots'] = [row for row in f['snapshots'] if not (row['instance_number'] == 3 and row['snap_id'] == 102)]
        f['start'] -= timedelta(hours=1)
        f['end'] += timedelta(hours=1)
        data = build_dataset(**f)
        frame = next(frame for frame in data['frames'] if frame['snap'] == 102)
        self.assertEqual(frame['missingInstances'], [3])
        self.assertTrue(all(not route['valid'] for route in frame['routes'] if 3 in (route['src'], route['dst'])))
        self.assertTrue(any('unknown' in issue['reason'] for issue in data['issues']))
        self.assertEqual(len(next(c for c in data['coverage'] if c['instance'] == 3)['gaps']), 3)
        self.assertTrue(any('complete requested window' in warning for warning in data['warnings']))

    def test_receiver_interval_export_and_timezone_inconsistency(self):
        f = fixture(frame_count=1)
        for row in f['snapshots']:
            if row['instance_number'] == 2 and row['snap_id'] == 101:
                row['end_interval_time'] += timedelta(seconds=15)
                row['begin_interval_time'] += timedelta(milliseconds=500)
        data = build_dataset(**f)
        route = next(row for row in data['frames'][0]['routes'] if row['dst'] == 2)
        self.assertEqual(route['begin'], '2026-11-03T10:00:00-03:00')
        self.assertEqual(route['end'], '2026-11-03T10:01:15-03:00')
        self.assertIn('2026-11-03T10:01:15-03:00', dataset_csvs(data)['transfers.csv'])
        for row in f['snapshots']:
            if row['snap_id'] == 100:
                row['snap_timezone'] = None
        frame = build_dataset(**f)['frames'][0]
        self.assertTrue(all(not route['valid'] for route in frame['routes']))
        self.assertTrue(all('timezone' in node['reason'] for node in frame['nodes']))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []
        self.rows = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, binds):
        self.connection.calls.append((sql, binds))
        view = re.search(r'FROM\s+(DBA_HIST_[A-Z_]+)', sql, re.I)[1].upper()
        self.description = [(name.upper(),) for name in self.connection.columns[view]]
        if '1 = 0' not in sql:
            self.rows = []
    def fetchmany(self, count):
        rows, self.rows = self.rows[:count], self.rows[count:]
        return rows


class FakeConnection:
    def __init__(self):
        self.calls = []
        f = fixture()
        self.columns = {view: tuple(f[key][0]) for key, view in [('snapshots','DBA_HIST_SNAPSHOT'), ('transfers','DBA_HIST_INST_CACHE_TRANSFER'), ('sga','DBA_HIST_SGA'), ('sysstat','DBA_HIST_SYSSTAT')]}
    def cursor(self): return FakeCursor(self)


class QueryTests(unittest.TestCase):
    def test_old_instance_baseline_cannot_expand_counter_scan(self):
        f = fixture()
        stale = dict(f['snapshots'][0], snap_id=1,
                     begin_interval_time=START-timedelta(days=730), end_interval_time=START-timedelta(days=729))
        self.assertEqual(counter_bounds(f['snapshots']+[stale], f['start'], f['end']), (100,104))
        repo = Repository(FakeConnection())
        repo.snapshots(DBID, START, f['end'], 0)
        self.assertIn('AND EXISTS', repo.ledger[-1]['sql'])
        self.assertIn('a.instance_number = sn.instance_number', repo.ledger[-1]['sql'])

    def test_allowed_views_only_binds_and_scope(self):
        connection = FakeConnection()
        repo = Repository(connection)
        repo.dbids()
        repo.snapshots(DBID, START, START + timedelta(days=1), 0)
        repo.scopes(DBID, 100, 104)
        for kind in ('transfers', 'sga', 'sysstat'):
            repo.scoped_rows(kind, DBID, 100, 104, SCOPE)
        dataset = build_dataset(**fixture())
        repo.reconcile(dataset, dataset['frames'][0], dataset['frames'][0]['routes'][0], 0)
        for sql, binds in connection.calls:
            self.assertNotRegex(sql.upper(), r'WRH\$|ALL_TAB|DBA_VIEWS|V\$|INSERT|UPDATE|DELETE')
            if 'GROUP BY dbid' not in sql:
                self.assertEqual(binds['dbid'], DBID)
            if 'bounded' in sql:
                self.assertIn(':low_snap', sql)
        transfer_sql = next(sql for sql, binds in connection.calls if 'NVL(con_dbid' in sql)
        self.assertIn('NVL(con_id, -1) = :con_id', transfer_sql)


if __name__ == '__main__':
    unittest.main()
