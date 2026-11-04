"""Read-only AWR transfer queries and coverage-aware calculations.

No Streamlit state or Oracle driver dependency: the repository accepts a DB-API
connection, and the calculation functions accept ordinary row dictionaries.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

VIEWS = {
    'snapshots': 'DBA_HIST_SNAPSHOT',
    'instances': 'DBA_HIST_DATABASE_INSTANCE',
    'transfers': 'DBA_HIST_INST_CACHE_TRANSFER',
    'sga': 'DBA_HIST_SGA',
    'sysstat': 'DBA_HIST_SYSSTAT',
}
COUNTS = ('cr_block', 'cr_busy', 'cr_congested', 'current_block', 'current_busy', 'current_congested')
TIMES = ('cr_block_time', 'current_block_time', 'cr_busy_time', 'cr_congested_time', 'current_busy_time', 'current_congested_time')
STATS = ('db block gets from cache', 'consistent gets from cache', 'physical reads cache')
UNIT_FACTORS = {'microseconds': 1, 'milliseconds': 1000, 'centiseconds': 10000}
ROW_LIMIT = 1_000_000


class DataUnavailable(ValueError):
    pass


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value).__name__)


def sql_literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, datetime):
        return "TO_TIMESTAMP('{}', 'YYYY-MM-DD HH24:MI:SS.FF6')".format(value.strftime('%Y-%m-%d %H:%M:%S.%f'))
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return "'{}'".format(str(value).replace("'", "''"))


def query_ledger_sql(ledger):
    return '\n\n'.join('-- ' + entry['label'] + '\n' + re.sub(
        r':([a-zA-Z_][a-zA-Z_0-9]*)', lambda m: sql_literal(entry['binds'][m[1]]), entry['sql']) + ';'
        for entry in ledger)


class Repository:
    def __init__(self, connection):
        self.connection = connection
        self.schemas = {}
        self.ledger = []

    def query(self, label, sql, binds=None):
        binds = binds or {}
        self.ledger.append({'label': label, 'sql': sql.strip(), 'binds': dict(binds)})
        with self.connection.cursor() as cursor:
            cursor.arraysize = 2000
            cursor.execute(sql, binds)
            names = [column[0].lower() for column in cursor.description]
            rows = []
            while True:
                batch = cursor.fetchmany(2000)
                if not batch:
                    break
                rows.extend(dict(zip(names, values)) for values in batch)
                if len(rows) > ROW_LIMIT:
                    raise DataUnavailable('Query exceeded 1,000,000 rows. Select a shorter analysis window; no rows were silently truncated.')
            return rows

    def columns(self, kind, dbid):
        if kind not in self.schemas:
            sql = 'SELECT * FROM {} WHERE dbid = :dbid AND 1 = 0'.format(VIEWS[kind])
            self.ledger.append({'label': 'Column inspection: ' + VIEWS[kind], 'sql': sql, 'binds': {'dbid': dbid}})
            with self.connection.cursor() as cursor:
                cursor.execute(sql, {'dbid': dbid})
                self.schemas[kind] = {column[0].lower() for column in cursor.description}
        return self.schemas[kind]

    def require(self, kind, dbid, columns):
        missing = set(columns) - self.columns(kind, dbid)
        if missing:
            raise DataUnavailable('{} is missing required columns: {}'.format(VIEWS[kind], ', '.join(sorted(missing))))

    def dbids(self):
        # Inventory is the one intentional unfiltered query: the user has not
        # selected a DBID yet. Every subsequent query binds that selection.
        return self.query('Available imported DBIDs', '''SELECT dbid,
            MIN(begin_interval_time) first_time, MAX(end_interval_time) last_time
            FROM DBA_HIST_SNAPSHOT GROUP BY dbid ORDER BY dbid''')

    def snapshot_contexts(self, dbid):
        if 'con_id' not in self.columns('snapshots', dbid):
            return [None]
        return [row['con_id'] for row in self.query('Snapshot container contexts',
            'SELECT DISTINCT con_id FROM DBA_HIST_SNAPSHOT WHERE dbid = :dbid ORDER BY con_id', {'dbid': dbid})]

    def snapshots(self, dbid, start, end, context):
        required = ('dbid', 'snap_id', 'instance_number', 'startup_time', 'begin_interval_time', 'end_interval_time')
        self.require('snapshots', dbid, required)
        columns = self.columns('snapshots', dbid)
        timezone_col = 'sn.snap_timezone' if 'snap_timezone' in columns else 'NULL'
        context_sql = 'AND NVL(sn.con_id, -1) = :snapshot_context' if 'con_id' in columns else ''
        prior_context = 'AND NVL(p.con_id, -1) = :snapshot_context' if 'con_id' in columns else ''
        active_context = 'AND NVL(a.con_id, -1) = :snapshot_context' if 'con_id' in columns else ''
        binds = {'dbid': dbid, 'from_ts': start, 'to_ts': end}
        if 'con_id' in columns:
            binds['snapshot_context'] = -1 if context is None else context
        return self.query('Selected snapshots plus one predecessor per instance', '''
            SELECT sn.dbid, sn.snap_id, sn.instance_number, sn.startup_time,
                   sn.begin_interval_time, sn.end_interval_time, {tz} snap_timezone
            FROM DBA_HIST_SNAPSHOT sn
            WHERE sn.dbid = :dbid {context}
              AND ((sn.end_interval_time > :from_ts AND sn.begin_interval_time < :to_ts)
                   OR (sn.snap_id = (SELECT MAX(p.snap_id) FROM DBA_HIST_SNAPSHOT p
                       WHERE p.dbid = :dbid AND p.instance_number = sn.instance_number
                         {prior_context} AND p.end_interval_time <= :from_ts)
                       AND EXISTS (SELECT 1 FROM DBA_HIST_SNAPSHOT a
                         WHERE a.dbid = :dbid AND a.instance_number = sn.instance_number {active_context}
                           AND a.end_interval_time > :from_ts AND a.begin_interval_time < :to_ts)))
            ORDER BY sn.instance_number, sn.snap_id'''.format(tz=timezone_col, context=context_sql, prior_context=prior_context, active_context=active_context), binds)

    def scopes(self, dbid, low, high):
        columns = self.columns('transfers', dbid)
        fields = ', '.join(name if name in columns else 'NULL ' + name for name in ('con_dbid', 'con_id'))
        return self.query('Transfer container scopes', '''SELECT DISTINCT {} FROM DBA_HIST_INST_CACHE_TRANSFER
            WHERE dbid = :dbid AND snap_id BETWEEN :low_snap AND :high_snap'''.format(fields),
            {'dbid': dbid, 'low_snap': low, 'high_snap': high})

    def scoped_rows(self, kind, dbid, low, high, scope):
        columns = self.columns(kind, dbid)
        required = {
            'transfers': ('snap_id', 'instance_number', 'instance', 'class') + COUNTS,
            'sga': ('snap_id', 'instance_number', 'name', 'value'),
            'sysstat': ('snap_id', 'instance_number', 'stat_name', 'value'),
        }[kind]
        self.require(kind, dbid, required)
        selected = list(required) + ['dbid']
        if kind == 'transfers':
            selected.extend(name if name in columns else 'NULL ' + name for name in TIMES)
        predicates = ['dbid = :dbid', 'snap_id BETWEEN :low_snap AND :high_snap']
        binds = {'dbid': dbid, 'low_snap': low, 'high_snap': high}
        for field in ('con_dbid', 'con_id'):
            if field in columns:
                selected.append(field)
                predicates.append('NVL({}, -1) = :{}'.format(field, field))
                binds[field] = -1 if scope[field] is None else scope[field]
            else:
                if scope[field] is not None:
                    raise DataUnavailable('{} cannot establish the selected {} scope.'.format(VIEWS[kind], field))
                selected.append('NULL ' + field)
        if kind == 'sga':
            predicates.append("name = 'Database Buffers'")
        if kind == 'sysstat':
            predicates.append('stat_name IN ({})'.format(', '.join(sql_literal(name) for name in STATS)))
        return self.query(VIEWS[kind] + ' bounded counters', 'SELECT {} FROM {} WHERE {}'.format(
            ', '.join(selected), VIEWS[kind], ' AND '.join(predicates)), binds)

    def instances(self, dbid):
        self.require('instances', dbid, ('instance_number', 'startup_time', 'instance_name', 'host_name', 'db_name'))
        return self.query('Historical instance identity', '''SELECT DISTINCT instance_number, startup_time,
            instance_name, host_name, db_name FROM DBA_HIST_DATABASE_INSTANCE WHERE dbid = :dbid''', {'dbid': dbid})

    def reconcile(self, dataset, frame, route, snapshot_context):
        """Independent SQL-side arithmetic for one valid class/route/interval."""
        dbid = dataset['dbid']
        columns = self.columns('transfers', dbid)
        snap_columns = self.columns('snapshots', dbid)
        binds = {'dbid': dbid, 'end_snap': frame['snap'], 'begin_snap': frame['snap'] - 1,
                 'src': route['src'], 'dst': route['dst'], 'block_class': route['class']}
        filters = []
        for field in ('con_dbid', 'con_id'):
            if field in columns:
                binds[field] = dataset['scope'][field] if dataset['scope'][field] is not None else -1
                filters.extend('NVL({}.{}, -1) = :{}'.format(alias, field, field) for alias in ('b', 'e'))
        if 'con_id' in snap_columns:
            binds['snapshot_context'] = snapshot_context if snapshot_context is not None else -1
            filters.extend('NVL({}.con_id, -1) = :snapshot_context'.format(alias) for alias in ('bs', 'es'))
        diff = 'es.end_interval_time - bs.end_interval_time'
        if 'snap_timezone' in snap_columns:
            diff = "(es.end_interval_time - NVL(es.snap_timezone, NUMTODSINTERVAL(0, 'SECOND'))) - (bs.end_interval_time - NVL(bs.snap_timezone, NUMTODSINTERVAL(0, 'SECOND')))"
        elapsed = ' + '.join('EXTRACT({} FROM ({})) * {}'.format(part, diff, scale)
                             for part, scale in [('DAY', 86400), ('HOUR', 3600), ('MINUTE', 60), ('SECOND', 1)])
        cr = ' + '.join('e.{0} - b.{0}'.format(name) for name in COUNTS[:3])
        current = ' + '.join('e.{0} - b.{0}'.format(name) for name in COUNTS[3:])
        timing = 'NULL'
        if dataset['timeUnit'] and {'cr_block_time', 'current_block_time'} <= columns:
            timing = '(e.cr_block_time - b.cr_block_time + e.current_block_time - b.current_block_time) * {}'.format(UNIT_FACTORS[dataset['timeUnit']])
        sql = '''WITH measured AS (
          SELECT ({cr}) cr, ({current}) current_blocks,
                 (e.cr_congested - b.cr_congested + e.current_congested - b.current_congested) congested,
                 ({elapsed}) seconds, {timing} time_us
          FROM DBA_HIST_INST_CACHE_TRANSFER b
          JOIN DBA_HIST_INST_CACHE_TRANSFER e ON e.dbid = b.dbid
            AND e.instance_number = b.instance_number AND e.instance = b.instance AND e.class = b.class
          JOIN DBA_HIST_SNAPSHOT bs ON bs.dbid = b.dbid AND bs.snap_id = b.snap_id AND bs.instance_number = b.instance_number
          JOIN DBA_HIST_SNAPSHOT es ON es.dbid = e.dbid AND es.snap_id = e.snap_id AND es.instance_number = e.instance_number
          WHERE b.dbid = :dbid AND e.dbid = :dbid AND b.snap_id = :begin_snap AND e.snap_id = :end_snap
            AND e.instance_number = :dst AND e.instance = :src AND e.class = :block_class
            AND bs.startup_time = es.startup_time {filters}
        ) SELECT cr, current_blocks, congested, seconds, time_us,
            (cr + current_blocks) / NULLIF(seconds, 0) total_rate,
            time_us / NULLIF(cr + current_blocks, 0) avg_us FROM measured'''.format(
                cr=cr, current=current, elapsed=elapsed, timing=timing,
                filters=(' AND ' + ' AND '.join(filters)) if filters else '')
        return self.query('Independent SQL reconciliation for snapshot {} route {} to {}'.format(frame['snap'], route['src'], route['dst']), sql, binds)


def wall(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=None)


def timestamp(value, offset=None):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None and offset is not None:
        value = value.replace(tzinfo=timezone(offset))
    return value


def counter_bounds(snapshots, start, end):
    """Bound counters to displayed intervals and their immediate predecessor.

    An instance returning after a long gap may have very old predecessor
    metadata. Its counters cannot form a valid delta and must not expand the
    counter scan to years of unrelated history.
    """
    selected = [row['snap_id'] for row in snapshots
                if wall(row['end_interval_time']) > start and wall(row['begin_interval_time']) < end]
    if not selected:
        raise DataUnavailable('No snapshots overlap the selected source-clock window.')
    return min(selected) - 1, max(selected)


def scoped_index(rows, scope, keys):
    result = {}
    for row in rows:
        if any(row.get(field) != scope.get(field) for field in ('con_dbid', 'con_id')):
            continue
        key = tuple(row[field] for field in keys)
        if key in result:
            raise DataUnavailable('Duplicate rows for {}. Scope or import overlap must be resolved; refusing to double-count.'.format(key))
        result[key] = row
    return result


def delta(current, previous, fields):
    if previous is None:
        return None, 'Missing prior counter'
    if any(current.get(name) is None or previous.get(name) is None for name in fields):
        return None, 'Missing counter value'
    values = {name: current[name] - previous[name] for name in fields}
    if any(value < 0 for value in values.values()):
        return None, 'Counter decreased or reset'
    return values, None


def build_dataset(dbid, scope, snapshots, transfers, sga, sysstat, instances, start, end,
                  time_unit=None, time_evidence='', warnings=None):
    if start >= end:
        raise DataUnavailable('Start time must be earlier than end time.')
    if time_unit and (time_unit not in UNIT_FACTORS or not time_evidence.strip()):
        raise DataUnavailable('Transfer timing requires a verified unit and its verification reference.')
    factor = UNIT_FACTORS.get(time_unit)
    snaps = {}
    by_instance = defaultdict(list)
    for row in snapshots:
        if int(row['dbid']) != int(dbid):
            raise DataUnavailable('Snapshot DBID does not match the selected database.')
        key = (row['snap_id'], row['instance_number'])
        if key in snaps:
            raise DataUnavailable('Duplicate snapshot metadata. Select a single snapshot container context.')
        snaps[key] = row
        by_instance[row['instance_number']].append(row)
    predecessors = {}
    for rows in by_instance.values():
        previous = None
        for row in sorted(rows, key=lambda item: item['snap_id']):
            predecessors[(row['snap_id'], row['instance_number'])] = previous
            previous = row

    for rows in (transfers, sga, sysstat):
        if any(int(row['dbid']) != int(dbid) for row in rows):
            raise DataUnavailable('Counter DBID does not match the selected database.')
    tr = scoped_index(transfers, scope, ('snap_id', 'instance_number', 'instance', 'class'))
    sg = scoped_index(sga, scope, ('snap_id', 'instance_number'))
    st = scoped_index(sysstat, scope, ('snap_id', 'instance_number', 'stat_name'))
    identities = {(row['instance_number'], wall(row['startup_time'])): row for row in instances}
    by_snapshot = defaultdict(list)
    for row in snapshots:
        if wall(row['end_interval_time']) > start and wall(row['begin_interval_time']) < end:
            by_snapshot[row['snap_id']].append(row)
    transfer_groups = defaultdict(list)
    for key, row in tr.items():
        transfer_groups[key[0]].append(row)
    classes = sorted({row['class'] for row in tr.values()})
    frames = []
    issues = []
    selected_instances = {row['instance_number'] for rows in by_snapshot.values() for row in rows}
    for snap_id, rows in sorted(by_snapshot.items()):
        nodes = []
        valid_intervals = {}
        for row in sorted(rows, key=lambda item: item['instance_number']):
            inst = row['instance_number']
            prev = predecessors[(snap_id, inst)]
            begin = timestamp(row['begin_interval_time'], row.get('snap_timezone'))
            finish = timestamp(row['end_interval_time'], row.get('snap_timezone'))
            reason = None
            seconds = None
            if not prev:
                reason = 'Missing preceding snapshot'
            elif wall(prev['startup_time']) != wall(row['startup_time']):
                reason = 'Instance restarted'
            elif snap_id != prev['snap_id'] + 1:
                reason = 'Missing snapshot IDs'
            else:
                prior_end = timestamp(prev['end_interval_time'], prev.get('snap_timezone'))
                if (begin.tzinfo is None) != (prior_end.tzinfo is None):
                    reason = 'Inconsistent snapshot timezone metadata'
                elif abs((begin - prior_end).total_seconds()) > 1:
                    reason = 'Gap between snapshot intervals'
                else:
                    seconds = (finish - prior_end).total_seconds()
                    if seconds <= 0:
                        reason = 'Non-positive elapsed time'
                    else:
                        begin = prior_end  # The exact counter baseline used by the denominator.
            valid_intervals[inst] = (prev, seconds, reason)
            identity = identities.get((inst, wall(row['startup_time'])), {})
            cache = sg.get((snap_id, inst), {}).get('value')
            if cache is not None and cache < 0:
                cache = None
            activity = {'logical': None, 'physical': None, 'hits': None, 'hitPct': None,
                        'logicalRate': None, 'missRate': None, 'hitRate': None, 'reason': reason}
            if not reason:
                current_stats = {name: st.get((snap_id, inst, name), {}).get('value') for name in STATS}
                previous_stats = {name: st.get((prev['snap_id'], inst, name), {}).get('value') for name in STATS}
                values, problem = delta(current_stats, previous_stats, STATS)
                activity['reason'] = problem
                if values is not None:
                    logical = values[STATS[0]] + values[STATS[1]]
                    physical = values[STATS[2]]
                    hits = logical - physical
                    activity.update(logical=logical, physical=physical, logicalRate=float(logical / seconds), missRate=float(physical / seconds))
                    if hits >= 0:
                        activity.update(hits=hits, hitRate=float(hits / seconds), hitPct=float(hits * 100 / logical) if logical else None)
                    else:
                        activity['reason'] = 'Physical cache reads exceed logical cache reads; hit estimate unavailable'
            nodes.append({'id': inst, 'name': identity.get('instance_name') or 'Instance {}'.format(inst),
                'host': identity.get('host_name') or 'Unknown host', 'startup': row['startup_time'].isoformat(),
                'begin': begin.isoformat(), 'end': finish.isoformat(), 'seconds': seconds,
                'cacheGiB': float(cache / 1073741824) if cache is not None else None, 'activity': activity, 'reason': reason})
            if reason:
                issues.append({'snap': snap_id, 'instance': inst, 'reason': reason})
        node_by_id = {node['id']: node for node in nodes}
        missing_instances = sorted(selected_instances - set(node_by_id))
        issues.extend({'snap': snap_id, 'instance': inst,
                       'reason': 'Snapshot absent; instance membership is unknown'} for inst in missing_instances)
        routes = []
        for row in transfer_groups[snap_id]:
            dst, src = row['instance_number'], row['instance']
            if dst == src:
                continue
            prev, seconds, reason = valid_intervals.get(dst, (None, None, 'Destination snapshot missing'))
            if src not in valid_intervals:
                reason = 'Source snapshot missing'
            elif valid_intervals[src][2]:
                reason = 'Source interval unavailable: ' + valid_intervals[src][2]
            old = tr.get((prev['snap_id'], dst, src, row['class'])) if prev else None
            values, problem = delta(row, old, COUNTS) if not reason else (None, reason)
            receiver = node_by_id.get(dst, {})
            route = {'src': src, 'dst': dst, 'class': row['class'], 'valid': values is not None,
                     'begin': receiver.get('begin'), 'end': receiver.get('end'),
                     'reason': problem, 'seconds': seconds, 'cr': None, 'current': None, 'congested': None,
                     'crRate': None, 'currentRate': None, 'congestedRate': None, 'totalRate': None,
                     'timeUs': None, 'avgUs': None, 'timeReason': 'Transfer time units have not been verified'}
            if values is not None:
                cr = sum(values[name] for name in COUNTS[:3])
                current = sum(values[name] for name in COUNTS[3:])
                congested = values['cr_congested'] + values['current_congested']
                route.update(cr=cr, current=current, congested=congested, crRate=float(cr / seconds),
                    currentRate=float(current / seconds), congestedRate=float(congested / seconds), totalRate=float((cr + current) / seconds))
                if factor:
                    timing, timing_problem = delta(row, old, ('cr_block_time', 'current_block_time'))
                    route['timeReason'] = timing_problem
                    if timing is not None:
                        # Documented BLOCK_TIME columns include the other times;
                        # do not add BUSY_TIME or CONGESTED_TIME again.
                        time_us = sum(timing.values()) * factor
                        route.update(timeUs=time_us, avgUs=float(time_us / (cr + current)) if cr + current else None)
            routes.append(route)
        begins = [wall(node['begin']) for node in nodes]
        ends = [wall(node['end']) for node in nodes]
        frames.append({'snap': snap_id, 'begin': min(begins).isoformat(), 'end': max(ends).isoformat(),
            'boundary': min(begins) < start or max(ends) > end, 'nodes': nodes, 'routes': routes,
            'missingInstances': missing_instances})
    if not frames:
        raise DataUnavailable('No snapshots overlap the selected source-clock window.')
    if not any(frame['routes'] for frame in frames):
        raise DataUnavailable('No instance-to-instance transfer rows in the selected window and scope. Check the imported data and chosen container; no internal-table fallback was used.')
    coverage = []
    for inst in sorted(selected_instances):
        intervals = sorted((wall(row['begin_interval_time']), wall(row['end_interval_time']))
                           for rows in by_snapshot.values() for row in rows if row['instance_number'] == inst)
        cursor = start
        gaps = []
        for begin, finish in intervals:
            if begin > cursor:
                gaps.append([cursor.isoformat(), min(begin, end).isoformat()])
            cursor = max(cursor, finish)
        if cursor < end:
            gaps.append([cursor.isoformat(), end.isoformat()])
        coverage.append({'instance': inst, 'first': intervals[0][0].isoformat(),
                         'last': max(finish for _, finish in intervals).isoformat(), 'gaps': gaps})
    warnings = list(warnings or [])
    incomplete = [str(row['instance']) for row in coverage if row['gaps']]
    if incomplete:
        warnings.append('Snapshot coverage does not span the complete requested window for instance(s) {}. Gaps do not prove an instance was down. See source and coverage details.'.format(', '.join(incomplete)))
    return {'schemaVersion': 1, 'dbid': dbid, 'scope': scope, 'requested': [start.isoformat(), end.isoformat()],
        'generatedUtc': datetime.now(timezone.utc).isoformat(), 'classes': classes, 'frames': frames,
        'issues': issues, 'warnings': warnings, 'coverage': coverage, 'timeUnit': time_unit, 'timeEvidence': time_evidence,
        'timingFormula': 'delta(CR_BLOCK_TIME + CURRENT_BLOCK_TIME) / delta(all six received block counters)',
        'validation': 'Calculated from repository rows; independent SQL reconciliation is not yet recorded.'}


def csv_text(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows:
        values = [row.get(field) for field in fields]
        writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value for value in values])
    return output.getvalue()


def dataset_csvs(dataset):
    routes, nodes = [], []
    for frame in dataset['frames']:
        context = {'dbid': dataset['dbid'], **dataset['scope'], 'snap': frame['snap'], 'begin': frame['begin'], 'end': frame['end'], 'boundary': frame['boundary']}
        routes.extend({**context, **route} for route in frame['routes'])
        nodes.extend({**context, **node, **node['activity']} for node in frame['nodes'])
    prefix = ['dbid', 'con_dbid', 'con_id', 'snap', 'begin', 'end', 'boundary']
    return {'transfers.csv': csv_text(routes, prefix + ['src', 'dst', 'class', 'valid', 'reason', 'seconds', 'cr', 'current', 'congested', 'crRate', 'currentRate', 'totalRate', 'congestedRate', 'timeUs', 'avgUs', 'timeReason']),
            'cache.csv': csv_text(nodes, prefix + ['id', 'name', 'host', 'startup', 'seconds', 'cacheGiB', 'logical', 'physical', 'hits', 'logicalRate', 'missRate', 'hitRate', 'hitPct', 'reason'])}
