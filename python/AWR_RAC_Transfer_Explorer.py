"""Independent Streamlit service for imported AWR RAC transfer history (8506)."""
from datetime import datetime, timedelta
import math

import oracledb
import streamlit as st
import streamlit.components.v1 as components

from awr_transfer_data import Repository, DataUnavailable, build_dataset, counter_bounds, dataset_csvs, query_ledger_sql, wall
from awr_transfer_report import render_report, report_bundle

st.set_page_config(page_title='AWR RAC Transfer Explorer', layout='wide')
st.title('AWR RAC Transfer Explorer')
st.caption('Imported customer AWR · directed cache transfers · source-clock snapshot history')
PREFIX = 'rac_transfer_'


def connect(config):
    kwargs = {'user': config['username'], 'password': config['password'],
              'dsn': oracledb.makedsn(config['host'], config['port'], service_name=config['service'])}
    if config['mode'] == 'SYSDBA':
        kwargs['mode'] = oracledb.AUTH_MODE_SYSDBA
    connection = oracledb.connect(**kwargs)
    connection.call_timeout = 120_000
    return connection


def clear_results():
    for suffix in ('dataset', 'ledger', 'html', 'zip'):
        st.session_state.pop(PREFIX + suffix, None)


def error_message(exc):
    # Never display exception locals or connection configuration.
    message = str(exc)
    password = st.session_state.get(PREFIX + 'config', {}).get('password')
    if password:
        message = message.replace(password, '[redacted]')
    return message


with st.sidebar:
    st.header('Oracle repository')
    if st.button('Disconnect / clear session'):
        for key in list(st.session_state):
            if key.startswith(PREFIX):
                del st.session_state[key]
        st.rerun()
    with st.form('rac_transfer_login'):
        host = st.text_input('Host', key=PREFIX + 'host')
        port = st.number_input('Port', min_value=1, max_value=65535, value=1521, key=PREFIX + 'port')
        service = st.text_input('Service name', key=PREFIX + 'service')
        username = st.text_input('Username', key=PREFIX + 'username')
        password = st.text_input('Password', type='password', key=PREFIX + 'password')
        mode = st.selectbox('Authentication', ['DEFAULT', 'SYSDBA'], key=PREFIX + 'mode')
        login = st.form_submit_button('Connect / refresh DBIDs', type='primary')
    st.caption('Credentials stay in this session. The app executes read-only queries against DBA_HIST views.')

if login:
    clear_results()
    for suffix in ('config', 'inventory', 'loaded', 'contexts', 'window_dbid', 'timing_scope'):
        st.session_state.pop(PREFIX + suffix, None)
    config = dict(host=host.strip(), port=int(port), service=service.strip(), username=username.strip(), password=password, mode=mode)
    if not all(config[key] for key in ('host', 'service', 'username', 'password')):
        st.error('Provide host, service name, username, and password.')
    else:
        try:
            with st.spinner('Reading available DBIDs…'), connect(config) as connection:
                inventory = Repository(connection).dbids()
            st.session_state[PREFIX + 'config'] = config
            st.session_state[PREFIX + 'inventory'] = inventory
        except Exception as exc:
            st.error(str(exc).replace(password, '[redacted]'))

config = st.session_state.get(PREFIX + 'config')
inventory = st.session_state.get(PREFIX + 'inventory', [])
if not config or not inventory:
    st.info('Connect to the database containing the imported AWR snapshots to begin.')
    st.stop()
st.caption('Active repository: {}:{}/{} · user {}'.format(config['host'], config['port'], config['service'], config['username']))
dbid = st.selectbox('Customer DBID', [int(row['dbid']) for row in inventory], key=PREFIX + 'dbid')
selected = next(row for row in inventory if int(row['dbid']) == dbid)
first, latest = wall(selected['first_time']), wall(selected['last_time'])
default_start = max(first, latest - timedelta(days=7))
if st.session_state.get(PREFIX + 'window_dbid') != dbid:
    clear_results()
    st.session_state.pop(PREFIX + 'loaded', None)
    st.session_state.pop(PREFIX + 'contexts', None)
    for key, value in {'start_date': default_start.date(), 'start_time': default_start.time(),
                       'end_date': latest.date(), 'end_time': latest.time()}.items():
        st.session_state[PREFIX + key] = value
    st.session_state[PREFIX + 'window_dbid'] = dbid
st.caption('Retained source-clock coverage: {} to {}. Default: latest seven days available, bounded by retained data.'.format(first, latest))
a, b, c, d = st.columns(4)
with a:
    start_date = st.date_input('From date', key=PREFIX + 'start_date')
with b:
    start_time = st.time_input('From time', key=PREFIX + 'start_time', step=timedelta(seconds=1))
with c:
    end_date = st.date_input('To date', key=PREFIX + 'end_date')
with d:
    end_time = st.time_input('To time', key=PREFIX + 'end_time', step=timedelta(seconds=1))
start, end = datetime.combine(start_date, start_time), datetime.combine(end_date, end_time)
contexts = st.session_state.get(PREFIX + 'contexts', [])
context = None
if contexts:
    default_context = contexts.index(0) if 0 in contexts else contexts.index(1) if 1 in contexts else 0
    context = st.selectbox('Snapshot container context', contexts, index=default_context,
                           format_func=lambda value: 'CON_ID = {}'.format(value))
load = st.button('Load snapshots and available scopes')
if start >= end:
    clear_results()
    st.error('From must be earlier than To.')
    st.stop()
if load:
    clear_results()
    st.session_state.pop(PREFIX + 'loaded', None)
    try:
        with st.spinner('Checking snapshot coverage and transfer scopes…'), connect(config) as connection:
            repo = Repository(connection)
            if not contexts:
                contexts = repo.snapshot_contexts(dbid)
                if not contexts:
                    raise DataUnavailable('No snapshot context found for the selected DBID.')
                context = 0 if 0 in contexts else 1 if 1 in contexts else contexts[0]
            snapshots = repo.snapshots(dbid, start, end, context)
            if not snapshots:
                raise DataUnavailable('No snapshot metadata found in this window.')
            low, high = counter_bounds(snapshots, start, end)
            scopes = repo.scopes(dbid, low, high)
            if not scopes:
                raise DataUnavailable('No transfer rows are visible for this DBID and snapshot range. Verify the import, view privileges, and container. SYS internal tables are not queried.')
        st.session_state[PREFIX + 'contexts'] = contexts
        st.session_state[PREFIX + 'loaded'] = {'stamp': (dbid, start, end, context), 'scopes': scopes}
        st.rerun()
    except Exception as exc:
        st.error(error_message(exc))
loaded = st.session_state.get(PREFIX + 'loaded')
if not loaded or loaded['stamp'] != (dbid, start, end, context):
    clear_results()
    st.info('Load snapshots for the selected DBID, source-clock window, and snapshot context.')
    st.stop()
scope_index = st.selectbox('Transfer and cache scope', range(len(loaded['scopes'])),
    format_func=lambda index: 'CON_DBID = {} · CON_ID = {}'.format(loaded['scopes'][index]['con_dbid'], loaded['scopes'][index]['con_id']))
scope = loaded['scopes'][scope_index]
timing_scope = (config['host'], config['port'], config['service'], config['username'], config['mode'],
                dbid, context, scope['con_dbid'], scope['con_id'])
if st.session_state.get(PREFIX + 'timing_scope') != timing_scope:
    st.session_state[PREFIX + 'timing_unit'] = None
    st.session_state[PREFIX + 'timing_evidence'] = ''
    st.session_state[PREFIX + 'timing_confirmed'] = False
    st.session_state[PREFIX + 'timing_scope'] = timing_scope
with st.expander('Transfer timing verification'):
    st.caption('Block counts and rates do not need this setting. BLOCK_TIME timing stays unavailable until its unit and total-time semantics have been verified for this repository. BUSY_TIME and CONGESTED_TIME are not added to the documented total-time columns.')
    unit = st.selectbox('Verified BLOCK_TIME unit', [None, 'microseconds', 'milliseconds', 'centiseconds'],
                        format_func=lambda value: value or 'Unverified — hide transfer timing', key=PREFIX + 'timing_unit')
    evidence = st.text_input('Verification reference / evidence', key=PREFIX + 'timing_evidence', help='For example, a checked release definition or a reconciled AWR report and its snapshot IDs.')
    confirmed = st.checkbox('I verified the BLOCK_TIME units and that these columns include busy/congested time.', key=PREFIX + 'timing_confirmed')
stamp = (dbid, start, end, context, scope_index, unit, evidence, confirmed)
if st.session_state.get(PREFIX + 'result_stamp') != stamp:
    clear_results()
generate = st.button('Generate / refresh dashboard', type='primary')
if generate:
    clear_results()
    try:
        if unit and (not confirmed or not evidence.strip()):
            raise DataUnavailable('Confirm the timing interpretation and provide its verification reference, or select Unverified.')
        with st.spinner('Reading bounded historical counters and calculating valid intervals…'), connect(config) as connection:
            repo = Repository(connection)
            snapshots = repo.snapshots(dbid, start, end, context)
            if not snapshots:
                raise DataUnavailable('The selected snapshots are no longer available. Reload coverage.')
            low, high = counter_bounds(snapshots, start, end)
            transfers = repo.scoped_rows('transfers', dbid, low, high, scope)
            warnings, optional = [], {}
            for kind in ('sga', 'sysstat'):
                try:
                    optional[kind] = repo.scoped_rows(kind, dbid, low, high, scope)
                    if not optional[kind]:
                        warnings.append('{}: no rows in the selected scope; the corresponding cache panel is unavailable.'.format(kind))
                except Exception as exc:
                    optional[kind] = []
                    warnings.append('{} unavailable: {}'.format(kind, error_message(exc)))
            try:
                identities = repo.instances(dbid)
            except Exception as exc:
                identities = []
                warnings.append('Instance names unavailable: ' + error_message(exc))
            dataset = build_dataset(dbid, scope, snapshots, transfers, optional['sga'], optional['sysstat'], identities,
                                    start, end, unit, evidence if unit else '', warnings)
        dataset['snapshotContext'] = context
        st.session_state[PREFIX + 'dataset'] = dataset
        st.session_state[PREFIX + 'ledger'] = repo.ledger
        st.session_state[PREFIX + 'result_stamp'] = stamp
    except Exception as exc:
        st.error(error_message(exc))

dataset = st.session_state.get(PREFIX + 'dataset')
if not dataset:
    st.stop()
ledger = st.session_state[PREFIX + 'ledger']
with st.expander('Reconcile a route against independent repository SQL'):
    valid_frames = [frame for frame in dataset['frames'] if any(route['valid'] for route in frame['routes'])]
    if not valid_frames:
        st.warning('No valid counter intervals are available to reconcile. Review coverage in the dashboard.')
    else:
        frame_index = st.selectbox('Snapshot to validate', range(len(valid_frames)),
                                   format_func=lambda i: str(valid_frames[i]['snap']))
        frame = valid_frames[frame_index]
        valid_routes = [route for route in frame['routes'] if route['valid']]
        route_index = st.selectbox('Directed route / block class', range(len(valid_routes)),
            format_func=lambda i: '{} → {} · {}'.format(valid_routes[i]['src'], valid_routes[i]['dst'], valid_routes[i]['class']))
        if st.button('Run independent SQL comparison'):
            route = valid_routes[route_index]
            try:
                with connect(config) as connection:
                    validator = Repository(connection)
                    rows = validator.reconcile(dataset, frame, route, context)
                ledger.extend(validator.ledger)
                expected = {'cr': route['cr'], 'current_blocks': route['current'], 'congested': route['congested'],
                            'seconds': route['seconds'], 'total_rate': route['totalRate'], 'time_us': route['timeUs'], 'avg_us': route['avgUs']}
                matches = len(rows) == 1 and all(
                    (rows[0][key] is None and value is None) or
                    (rows[0][key] is not None and value is not None and math.isclose(float(rows[0][key]), float(value), rel_tol=1e-9, abs_tol=1e-6))
                    for key, value in expected.items())
                check = {'snap': frame['snap'], 'src': route['src'], 'dst': route['dst'], 'class': route['class'],
                         'passed': matches, 'expected': expected, 'sqlResult': rows}
                dataset.setdefault('reconciliations', []).append(check)
                passed = sum(item['passed'] for item in dataset['reconciliations'])
                dataset['validation'] = '{} of {} selected route checks matched independent SQL; other intervals are not independently verified.'.format(passed, len(dataset['reconciliations']))
                st.session_state.pop(PREFIX + 'html', None)
                st.session_state.pop(PREFIX + 'zip', None)
                if matches:
                    st.success('This route and interval matched SQL counts, elapsed time, rates, and available timing.')
                else:
                    st.error('SQL reconciliation did not match. Review the exported SQL and results before using these values.')
                st.json(check)
            except Exception as exc:
                st.error(error_message(exc))
st.caption(dataset['validation'])
if PREFIX + 'html' not in st.session_state:
    st.session_state[PREFIX + 'html'] = render_report(dataset, ledger)
    st.session_state[PREFIX + 'zip'] = report_bundle(dataset, ledger, st.session_state[PREFIX + 'html'])
html = st.session_state[PREFIX + 'html']
downloads = st.columns(4)
with downloads[0]:
    st.download_button('Standalone HTML', html, 'awr-rac-transfer.html', 'text/html')
with downloads[1]:
    st.download_button('Report + CSV + SQL', st.session_state[PREFIX + 'zip'], 'awr-rac-transfer.zip', 'application/zip')
with downloads[2]:
    st.download_button('Transfer CSV', dataset_csvs(dataset)['transfers.csv'], 'transfers.csv', 'text/csv')
with downloads[3]:
    st.download_button('Reproducible SQL', query_ledger_sql(ledger), 'queries.sql', 'text/plain')
if hasattr(st, 'iframe'):
    st.iframe(html, height='content', alt='Historical RAC transfer network, timelines, and route matrix')
else:
    components.html(html, height=2350, scrolling=True)
