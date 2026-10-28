"""
AWR_Repository_Explorer.py - Oracle AWR Repository Chart Explorer

Streamlit app for connecting to an Oracle database that hosts imported
AWR repository data and charting selected DBA_HIST_* metrics.

Author: Paulo Portugal - Oracle XTeam
Date: 18-Mar-2026
"""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    import oracledb as oracle_driver
    ORACLE_DRIVER_NAME = "oracledb"
except ImportError:
    import cx_Oracle as oracle_driver
    ORACLE_DRIVER_NAME = "cx_Oracle"


st.set_page_config(
    page_title="AWR Repository Explorer",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("AWR Repository Explorer")
st.caption(
    "Connect to an Oracle database with imported AWR data, choose a DBID, "
    "and chart DBA_HIST metrics with built-in GC event analysis."
)
st.caption("Oracle driver in use: `{}`".format(ORACLE_DRIVER_NAME))


DEFAULT_CUSTOM_SQL = """select trunc(sn.begin_interval_time,'HH24') as bucket_time,
       se.instance_number,
       sum(se.total_waits_delta) as waits,
       round((sum(se.time_waited_micro_delta) / nullif(sum(se.total_waits_delta), 0)) / 1000, 3) as avg_wait_ms
from   dba_hist_system_event se
join   dba_hist_snapshot sn
  on   sn.dbid = se.dbid
 and   sn.instance_number = se.instance_number
 and   sn.snap_id = se.snap_id
where  se.dbid = :dbid
  and  sn.begin_interval_time >= :from_ts
  and  sn.begin_interval_time <  :to_ts
  and  se.event_name = 'gc current block congested'
group  by trunc(sn.begin_interval_time,'HH24'), se.instance_number
order  by bucket_time, se.instance_number"""

CHART_TYPES = {
    "Line": "line",
    "Bar": "bar",
    "Scatter": "scatter",
    "Area": "area",
}

LOAD_STAT_NAMES = [
    "execute count",
    "user calls",
    "user commits",
]

RECENT_CONNECTIONS_PATH = Path(__file__).resolve().parent.parent / ".awr_recent_connections.json"
MAX_RECENT_CONNECTIONS = 8


class ConnectionConfig:
    def __init__(self, username, password, host, port, service_name, mode):
        self.username = username
        self.password = password
        self.host = host
        self.port = port
        self.service_name = service_name
        self.mode = mode

    def dsn(self) -> str:
        return oracle_driver.makedsn(self.host, self.port, service_name=self.service_name)


def load_recent_connections() -> list[Dict[str, Any]]:
    if not RECENT_CONNECTIONS_PATH.exists():
        return []
    try:
        payload = json.loads(RECENT_CONNECTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    sanitized = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            sanitized.append(
                {
                    "name": str(item["name"]),
                    "host": str(item["host"]),
                    "port": int(item["port"]),
                    "service_name": str(item["service_name"]),
                    "username": str(item["username"]),
                    "mode": str(item.get("mode", "DEFAULT")),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sanitized


def save_recent_connections(connections: list[Dict[str, Any]]) -> None:
    RECENT_CONNECTIONS_PATH.write_text(json.dumps(connections[:MAX_RECENT_CONNECTIONS], indent=2), encoding="utf-8")


def connection_entry_name(host: str, port: int, service_name: str, username: str, mode: str) -> str:
    return f"{username}@{host}:{port}/{service_name} [{mode}]"


def store_recent_connection(host: str, port: int, service_name: str, username: str, mode: str) -> list[Dict[str, Any]]:
    entry = {
        "name": connection_entry_name(host, port, service_name, username, mode),
        "host": host,
        "port": int(port),
        "service_name": service_name,
        "username": username,
        "mode": mode,
    }
    recent = [
        item
        for item in load_recent_connections()
        if not (
            item["host"] == entry["host"]
            and int(item["port"]) == entry["port"]
            and item["service_name"] == entry["service_name"]
            and item["username"] == entry["username"]
            and item["mode"] == entry["mode"]
        )
    ]
    recent.insert(0, entry)
    save_recent_connections(recent)
    return recent


def remove_recent_connection(name: str) -> list[Dict[str, Any]]:
    recent = [item for item in load_recent_connections() if item["name"] != name]
    save_recent_connections(recent)
    return recent


def get_connection(config: ConnectionConfig):
    connect_mode = None
    if config.mode == "SYSDBA":
        connect_mode = getattr(oracle_driver, "AUTH_MODE_SYSDBA", None)
        if connect_mode is None:
            connect_mode = getattr(oracle_driver, "SYSDBA", None)

    connect_kwargs = {
        "user": config.username,
        "password": config.password,
        "dsn": config.dsn(),
    }
    if connect_mode is not None:
        connect_kwargs["mode"] = connect_mode

    return oracle_driver.connect(**connect_kwargs)


def run_query(
    config: ConnectionConfig,
    sql: str,
    binds: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    with get_connection(config) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, binds or {})
        rows = cursor.fetchall()
        columns = [desc[0].lower() for desc in cursor.description] if cursor.description else []
        frame = pd.DataFrame(rows, columns=columns)
    frame.columns = [col.lower() for col in frame.columns]
    return frame


def fetch_dbids(config: ConnectionConfig) -> pd.DataFrame:
    sql = """
        select dbid,
               min(db_name) as db_name,
               min(instance_name) as sample_instance,
               min(startup_time) as first_startup_time,
               max(instance_number) as max_instance_number
        from   dba_hist_database_instance
        group  by dbid
        order  by dbid
    """
    return run_query(config, sql)


def fetch_view_columns(config: ConnectionConfig, view_name: str) -> set:
    sql = """
        select lower(column_name) as column_name
        from   all_tab_columns
        where  owner = 'SYS'
          and  table_name = upper(:view_name)
    """
    frame = run_query(config, sql, {"view_name": view_name})
    return set(frame["column_name"].tolist())


def fetch_event_names(
    config: ConnectionConfig,
    dbid: int,
) -> list:
    sql = """
        with snap_window as (
          select min(snap_id) as min_snap_id,
                 max(snap_id) as max_snap_id
          from (
            select distinct snap_id
            from   dba_hist_snapshot
            where  dbid = :dbid
            order  by snap_id desc
            fetch first 8 rows only
          )
        )
        select distinct se.event_name
        from   dba_hist_system_event se
        cross join snap_window sw
        where  se.dbid = :dbid
          and  se.snap_id between sw.min_snap_id and sw.max_snap_id
          and  se.event_name is not null
        order  by se.event_name
    """
    frame = run_query(
        config,
        sql,
        {
            "dbid": dbid,
        },
    )
    return frame["event_name"].tolist()


def build_gc_preset_sql(system_event_columns: set) -> str:
    if {"total_waits_delta", "time_waited_micro_delta"}.issubset(system_event_columns):
        return """select trunc(sn.begin_interval_time,'HH24') as bucket_time,
       se.event_name,
       sum(se.total_waits_delta) as waits,
       round(sum(se.time_waited_micro_delta)/1e6, 3) as waited_seconds,
       round((sum(se.time_waited_micro_delta) / nullif(sum(se.total_waits_delta), 0)) / 1000, 3) as avg_wait_ms
from   dba_hist_system_event se
join   dba_hist_snapshot sn
  on   sn.dbid = se.dbid
 and   sn.instance_number = se.instance_number
 and   sn.snap_id = se.snap_id
where  se.dbid = :dbid
  and  se.event_name = :event_name
  and  sn.begin_interval_time >= :from_ts
  and  sn.begin_interval_time <  :to_ts
group  by trunc(sn.begin_interval_time,'HH24'), se.event_name
order  by bucket_time"""

    if {"total_waits", "time_waited_micro"}.issubset(system_event_columns):
        return """with event_deltas as (
  select sn.begin_interval_time,
         se.event_name,
         greatest(
           se.total_waits - lag(se.total_waits) over (
             partition by se.dbid, se.instance_number, se.event_name
             order by se.snap_id
           ),
           0
         ) as waits_delta,
         greatest(
           se.time_waited_micro - lag(se.time_waited_micro) over (
             partition by se.dbid, se.instance_number, se.event_name
             order by se.snap_id
           ),
           0
         ) as time_waited_micro_delta
  from   dba_hist_system_event se
  join   dba_hist_snapshot sn
    on   sn.dbid = se.dbid
   and   sn.instance_number = se.instance_number
   and   sn.snap_id = se.snap_id
  where  se.dbid = :dbid
    and  se.event_name = :event_name
    and  sn.begin_interval_time >= :from_ts - interval '1' day
    and  sn.begin_interval_time <  :to_ts
)
select trunc(begin_interval_time,'HH24') as bucket_time,
       event_name,
       sum(waits_delta) as waits,
       round(sum(time_waited_micro_delta)/1e6, 3) as waited_seconds,
       round((sum(time_waited_micro_delta) / nullif(sum(waits_delta), 0)) / 1000, 3) as avg_wait_ms
from   event_deltas
where  begin_interval_time >= :from_ts
  and  begin_interval_time <  :to_ts
group  by trunc(begin_interval_time,'HH24'), event_name
order  by bucket_time"""

    raise ValueError(
        "DBA_HIST_SYSTEM_EVENT does not expose either delta columns "
        "(`TOTAL_WAITS_DELTA`, `TIME_WAITED_MICRO_DELTA`) or cumulative "
        "columns (`TOTAL_WAITS`, `TIME_WAITED_MICRO`) in this repository."
    )


def build_ash_event_sql() -> str:
    return """select trunc(ash.sample_time,'HH24') as bucket_time,
       ash.event as event_name,
       ash.sql_id,
       count(*) as samples,
       round(sum(ash.time_waited)/1e6, 3) as waited_seconds,
       round((sum(ash.time_waited) / nullif(count(*), 0)) / 1000, 3) as avg_wait_ms
from   dba_hist_active_sess_history ash
where  ash.dbid = :dbid
  and  ash.event = :event_name
  and  ash.sample_time >= :from_ts
  and  ash.sample_time <  :to_ts
  and  ash.sql_id = :sql_id
group  by trunc(ash.sample_time,'HH24'), ash.event, ash.sql_id
order  by bucket_time"""


def build_top_gc_sqlids_sql() -> str:
    return """with ash as (
  select ash.sql_id,
         ash.instance_number,
         count(*) as samples,
         sum(ash.wait_time + ash.time_waited) / 1e6 as waited_sec
  from   dba_hist_active_sess_history ash
  where  ash.dbid = :dbid
    and  ash.sample_time >= :from_ts
    and  ash.sample_time <  :to_ts
    and  ash.event = 'gc current block congested'
    and  ash.sql_id is not null
  group  by ash.sql_id, ash.instance_number
),
agg as (
  select sql_id,
         sum(samples) as samples,
         sum(waited_sec) as waited_sec,
         round(sum(waited_sec) * 1000 / nullif(sum(samples),0), 3) as avg_wait_ms
  from   ash
  group  by sql_id
),
inst as (
  select sql_id,
         listagg(to_char(instance_number), ',')
           within group (order by instance_number) as insts
  from   (select distinct sql_id, instance_number from ash)
  group  by sql_id
)
select *
from (
  select a.sql_id,
         a.samples,
         round(a.waited_sec, 3) as waited_sec,
         a.avg_wait_ms,
         i.insts
  from   agg a
  join   inst i
    on   i.sql_id = a.sql_id
  order  by a.waited_sec desc
)
where rownum <= :top_n"""


def build_sqlid_service_sql() -> str:
    return """select nvl(sn.service_name, '[unknown]') as service_name,
       count(*) as samples,
       round(sum(ash.time_waited)/1e6, 3) as waited_seconds,
       listagg(distinct to_char(ash.instance_number), ',')
         within group (order by to_char(ash.instance_number)) as instances
from   dba_hist_active_sess_history ash
left join dba_hist_service_name sn
  on   sn.dbid = ash.dbid
 and   sn.service_name_hash = ash.service_hash
where  ash.dbid = :dbid
  and  ash.sql_id = :sql_id
  and  ash.sample_time >= :from_ts
  and  ash.sample_time <  :to_ts
group  by nvl(sn.service_name, '[unknown]')
order  by waited_seconds desc, samples desc"""


def build_sqlid_event_sql() -> str:
    return """select ash.event,
       count(*) as samples,
       round(sum(ash.time_waited)/1e6, 3) as waited_seconds,
       round((sum(ash.time_waited) / nullif(count(*),0)) / 1000, 3) as avg_wait_ms
from   dba_hist_active_sess_history ash
where  ash.dbid = :dbid
  and  ash.sql_id = :sql_id
  and  ash.sample_time >= :from_ts
  and  ash.sample_time <  :to_ts
group  by ash.event
order  by waited_seconds desc nulls last, samples desc"""


def build_sqlid_sqlstat_sql(sqlstat_columns: set) -> str:
    if {"executions_delta", "elapsed_time_delta", "rows_processed_delta", "cpu_time_delta"}.issubset(sqlstat_columns):
        return """select trunc(sn.begin_interval_time,'HH24') as bucket_time,
       st.sql_id,
       sum(st.executions_delta) as executions,
       sum(st.rows_processed_delta) as rows_processed,
       round(sum(st.elapsed_time_delta)/1e6, 3) as elapsed_seconds,
       round(sum(st.cpu_time_delta)/1e6, 3) as cpu_seconds,
       round((sum(st.elapsed_time_delta)/nullif(sum(st.executions_delta),0))/1000, 3) as ms_per_exec
from   dba_hist_sqlstat st
join   dba_hist_snapshot sn
  on   sn.dbid = st.dbid
 and   sn.instance_number = st.instance_number
 and   sn.snap_id = st.snap_id
where  st.dbid = :dbid
  and  st.sql_id = :sql_id
  and  sn.begin_interval_time >= :from_ts
  and  sn.begin_interval_time <  :to_ts
group  by trunc(sn.begin_interval_time,'HH24'), st.sql_id
order  by bucket_time"""
    raise ValueError("DBA_HIST_SQLSTAT does not expose the expected delta columns in this repository.")


def build_top_segments_sql(metric_column: str) -> str:
    return """select *
from (
  select o.owner,
         o.object_name,
         o.subobject_name,
         o.object_type,
         sum(ss.{metric_column}) as stat_value
  from   dba_hist_seg_stat ss
  join   dba_hist_snapshot sn
    on   sn.dbid = ss.dbid
   and   sn.instance_number = ss.instance_number
   and   sn.snap_id = ss.snap_id
  join   dba_hist_seg_stat_obj o
    on   o.dbid = ss.dbid
   and   o.obj# = ss.obj#
   and   o.dataobj# = ss.dataobj#
  where  ss.dbid = :dbid
    and  sn.begin_interval_time >= :from_ts
    and  sn.begin_interval_time <  :to_ts
  group  by o.owner, o.object_name, o.subobject_name, o.object_type
  order  by stat_value desc
)
where rownum <= :top_n""".format(metric_column=metric_column)


def fetch_seg_stat_metric_columns(config: ConnectionConfig) -> list:
    columns = fetch_view_columns(config, "DBA_HIST_SEG_STAT")
    ignored = {
        "snap_id", "dbid", "instance_number", "ts#", "obj#", "dataobj#",
        "logical_reads_total", "physical_reads_total", "physical_writes_total",
    }
    metric_columns = [
        column for column in sorted(columns)
        if column not in ignored and (column.endswith("_delta") or column.endswith("_total"))
    ]
    return metric_columns


def run_gc_preset(
    config: ConnectionConfig,
    dbid: int,
    event_name: str,
    from_ts: datetime,
    to_ts: datetime,
    sql_id_filter: str,
) -> (pd.DataFrame, str):
    if sql_id_filter:
        preset_sql = build_ash_event_sql()
        binds = {
            "dbid": dbid,
            "event_name": event_name,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "sql_id": sql_id_filter,
        }
    else:
        system_event_columns = fetch_view_columns(config, "DBA_HIST_SYSTEM_EVENT")
        preset_sql = build_gc_preset_sql(system_event_columns)
        binds = {
            "dbid": dbid,
            "event_name": event_name,
            "from_ts": from_ts,
            "to_ts": to_ts,
        }
    return convert_datetime_columns(run_query(config, preset_sql, binds)), preset_sql


def build_daily_load_sql(sysstat_columns: set) -> str:
    if not {"value"}.issubset(sysstat_columns):
        raise ValueError(
            "DBA_HIST_SYSSTAT does not expose VALUE, which is required for the daily load snapshot."
        )

    return """with snaps as (
  select dbid, instance_number, snap_id, begin_interval_time
  from   dba_hist_snapshot
  where  dbid = :dbid
    and  begin_interval_time >= :start_day - 1
    and  begin_interval_time <  :end_day
),
stat_base as (
  select sn.begin_interval_time,
         ss.instance_number,
         ss.stat_name,
         ss.value
  from   dba_hist_sysstat ss
  join   snaps sn
    on   sn.dbid = ss.dbid
   and   sn.instance_number = ss.instance_number
   and   sn.snap_id = ss.snap_id
  where  ss.dbid = :dbid
    and  ss.stat_name in ('execute count','user calls','user commits')
),
stat_delta as (
  select begin_interval_time,
         stat_name,
         greatest(
           value - lag(value) over (
             partition by instance_number, stat_name
             order by begin_interval_time
           ),
           0
         ) as delta
  from   stat_base
)
select to_char(trunc(begin_interval_time),'YYYY-MM-DD') as day_key,
       round(sum(case when stat_name = 'execute count' then delta else 0 end) / 1e9, 2) as execs_b,
       round(sum(case when stat_name = 'user calls' then delta else 0 end) / 1e9, 2) as calls_b,
       round(sum(case when stat_name = 'user commits' then delta else 0 end) / 1e6, 2) as commits_m
from   stat_delta
where  begin_interval_time >= :start_day
  and  begin_interval_time <  :end_day
group  by trunc(begin_interval_time)
order  by trunc(begin_interval_time)"""


def build_daily_gc_sql(system_event_columns: set) -> str:
    required_columns = {"total_waits_fg", "time_waited_micro_fg"}
    if not required_columns.issubset(system_event_columns):
        raise ValueError(
            "DBA_HIST_SYSTEM_EVENT does not expose TOTAL_WAITS_FG and TIME_WAITED_MICRO_FG, "
            "which are required for the daily GC congestion query."
        )

    return """with snaps as (
  select dbid, instance_number, snap_id, begin_interval_time
  from   dba_hist_snapshot
  where  dbid = :dbid
    and  begin_interval_time >= :start_day - 1
    and  begin_interval_time <  :end_day
),
se_base as (
  select sn.begin_interval_time,
         se.instance_number,
         se.event_name,
         se.total_waits_fg,
         se.time_waited_micro_fg
  from   dba_hist_system_event se
  join   snaps sn
    on   sn.dbid = se.dbid
   and   sn.instance_number = se.instance_number
   and   sn.snap_id = se.snap_id
  where  se.dbid = :dbid
    and  se.event_name in ('gc cr block congested','gc current block congested')
),
se_delta as (
  select begin_interval_time,
         event_name,
         greatest(
           total_waits_fg - lag(total_waits_fg) over (
             partition by instance_number, event_name
             order by begin_interval_time
           ),
           0
         ) as waits,
         greatest(
           time_waited_micro_fg - lag(time_waited_micro_fg) over (
             partition by instance_number, event_name
             order by begin_interval_time
           ),
           0
         ) as waited_us
  from   se_base
)
select to_char(day_key, 'YYYY-MM-DD') as day_key,
       round(max(case when event_name = 'gc cr block congested' then waits end) / 1e6, 2) as cr_waits_m,
       round(max(case when event_name = 'gc cr block congested' then waited_us end) / 1e9, 2) as cr_waited_ks,
       round(
         max(case when event_name = 'gc cr block congested' then waited_us end)
         / nullif(max(case when event_name = 'gc cr block congested' then waits end), 0)
         / 1000,
         2
       ) as cr_avg_ms,
       round(max(case when event_name = 'gc current block congested' then waits end) / 1e6, 2) as cur_waits_m,
       round(max(case when event_name = 'gc current block congested' then waited_us end) / 1e9, 2) as cur_waited_ks,
       round(
         max(case when event_name = 'gc current block congested' then waited_us end)
         / nullif(max(case when event_name = 'gc current block congested' then waits end), 0)
         / 1000,
         2
       ) as cur_avg_ms
from (
  select trunc(begin_interval_time) as day_key,
         event_name,
         sum(waits) as waits,
         sum(waited_us) as waited_us
  from   se_delta
  where  begin_interval_time >= :start_day
    and  begin_interval_time <  :end_day
  group  by trunc(begin_interval_time), event_name
)
group by day_key
order by day_key"""


def format_human_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "0"
    value = float(value)
    abs_value = abs(value)
    if abs_value >= 1e9:
        return "{:.2f}B".format(value / 1e9)
    if abs_value >= 1e6:
        return "{:.2f}M".format(value / 1e6)
    if abs_value >= 1e3:
        return "{:.2f}K".format(value / 1e3)
    return "{:.2f}".format(value)


def render_dataframe_chart_controls(frame: pd.DataFrame, key_prefix: str, title: str):
    numeric_columns = [
        col for col in frame.columns
        if pd.api.types.is_numeric_dtype(frame[col])
    ]
    if not numeric_columns:
        return

    datetime_columns = [
        col for col in frame.columns
        if pd.api.types.is_datetime64_any_dtype(frame[col])
    ]
    dimension_columns = [col for col in frame.columns if col not in numeric_columns]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        x_col = st.selectbox(
            "X Axis",
            options=frame.columns.tolist(),
            index=frame.columns.tolist().index(datetime_columns[0]) if datetime_columns else 0,
            key="{}_x".format(key_prefix),
        )
    with col2:
        default_y = choose_default_y(frame.columns)
        y_options = numeric_columns
        y_col = st.selectbox(
            "Y Axis",
            options=y_options,
            index=y_options.index(default_y) if default_y in y_options else 0,
            key="{}_y".format(key_prefix),
        )
    with col3:
        chart_type_label = st.selectbox(
            "Chart Type",
            options=list(CHART_TYPES.keys()),
            key="{}_chart".format(key_prefix),
        )
    with col4:
        series_choices = ["<none>"] + dimension_columns
        series_col = st.selectbox(
            "Series",
            options=series_choices,
            key="{}_series".format(key_prefix),
        )

    render_chart(
        frame,
        x_col=x_col,
        y_col=y_col,
        chart_type=CHART_TYPES[chart_type_label],
        series_col=None if series_col == "<none>" else series_col,
        title=title,
    )


def validate_custom_sql(sql_text: str) -> Optional[str]:
    normalized = sql_text.lower()

    if not normalized.strip().startswith("select"):
        return "Only SELECT statements are allowed."
    if ":dbid" not in normalized:
        return "The query must include the :dbid bind variable."
    if "dba_hist_" not in normalized:
        return "The query must use DBA_HIST_% views."

    banned_tokens = [
        " insert ",
        " update ",
        " delete ",
        " merge ",
        " alter ",
        " drop ",
        " truncate ",
        " begin ",
        " commit",
        " rollback",
    ]
    padded = f" {normalized} "
    if any(token in padded for token in banned_tokens):
        return "Only read-only DBA_HIST SELECT queries are allowed."

    return None


def coerce_datetime(value: date, value_time: time) -> datetime:
    return datetime.combine(value, value_time)


def format_dbid_label(row: pd.Series) -> str:
    db_name = row.get("db_name") or "unknown"
    sample_instance = row.get("sample_instance") or "n/a"
    return f"{int(row['dbid'])} | {db_name} | sample instance {sample_instance}"


def convert_datetime_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = pd.to_datetime(result[column])
    return result


def choose_default_y(columns: Iterable[str]) -> Optional[str]:
    preferred = ["avg_wait_ms", "waited_seconds", "waits", "elapsed_s"]
    column_list = list(columns)
    for candidate in preferred:
        if candidate in column_list:
            return candidate
    numeric = [col for col in column_list if col not in ("bucket_time", "event_name")]
    return numeric[0] if numeric else None


def render_chart(
    frame: pd.DataFrame,
    x_col: str,
    y_col: str,
    chart_type: str,
    series_col: Optional[str],
    title: str,
):
    if chart_type == "line":
        fig = px.line(frame, x=x_col, y=y_col, color=series_col, markers=True, title=title)
    elif chart_type == "bar":
        fig = px.bar(frame, x=x_col, y=y_col, color=series_col, title=title, barmode="group")
    elif chart_type == "area":
        fig = px.area(frame, x=x_col, y=y_col, color=series_col, title=title)
    else:
        fig = px.scatter(frame, x=x_col, y=y_col, color=series_col, title=title)

    fig.update_layout(legend_title_text=series_col or "", height=520)
    st.plotly_chart(fig, use_container_width=True)


def render_gc_compare_chart(frame: pd.DataFrame, y_col: str, title: str):
    plot_frame = frame.copy()
    plot_frame["snap_time_label"] = plot_frame["bucket_time"].dt.strftime("%Y-%m-%d %H:%M")
    fig = px.line(
        plot_frame,
        x="snap_time_label",
        y=y_col,
        color="period_label",
        markers=True,
        title=title,
        hover_data=["bucket_time"],
    )
    fig.update_layout(
        legend_title_text="Period",
        height=520,
        xaxis_title="Snapshot Time",
        yaxis_title=y_col,
    )
    st.plotly_chart(fig, use_container_width=True)


st.session_state.setdefault("awr_host", "localhost")
st.session_state.setdefault("awr_port", 1521)
st.session_state.setdefault("awr_service_name", "orclpdb1")
st.session_state.setdefault("awr_username", "system")
st.session_state.setdefault("awr_mode", "DEFAULT")


with st.sidebar:
    st.header("Oracle Login")
    recent_connections = load_recent_connections()
    recent_labels = ["Select a saved connection..."] + [item["name"] for item in recent_connections]
    selected_recent = st.selectbox("Saved Connections", options=recent_labels, index=0)
    recent_action_col1, recent_action_col2 = st.columns(2)
    with recent_action_col1:
        load_recent_clicked = st.button("Load Saved", use_container_width=True)
    with recent_action_col2:
        remove_recent_clicked = st.button("Remove Saved", use_container_width=True)

    if load_recent_clicked and selected_recent != recent_labels[0]:
        selected_entry = next((item for item in recent_connections if item["name"] == selected_recent), None)
        if selected_entry is not None:
            st.session_state["awr_host"] = selected_entry["host"]
            st.session_state["awr_port"] = int(selected_entry["port"])
            st.session_state["awr_service_name"] = selected_entry["service_name"]
            st.session_state["awr_username"] = selected_entry["username"]
            st.session_state["awr_mode"] = selected_entry["mode"]
            st.rerun()

    if remove_recent_clicked and selected_recent != recent_labels[0]:
        remove_recent_connection(selected_recent)
        st.session_state.pop("awr_selected_recent", None)
        st.rerun()

    host = st.text_input("Host", key="awr_host")
    port = st.number_input("Port", min_value=1, max_value=65535, key="awr_port")
    service_name = st.text_input("Service Name", key="awr_service_name")
    username = st.text_input("Username", key="awr_username")
    password = st.text_input("Password", type="password")
    mode = st.selectbox("Auth Mode", options=["DEFAULT", "SYSDBA"], key="awr_mode")
    save_current_clicked = st.button("Save Current Connection", use_container_width=True)

    connect_clicked = st.button("Connect / Refresh DBIDs", type="primary", use_container_width=True)


connection_ready = all([host, port, service_name, username, password])
save_ready = all([host, port, service_name, username, mode])
config = None

if connection_ready:
    config = ConnectionConfig(
        username=username.strip(),
        password=password,
        host=host.strip(),
        port=int(port),
        service_name=service_name.strip(),
        mode=mode,
    )
if save_current_clicked:
    if save_ready:
        store_recent_connection(
            host=host.strip(),
            port=int(port),
            service_name=service_name.strip(),
            username=username.strip(),
            mode=mode,
        )
        st.success("Connection saved.")
    else:
        st.error("Provide host, port, service name, username, and auth mode before saving.")

if connect_clicked and not connection_ready:
    st.error("Provide host, port, service name, username, and password before connecting.")

dbid_frame = None
if connect_clicked and config is not None:
    try:
        dbid_frame = fetch_dbids(config)
        store_recent_connection(
            host=config.host,
            port=config.port,
            service_name=config.service_name,
            username=config.username,
            mode=config.mode,
        )
        st.session_state["awr_dbids"] = dbid_frame
        st.success(f"Connected successfully. Found {len(dbid_frame)} DBID value(s).")
    except Exception as exc:
        st.exception(exc)

if dbid_frame is None:
    dbid_frame = st.session_state.get("awr_dbids")

if dbid_frame is None or len(dbid_frame) == 0:
    st.info("Connect first to list the DBIDs available in the AWR repository.")
    st.stop()

dbid_options = {format_dbid_label(row): int(row["dbid"]) for _, row in dbid_frame.iterrows()}
selected_dbid_label = st.selectbox("DBID", options=list(dbid_options.keys()))
selected_dbid = dbid_options[selected_dbid_label]

default_end = datetime.now()
default_start = default_end - timedelta(days=7)

st.subheader("Analysis Window")
window_col1, window_col2, window_col3, window_col4 = st.columns(4)
with window_col1:
    from_date = st.date_input("From Date", value=default_start.date())
with window_col2:
    from_time = st.time_input("From Time", value=time(default_start.hour, 0))
with window_col3:
    to_date = st.date_input("To Date", value=default_end.date())
with window_col4:
    to_time = st.time_input("To Time", value=time(default_end.hour, 0))

from_ts = coerce_datetime(from_date, from_time)
to_ts = coerce_datetime(to_date, to_time)

if from_ts >= to_ts:
    st.error("The analysis window is invalid: `From` must be earlier than `To`.")
    st.stop()

tab_preset, tab_top_sql, tab_sqlid_drill, tab_segments, tab_load_gc, tab_custom = st.tabs([
    "GC Event Preset",
    "Top SQL_IDs GC Current",
    "SQL_ID Drilldown",
    "Top Segments",
    "Daily Load & GC",
    "Custom DBA_HIST Query",
])

with tab_preset:
    st.markdown(
        "Preset query for AWR-style GC trend analysis using `DBA_HIST_SYSTEM_EVENT` "
        "and `DBA_HIST_SNAPSHOT` with explicit `DBID` filtering."
    )
    event_cache = st.session_state.get("awr_event_names_by_dbid", {})
    available_events = event_cache.get(selected_dbid, [])

    preset_col1, preset_col2, preset_col3, preset_col4 = st.columns([2, 1, 1.2, 1.1])
    with preset_col1:
        default_event = "gc current block congested"
        if available_events:
            default_index = available_events.index(default_event) if default_event in available_events else 0
            event_name = st.selectbox(
                "Event",
                options=available_events,
                index=default_index,
                help="Start typing to filter available event names from DBA_HIST_SYSTEM_EVENT.",
            )
        else:
            event_name = st.text_input(
                "Event",
                value=default_event,
                help="Event list could not be loaded, so enter the event name manually.",
            )
    with preset_col2:
        chart_metric = st.selectbox(
            "Metric",
            options=["avg_wait_ms", "waited_seconds", "waits_or_samples"],
            index=0,
        )
    with preset_col3:
        sql_id_filter = st.text_input(
            "SQL_ID Filter",
            value="",
            help="Optional. If provided, the preset switches to DBA_HIST_ACTIVE_SESS_HISTORY so results are filtered to one SQL_ID.",
        ).strip()
    with preset_col4:
        compare_mode = st.checkbox(
            "Compare 2 Periods",
            value=False,
            help="Overlay the same event from two different time windows on one chart.",
        )

    compare_from_ts = None
    compare_to_ts = None
    if compare_mode:
        st.markdown("#### Comparison Window")
        cmp1, cmp2, cmp3, cmp4 = st.columns(4)
        with cmp1:
            compare_from_date = st.date_input("Compare From Date", value=from_date, key="compare_from_date")
        with cmp2:
            compare_from_time = st.time_input("Compare From Time", value=from_time, key="compare_from_time")
        with cmp3:
            compare_to_date = st.date_input("Compare To Date", value=to_date, key="compare_to_date")
        with cmp4:
            compare_to_time = st.time_input("Compare To Time", value=to_time, key="compare_to_time")
        compare_from_ts = coerce_datetime(compare_from_date, compare_from_time)
        compare_to_ts = coerce_datetime(compare_to_date, compare_to_time)

    if st.button("Run GC Preset", use_container_width=True):
        if config is None:
            st.error("Connection details are incomplete.")
        else:
            try:
                if selected_dbid not in event_cache:
                    event_cache[selected_dbid] = fetch_event_names(config, selected_dbid)
                    st.session_state["awr_event_names_by_dbid"] = event_cache

                result, preset_sql = run_gc_preset(
                    config,
                    selected_dbid,
                    event_name,
                    from_ts,
                    to_ts,
                    sql_id_filter,
                )
                result["period_label"] = "Primary"
                if compare_mode:
                    if compare_from_ts >= compare_to_ts:
                        st.error("The comparison window is invalid.")
                        st.stop()
                    compare_result, _ = run_gc_preset(
                        config,
                        selected_dbid,
                        event_name,
                        compare_from_ts,
                        compare_to_ts,
                        sql_id_filter,
                    )
                    compare_result["period_label"] = "Comparison"
                    result = pd.concat([result, compare_result], ignore_index=True, sort=False)

                if result.empty or (result["period_label"] == "Primary").sum() == 0:
                    st.warning("No rows returned for the selected DBID, event, and time window.")
                else:
                    metric_column = chart_metric
                    if chart_metric == "waits_or_samples":
                        metric_column = "samples" if "samples" in result.columns else "waits"

                    chart_title = (
                        f"{event_name} over time for DBID {selected_dbid}"
                        + (f" and SQL_ID {sql_id_filter}" if sql_id_filter else "")
                    )
                    st.session_state["awr_gc_preset_result"] = result
                    st.session_state["awr_gc_preset_sql"] = preset_sql
                    st.session_state["awr_gc_preset_metric"] = metric_column
                    st.session_state["awr_gc_preset_compare_mode"] = compare_mode
                    st.session_state["awr_gc_preset_chart_title"] = chart_title
            except Exception as exc:
                st.exception(exc)

    preset_result = st.session_state.get("awr_gc_preset_result")
    preset_sql = st.session_state.get("awr_gc_preset_sql")
    preset_metric = st.session_state.get("awr_gc_preset_metric")
    preset_compare_mode = st.session_state.get("awr_gc_preset_compare_mode", False)
    preset_chart_title = st.session_state.get("awr_gc_preset_chart_title")

    if preset_result is not None and not preset_result.empty:
        if preset_compare_mode:
            render_gc_compare_chart(preset_result, preset_metric, preset_chart_title)
        else:
            render_chart(
                preset_result,
                x_col="bucket_time",
                y_col=preset_metric,
                chart_type="line",
                series_col="sql_id" if "sql_id" in preset_result.columns else "event_name",
                title=preset_chart_title,
            )
        st.dataframe(preset_result, use_container_width=True)
        if preset_sql:
            st.code(preset_sql, language="sql")

with tab_custom:
    st.markdown(
        "Run your own read-only `DBA_HIST_%` query. The SQL must include the "
        "`:dbid` bind variable. `:from_ts` and `:to_ts` are optional but supported."
    )
    custom_sql = st.text_area(
        "SQL",
        value=DEFAULT_CUSTOM_SQL,
        height=260,
        help="Return at least one time-like column and one numeric column if you want a chart.",
    )

    validation_error = validate_custom_sql(custom_sql)
    if validation_error:
        st.warning(validation_error)

    if st.button("Run Custom Query", use_container_width=True, disabled=validation_error is not None):
        if config is None:
            st.error("Connection details are incomplete.")
        else:
            try:
                result = run_query(
                    config,
                    custom_sql,
                    {
                        "dbid": selected_dbid,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                    },
                )
                result = convert_datetime_columns(result)

                if result.empty:
                    st.warning("The custom query returned no rows.")
                else:
                    st.success(f"Returned {len(result)} row(s).")
                    st.dataframe(result, use_container_width=True)

                    datetime_columns = [
                        col for col in result.columns
                        if pd.api.types.is_datetime64_any_dtype(result[col])
                    ]
                    numeric_columns = [
                        col for col in result.columns
                        if pd.api.types.is_numeric_dtype(result[col])
                    ]
                    dimension_columns = [
                        col for col in result.columns
                        if col not in numeric_columns
                    ]

                    if not datetime_columns:
                        st.info("No datetime column detected. The chart builder can still use any selected X-axis column.")

                    if not numeric_columns:
                        st.warning("No numeric column returned, so no chart can be built.")
                    else:
                        chart_builder_col1, chart_builder_col2, chart_builder_col3, chart_builder_col4 = st.columns(4)
                        with chart_builder_col1:
                            x_col = st.selectbox(
                                "X Axis",
                                options=result.columns.tolist(),
                                index=result.columns.tolist().index(datetime_columns[0]) if datetime_columns else 0,
                                key="custom_x_axis",
                            )
                        with chart_builder_col2:
                            default_y = choose_default_y(result.columns)
                            y_col = st.selectbox(
                                "Y Axis",
                                options=numeric_columns,
                                index=numeric_columns.index(default_y) if default_y in numeric_columns else 0,
                                key="custom_y_axis",
                            )
                        with chart_builder_col3:
                            chart_type_label = st.selectbox("Chart Type", options=list(CHART_TYPES.keys()), key="custom_chart_type")
                        with chart_builder_col4:
                            series_choices = ["<none>"] + dimension_columns
                            series_col = st.selectbox("Series", options=series_choices, key="custom_series")

                        render_chart(
                            result,
                            x_col=x_col,
                            y_col=y_col,
                            chart_type=CHART_TYPES[chart_type_label],
                            series_col=None if series_col == "<none>" else series_col,
                            title=f"Custom DBA_HIST chart for DBID {selected_dbid}",
                        )
            except Exception as exc:
                st.exception(exc)

with tab_load_gc:
    st.markdown(
        "Generate daily load and GC congestion summaries from AWR history, then chart "
        "the returned data in a custom way."
    )

    if st.button("Run Daily Load & GC Snapshot", use_container_width=True):
        if config is None:
            st.error("Connection details are incomplete.")
        else:
            try:
                system_event_columns = fetch_view_columns(config, "DBA_HIST_SYSTEM_EVENT")
                sysstat_columns = fetch_view_columns(config, "DBA_HIST_SYSSTAT")

                load_sql = build_daily_load_sql(sysstat_columns)
                gc_sql = build_daily_gc_sql(system_event_columns)

                load_result = run_query(
                    config,
                    load_sql,
                    {
                        "dbid": selected_dbid,
                        "start_day": from_ts,
                        "end_day": to_ts,
                    },
                )
                gc_result = run_query(
                    config,
                    gc_sql,
                    {
                        "dbid": selected_dbid,
                        "start_day": from_ts,
                        "end_day": to_ts,
                    },
                )

                load_result = convert_datetime_columns(load_result)
                gc_result = convert_datetime_columns(gc_result)

                st.session_state["awr_daily_load_result"] = load_result
                st.session_state["awr_daily_gc_result"] = gc_result
                st.session_state["awr_daily_load_sql"] = load_sql
                st.session_state["awr_daily_gc_sql"] = gc_sql
                st.success(
                    "Daily snapshot completed. Load rows: {} | GC rows: {}".format(
                        len(load_result),
                        len(gc_result),
                    )
                )
            except Exception as exc:
                st.exception(exc)

    load_result = st.session_state.get("awr_daily_load_result")
    gc_result = st.session_state.get("awr_daily_gc_result")
    load_sql = st.session_state.get("awr_daily_load_sql")
    gc_sql = st.session_state.get("awr_daily_gc_sql")

    if load_result is not None and not load_result.empty:
        st.markdown("### Daily Load Snapshot")
        for _, row in load_result.iterrows():
            st.write(
                "- {} -> {}B execs | {}B calls | {}M commits".format(
                    row["day_key"],
                    format_human_number(row.get("execs_b", 0)),
                    format_human_number(row.get("calls_b", 0)),
                    format_human_number(row.get("commits_m", 0)),
                )
            )

        render_dataframe_chart_controls(
            load_result,
            key_prefix="daily_load",
            title="Daily Load Snapshot for DBID {}".format(selected_dbid),
        )
        st.dataframe(load_result, use_container_width=True)
        st.code(load_sql, language="sql")
    elif load_result is not None:
        st.info("Daily Load Snapshot returned no rows for the selected DBID and time window.")
        if load_sql:
            st.code(load_sql, language="sql")

    if gc_result is not None and not gc_result.empty:
        st.markdown("### Daily GC Congestion Trend")
        for _, row in gc_result.iterrows():
            st.write(
                "- {}: CR {}M waits / {}K s ({} ms) | Current {}M waits / {}K s ({} ms)".format(
                    row["day_key"],
                    format_human_number(row.get("cr_waits_m", 0)),
                    format_human_number(row.get("cr_waited_ks", 0)),
                    "{:.2f}".format(row.get("cr_avg_ms", 0) or 0),
                    format_human_number(row.get("cur_waits_m", 0)),
                    format_human_number(row.get("cur_waited_ks", 0)),
                    "{:.2f}".format(row.get("cur_avg_ms", 0) or 0),
                )
            )

        render_dataframe_chart_controls(
            gc_result,
            key_prefix="daily_gc",
            title="Daily GC Congestion for DBID {}".format(selected_dbid),
        )
        st.dataframe(gc_result, use_container_width=True)
        st.code(gc_sql, language="sql")
    elif gc_result is not None:
        st.info("Daily GC Congestion Trend returned no rows for the selected DBID and time window.")
        if gc_sql:
            st.code(gc_sql, language="sql")

with tab_top_sql:
    st.markdown(
        "Rank top `SQL_ID`s for `gc current block congested` using "
        "`DBA_HIST_ACTIVE_SESS_HISTORY`."
    )

    top_n = st.number_input(
        "Top N SQL_IDs",
        min_value=1,
        max_value=200,
        value=20,
        step=1,
    )

    if st.button("Run Top SQL_IDs for GC Current", use_container_width=True):
        if config is None:
            st.error("Connection details are incomplete.")
        else:
            try:
                top_sql = build_top_gc_sqlids_sql()
                top_sql_result = run_query(
                    config,
                    top_sql,
                    {
                        "dbid": selected_dbid,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                        "top_n": int(top_n),
                    },
                )
                st.session_state["awr_top_gc_sqlids_result"] = top_sql_result
                st.session_state["awr_top_gc_sqlids_sql"] = top_sql
            except Exception as exc:
                st.exception(exc)

    top_sql_result = st.session_state.get("awr_top_gc_sqlids_result")
    top_sql_sql = st.session_state.get("awr_top_gc_sqlids_sql")

    if top_sql_result is not None and not top_sql_result.empty:
        for _, row in top_sql_result.iterrows():
            st.write(
                "- {} | {} samples | {} waited_sec | {} avg_wait_ms | insts {}".format(
                    row["sql_id"],
                    format_human_number(row["samples"]),
                    format_human_number(row["waited_sec"]),
                    "{:.3f}".format(row["avg_wait_ms"]),
                    row["insts"],
                )
            )

        render_dataframe_chart_controls(
            top_sql_result,
            key_prefix="top_gc_sqlids",
            title="Top SQL_IDs for gc current block congested - DBID {}".format(selected_dbid),
        )
        st.dataframe(top_sql_result, use_container_width=True)
        st.code(top_sql_sql, language="sql")

with tab_sqlid_drill:
    st.markdown(
        "Drill into one `SQL_ID` across AWR history using `DBA_HIST_ACTIVE_SESS_HISTORY` "
        "and `DBA_HIST_SQLSTAT`."
    )
    drill_sql_id = st.text_input(
        "SQL_ID",
        value=sql_id_filter if 'sql_id_filter' in locals() else "",
        help="Enter the SQL_ID to inspect across service names, instances, events, and SQLSTAT metrics.",
    ).strip()

    if st.button("Run SQL_ID Drilldown", use_container_width=True):
        if config is None:
            st.error("Connection details are incomplete.")
        elif not drill_sql_id:
            st.error("Provide a SQL_ID.")
        else:
            try:
                ash_service = run_query(
                    config,
                    build_sqlid_service_sql(),
                    {
                        "dbid": selected_dbid,
                        "sql_id": drill_sql_id,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                    },
                )
                ash_events = run_query(
                    config,
                    build_sqlid_event_sql(),
                    {
                        "dbid": selected_dbid,
                        "sql_id": drill_sql_id,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                    },
                )
                sqlstat_columns = fetch_view_columns(config, "DBA_HIST_SQLSTAT")
                sqlstat = run_query(
                    config,
                    build_sqlid_sqlstat_sql(sqlstat_columns),
                    {
                        "dbid": selected_dbid,
                        "sql_id": drill_sql_id,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                    },
                )
                sqlstat = convert_datetime_columns(sqlstat)
                st.session_state["awr_drill_service"] = ash_service
                st.session_state["awr_drill_events"] = ash_events
                st.session_state["awr_drill_sqlstat"] = sqlstat
            except Exception as exc:
                st.exception(exc)

    ash_service = st.session_state.get("awr_drill_service")
    ash_events = st.session_state.get("awr_drill_events")
    sqlstat = st.session_state.get("awr_drill_sqlstat")

    if ash_service is not None and not ash_service.empty:
        st.markdown("### Distinct Service Names / Instances")
        st.dataframe(ash_service, use_container_width=True)
        render_dataframe_chart_controls(
            ash_service,
            key_prefix="sqlid_services",
            title="SQL_ID service distribution for {}".format(drill_sql_id or selected_dbid),
        )

    if ash_events is not None and not ash_events.empty:
        st.markdown("### ASH Event Mix")
        st.dataframe(ash_events, use_container_width=True)
        render_dataframe_chart_controls(
            ash_events,
            key_prefix="sqlid_events",
            title="SQL_ID event mix for {}".format(drill_sql_id or selected_dbid),
        )

    if sqlstat is not None and not sqlstat.empty:
        st.markdown("### SQLSTAT Time Series")
        st.dataframe(sqlstat, use_container_width=True)
        render_dataframe_chart_controls(
            sqlstat,
            key_prefix="sqlid_sqlstat",
            title="SQLSTAT profile for {}".format(drill_sql_id or selected_dbid),
        )

with tab_segments:
    st.markdown(
        "Rank top database segments by a selected segment statistic from "
        "`DBA_HIST_SEG_STAT` joined to `DBA_HIST_SEG_STAT_OBJ`."
    )
    seg_metric_columns = []
    if config is not None:
        try:
            seg_metric_columns = fetch_seg_stat_metric_columns(config)
        except Exception as exc:
            st.warning("Could not load segment metric columns automatically: {}".format(exc))

    seg_col1, seg_col2 = st.columns([2, 1])
    with seg_col1:
        if seg_metric_columns:
            default_metric = "logical_reads_delta"
            segment_metric = st.selectbox(
                "Segment Metric Column",
                options=seg_metric_columns,
                index=seg_metric_columns.index(default_metric) if default_metric in seg_metric_columns else 0,
                help="Start typing to filter available DBA_HIST_SEG_STAT metric columns.",
            )
        else:
            segment_metric = st.text_input("Segment Metric Column", value="logical_reads_delta").strip().lower()
    with seg_col2:
        segment_top_n = st.number_input("Top N Segments", min_value=1, max_value=200, value=20, step=1)

    if st.button("Run Top Segments", use_container_width=True):
        if config is None:
            st.error("Connection details are incomplete.")
        else:
            try:
                seg_columns = fetch_view_columns(config, "DBA_HIST_SEG_STAT")
                if segment_metric not in seg_columns:
                    st.error("Selected metric column does not exist in DBA_HIST_SEG_STAT: {}".format(segment_metric))
                    st.stop()
                segment_sql = build_top_segments_sql(segment_metric)
                segment_result = run_query(
                    config,
                    segment_sql,
                    {
                        "dbid": selected_dbid,
                        "from_ts": from_ts,
                        "to_ts": to_ts,
                        "top_n": int(segment_top_n),
                    },
                )
                st.session_state["awr_top_segments_result"] = segment_result
                st.session_state["awr_top_segments_sql"] = segment_sql
            except Exception as exc:
                st.exception(exc)

    segment_result = st.session_state.get("awr_top_segments_result")
    segment_sql = st.session_state.get("awr_top_segments_sql")

    if segment_result is not None and not segment_result.empty:
        st.dataframe(segment_result, use_container_width=True)
        render_dataframe_chart_controls(
            segment_result,
            key_prefix="top_segments",
            title="Top segments by {} for DBID {}".format(segment_metric, selected_dbid),
        )
        st.code(segment_sql, language="sql")
