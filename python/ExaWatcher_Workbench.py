"""Streamlit workbench for the ExaWatcher outputs without a dedicated analyzer."""

from pathlib import Path
import inspect
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st

from echarts_component import render_echarts
from exawatcher_analysis import (
    CATEGORIES,
    MAX_FILE_BYTES,
    analyze_payloads,
    bytes_for_supported_file,
    metric_columns,
)


PORT = 8505
MAX_TABLE_ROWS = 5000
DISPLAY_NAMES = {"Acfs": "ACFS", "EdvStat": "EDVStat"}


def _category_folder(root: Path, category: str) -> Path:
    expected = f"{category}.ExaWatcher"
    if root.name.lower() == expected.lower():
        return root
    candidate = root / expected
    if candidate.is_dir():
        return candidate
    return root


def _read_folder(folder: Path, category: str) -> Tuple[List[Tuple[str, bytes]], List[str]]:
    payloads: List[Tuple[str, bytes]] = []
    warnings: List[str] = []
    if not folder.exists() or not folder.is_dir():
        return payloads, [f"{folder}: directory not found."]
    if folder.name.lower() != f"{category}.exawatcher".lower():
        return payloads, [f"{folder}: expected a {category}.ExaWatcher directory or its capture root."]
    for path in sorted(folder.iterdir()):
        if not bytes_for_supported_file(path):
            continue
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                warnings.append(f"{path.name}: skipped because it exceeds the 200 MiB per-file limit.")
                continue
            payloads.append((path.name, path.read_bytes()))
        except OSError as exc:
            warnings.append(f"{path.name}: could not be read: {exc}")
    if not payloads and not warnings:
        warnings.append(f"{folder}: no plain .dat, .dat.xz, or .dat.bz2 files found.")
    return payloads, warnings


def _series_option(frame: pd.DataFrame, metric: str, entities: List[str]) -> Dict[str, object]:
    chart = frame[frame["entity"].astype(str).isin(entities)].copy()
    chart = chart[chart[metric].notna()].sort_values("timestamp")
    series = []
    for entity, group in chart.groupby(chart["entity"].astype(str), sort=True):
        group = group.dropna(subset=["timestamp", metric])
        if group.empty:
            continue
        entry = {
            "name": entity,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                [stamp.isoformat(), float(value)]
                for stamp, value in zip(group["timestamp"], group[metric])
            ],
        }
        if len(group) > 1500:
            entry["sampling"] = "lttb"
        series.append(entry)
    return {
        "animation": False,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {"type": "scroll", "top": 8},
        "toolbox": {
            "right": 12,
            "feature": {
                "dataZoom": {"yAxisIndex": "none"},
                "restore": {},
                "saveAsImage": {"pixelRatio": 2},
            },
        },
        "grid": {"left": 78, "right": 26, "top": 62, "bottom": 74, "containLabel": True},
        "dataZoom": [
            {"type": "inside", "xAxisIndex": 0},
            {"type": "slider", "xAxisIndex": 0, "height": 18, "bottom": 20},
        ],
        "xAxis": {"type": "time", "name": "Timestamp", "axisLabel": {"hideOverlap": True}},
        "yAxis": {
            "type": "value",
            "name": metric.replace("_", " "),
            "scale": True,
            "splitLine": {"lineStyle": {"color": "#334155"}},
        },
        "series": series,
    }


def _csv_button(label: str, frame: pd.DataFrame, filename: str, key: str) -> None:
    if frame.empty:
        return
    csv_data = frame.to_csv(index=False).encode("utf-8")
    st.download_button(label, csv_data, filename, "text/csv", key=key)


def _show_dataframe(frame: pd.DataFrame) -> None:
    parameters = inspect.signature(st.dataframe).parameters
    kwargs = {}
    if "use_container_width" in parameters:
        kwargs["use_container_width"] = True
    if "hide_index" in parameters:
        kwargs["hide_index"] = True
    st.dataframe(frame, **kwargs)


def _session_analysis(category: str, payloads: List[Tuple[str, bytes]]):
    key = (category, tuple((name, len(payload), hash(payload)) for name, payload in payloads))
    if st.session_state.get("_exawatcher_workbench_key") != key:
        st.session_state["_exawatcher_workbench_result"] = analyze_payloads(category, payloads)
        st.session_state["_exawatcher_workbench_key"] = key
    return st.session_state["_exawatcher_workbench_result"]


st.set_page_config(
    page_title="ExaWatcher Analyzer Workbench",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ExaWatcher Analyzer Workbench")
st.caption(
    "Analyze the remaining ExaWatcher collectors with per-output views, "
    "coverage-aware charts, searchable details, and CSV downloads."
)

with st.sidebar:
    st.header("Choose an output")
    category = st.selectbox(
        "Collector output",
        list(CATEGORIES),
        format_func=lambda value: f"{DISPLAY_NAMES.get(value, value)} — {CATEGORIES[value]}",
    )
    st.markdown("---")
    st.header("Load capture")
    capture_root = st.text_input(
        "Capture root or category folder",
        value="",
        help=(
            "Enter the capture directory or its matching "
            f"{category}.ExaWatcher subdirectory on the app host."
        ),
    )
    uploads = st.file_uploader(
        "Or upload matching output files",
        type=["dat", "xz", "bz2"],
        accept_multiple_files=True,
        help="Supports plain .dat, .dat.xz, and .dat.bz2 files.",
    )
    row_limit = st.slider("Rows shown in tables", 100, 5000, 1000, 100)

payloads: List[Tuple[str, bytes]] = []
input_warnings: List[str] = []
if capture_root.strip():
    root = Path(capture_root.strip()).expanduser()
    folder = _category_folder(root, category)
    local_payloads, local_warnings = _read_folder(folder, category)
    payloads.extend(local_payloads)
    input_warnings.extend(local_warnings)
if uploads:
    payloads.extend((upload.name, upload.getvalue()) for upload in uploads)

if not payloads:
    for warning in input_warnings:
        st.warning(warning)
    st.info(
        "Enter a capture root or matching category folder on the app host, "
        "or upload one or more files to begin."
    )
    st.stop()

with st.spinner(f"Reading {category} files..."):
    result = _session_analysis(category, payloads)

all_warnings = input_warnings + result.warnings
if all_warnings:
    with st.expander(f"Coverage and parsing notes ({len(all_warnings)})", expanded=True):
        for warning in all_warnings:
            st.warning(warning)

records = result.records
details = result.details
metadata = result.metadata

start = records["timestamp"].min() if not records.empty and "timestamp" in records else pd.NaT
end = records["timestamp"].max() if not records.empty and "timestamp" in records else pd.NaT
entity_count = int(records["entity"].nunique()) if not records.empty and "entity" in records else 0

st.subheader(f"{DISPLAY_NAMES.get(category, category)} — {CATEGORIES[category]}")
summary_cols = st.columns(5)
summary_cols[0].metric("Files loaded", len(metadata))
summary_cols[1].metric("Parsed rows", len(records))
summary_cols[2].metric("Entities", entity_count)
summary_cols[3].metric("First sample", start.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(start) else "Unavailable")
summary_cols[4].metric("Last sample", end.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(end) else "Unavailable")

with st.expander("Capture metadata", expanded=False):
    if metadata.empty:
        st.info("No ExaWatcher metadata headers were found.")
    else:
        _show_dataframe(metadata)

if not metadata.empty:
    coverage_columns = [
        "source_file", "starting_time", "first_observed_sample", "last_observed_sample",
        "sample_interval_seconds", "archive_count", "observed_samples",
    ]
    coverage_columns = [column for column in coverage_columns if column in metadata.columns]
    coverage = metadata[coverage_columns].copy()
    if {"archive_count", "observed_samples"}.issubset(coverage.columns):
        coverage["sample_coverage_pct"] = coverage.apply(
            lambda row: (
                100 * row["observed_samples"] / row["archive_count"]
                if pd.notna(row["archive_count"]) and row["archive_count"] > 0
                and pd.notna(row["observed_samples"])
                else None
            ),
            axis=1,
        )
    st.subheader("File coverage")
    _show_dataframe(coverage)

view_records = records
if category in {"Ps", "Top", "TopPid"} and not records.empty:
    st.subheader("Process filters and ranking")
    filter_cols = st.columns(3)
    selected_states = []
    if "state" in records:
        states = sorted(records["state"].dropna().astype(str).unique().tolist())
        selected_states = filter_cols[0].multiselect("Process states", states, default=states)
    process_query = filter_cols[1].text_input("Process or command contains", key=f"process-search-{category}").strip()
    ranked_fields = [field for field in ("cpu_pct", "cpu_c", "mem_pct", "rss_mib", "resident_mib") if field in records]
    rank_field = filter_cols[2].selectbox(
        "Rank by",
        ranked_fields,
        format_func=lambda value: value.replace("_", " "),
    ) if ranked_fields else None
    view_records = records
    if "state" in view_records:
        view_records = view_records[view_records["state"].astype(str).isin(selected_states)]
    if process_query:
        command_text = view_records.get("command", view_records["entity"]).astype(str)
        view_records = view_records[command_text.str.contains(process_query, case=False, na=False, regex=False)]
    if rank_field:
        top_count = st.slider("Ranked rows", 10, 200, 50, 10, key=f"rank-count-{category}")
        ranked = view_records.dropna(subset=[rank_field]).sort_values(rank_field, ascending=False).head(top_count)
        if category == "Ps" and rank_field == "cpu_c":
            st.caption("The Ps collector provides the raw `C` field; it is shown as reported and not relabeled as a CPU percentage.")
        _show_dataframe(ranked)
    else:
        st.info("No numeric process ranking fields were recognized.")

metrics = metric_columns(records) if not records.empty else []
if metrics:
    st.subheader("Metric trends")
    preferred = {
        "Diskinfo": "read_iops",
        "Meminfo": "mem_available_pct",
        "Mpstat": "busy_pct",
        "Nfsiostat": "read_avg_exec_ms",
        "Rocestat": "port_rcv_data_gbps",
        "Acfs": "used_pct",
        "EdvStat": "iowait_pct",
        "IBCardInfo": "qp",
        "IBprocs": "cm_rx",
        "Numa": "numastat_mib_numa_hit",
        "Top": "cpu_us_pct",
        "TopPid": "cpu_pct",
        "Lsof": "open_fd_count",
        "Ps": "rss_mib",
    }.get(category)
    default_metric = preferred if preferred in metrics else metrics[0]
    metric = st.selectbox(
        "Metric",
        metrics,
        index=metrics.index(default_metric),
        format_func=lambda value: value.replace("_", " "),
    )
    entity_source = view_records if not view_records.empty else records
    entity_count_for_chart = int(entity_source["entity"].nunique())
    if entity_count_for_chart > 500 and metric in entity_source:
        entity_rank = entity_source.groupby("entity", observed=True)[metric].max().dropna().sort_values(ascending=False)
        ranked_entities = entity_rank.head(500).index.astype(str).tolist()
        if "system" in entity_source["entity"].astype(str).values and "system" not in ranked_entities:
            ranked_entities.append("system")
        entity_values = sorted(set(ranked_entities))
        st.caption(f"The entity picker is limited to the top 500 entities by maximum {metric} value.")
    else:
        entity_values = sorted(entity_source["entity"].dropna().astype(str).unique().tolist())
        ranked_entities = entity_values
    if "all" in entity_values:
        default_entities = ["all"]
    elif "system" in entity_values:
        default_entities = ["system"]
    elif entity_count_for_chart > 500:
        default_entities = ranked_entities[:8]
    else:
        default_entities = entity_values[: min(8, len(entity_values))]
    selected_entities = st.multiselect(
        "Entities",
        options=entity_values,
        default=default_entities,
        help="Select devices, CPUs, mounts, adapters, processes, or nodes to chart.",
    )
    if selected_entities:
        chart_frame = view_records[view_records["entity"].astype(str).isin(selected_entities)]
        option = _series_option(chart_frame, metric, selected_entities)
        if option["series"]:
            render_echarts(option, height=510)
            chart_rows = chart_frame[["timestamp", "entity", "source_file", metric]].copy()
            _csv_button(
                "Download selected chart data as CSV",
                chart_rows,
                f"{category.lower()}_{metric}.csv",
                f"chart-{category}-{metric}",
            )
        else:
            st.info("The selected entities do not have numeric values for this metric.")
    else:
        st.info("Select at least one entity to draw the chart.")
elif records.empty and details.empty:
    st.error("No rows were recognized in the selected files. Review the parsing notes above.")
else:
    st.info("This output is primarily a snapshot or text report; use its searchable details below.")

if not records.empty:
    st.subheader("Parsed metrics and records")
    _show_dataframe(view_records.head(row_limit))
    if len(records) <= 500_000:
        _csv_button("Download parsed records as CSV", view_records, f"{category.lower()}_records.csv", f"records-{category}")
    else:
        st.caption(
            "The full parsed table is large. Use the selected chart-data export "
            "to download a focused time series."
        )

if not details.empty:
    st.subheader("Details and snapshot report")
    search = st.text_input("Filter detail rows", key=f"search-{category}")
    searchable = [column for column in details.columns if column not in {"timestamp", "source_file"}]
    preferred_search = (
        "name" if category == "Lsof" and "name" in searchable
        else "detail" if "detail" in searchable
        else searchable[0] if searchable
        else ""
    )
    search_field = st.selectbox(
        "Search field",
        ["All fields"] + searchable,
        index=1 + searchable.index(preferred_search),
        key=f"search-field-{category}",
    ) if searchable else "All fields"
    visible = details
    if search.strip():
        if search_field == "All fields":
            mask = pd.Series(False, index=visible.index)
            for column in searchable:
                mask |= visible[column].astype(str).str.contains(search.strip(), case=False, na=False, regex=False)
        else:
            mask = visible[search_field].astype(str).str.contains(search.strip(), case=False, na=False, regex=False)
        visible = visible[mask]
    st.caption(f"Showing {min(len(visible), row_limit):,} of {len(visible):,} matching rows.")
    _show_dataframe(visible.head(row_limit))
    _csv_button(
        "Download detail report as CSV",
        visible,
        f"{category.lower()}_details.csv",
        f"details-{category}",
    )
