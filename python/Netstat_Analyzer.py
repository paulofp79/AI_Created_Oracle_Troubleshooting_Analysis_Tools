"""ExaWatcher netstat analyzer.

Streamlit application for compressed or plain netstat captures.  It reports
timestamp-aligned throughput, optional configured-capacity utilization, TCP/IP
counter deltas, interface errors/drops, and conservative correlation findings.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st

from netstat_analysis import (
    COUNTER_LABELS,
    MAX_FILE_SIZE_MB,
    build_intervals,
    counter_summary,
    decode_bytes,
    findings_for_intervals,
    interface_delta_rows,
    interface_summary,
    interval_rows,
    merge_samples,
    parse_text,
    sample_rows,
    throughput_summary,
)


st.set_page_config(
    page_title="ExaWatcher Netstat Analyzer",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ExaWatcher Netstat Analyzer")
st.caption(
    "Analyze ExaWatcher netstat output, including .xz archives, for peak traffic, "
    "capacity utilization, TCP retransmissions, timeouts, and interface errors/drops."
)


def _format_gbps(value: object) -> str:
    try:
        return f"{float(value):,.2f} Gbps"
    except (TypeError, ValueError):
        return "—"


def _format_timestamp(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _plot_if_available(
    frame: pd.DataFrame,
    y: str,
    title: str,
    color: str = "group",
) -> None:
    if frame.empty or y not in frame.columns or frame[y].dropna().empty:
        st.info(f"No {title.lower()} data was parsed.")
        return
    chart = frame.dropna(subset=[y]).copy()
    fig = px.line(
        chart,
        x="timestamp",
        y=y,
        color=color if color in chart.columns else None,
        hover_data=[column for column in ("source_file", "dt_seconds") if column in chart.columns],
        title=title,
    )
    fig.update_layout(legend_title_text=color.replace("_", " ").title())
    st.plotly_chart(fig, use_container_width=True)


with st.sidebar:
    st.header("Load Files")
    uploaded_files = st.file_uploader(
        "Netstat output files",
        type=["dat", "txt", "log", "out", "xz", "bz2"],
        accept_multiple_files=True,
        help="Supports plain text, .xz, and .bz2 ExaWatcher captures.",
    )
    path_input = st.text_area(
        "Optional server-side file paths",
        value="",
        help="One path per line. Useful when the ExaWatcher files already exist on the app host.",
    )
    max_gap_seconds = st.slider(
        "Maximum interval gap (seconds)",
        min_value=30,
        max_value=900,
        value=120,
        step=30,
        help="Larger gaps are skipped so a missing collection period is not reported as a low rate.",
    )
    aggregate_capacity_gbps = st.number_input(
        "Aggregate bidirectional capacity (Gbps)",
        min_value=0.0,
        max_value=2_000.0,
        value=0.0,
        step=1.0,
        help="Optional. Enter the capacity represented by the combined InOctets/OutOctets counters to calculate utilization percentage.",
    )
    show_raw_rows = st.slider("Rows to show", 50, 5000, 500, 50)


local_paths = [Path(line.strip()).expanduser() for line in path_input.splitlines() if line.strip()]
if not uploaded_files and not local_paths:
    st.info("Upload one or more ExaWatcher netstat files, or provide server-side file paths, to begin.")
    st.stop()


all_samples = []
metadata_rows: List[Dict[str, object]] = []
parse_warnings: List[str] = []
file_count = 0


def _process_payload(name: str, data: bytes) -> None:
    global file_count
    size_mb = len(data) / 1024 / 1024
    if size_mb > MAX_FILE_SIZE_MB:
        parse_warnings.append(f"{name}: skipped because it is {size_mb:.1f} MB.")
        return
    try:
        text = decode_bytes(name, data)
        metadata, samples = parse_text(text, name)
        file_count += 1
        all_samples.extend(samples)
        metadata_rows.append(
            {
                "source_file": metadata.source_file,
                "host": metadata.host or "(not detected)",
                "starting_time": metadata.starting_time,
                "sample_interval_seconds": metadata.sample_interval,
                "archive_count": metadata.archive_count,
                "samples_parsed": len(samples),
                "collection_command": metadata.collection_command,
            }
        )
        if not samples:
            parse_warnings.append(f"{name}: no timestamped netstat samples were parsed.")
    except Exception as exc:  # pragma: no cover - defensive UI boundary
        parse_warnings.append(f"{name}: {exc}")


for upload in uploaded_files:
    _process_payload(upload.name, upload.getvalue())

for local_path in local_paths:
    if not local_path.exists() or not local_path.is_file():
        parse_warnings.append(f"{local_path}: path not found or not a file.")
        continue
    try:
        _process_payload(str(local_path), local_path.read_bytes())
    except OSError as exc:
        parse_warnings.append(f"{local_path}: could not read file ({exc}).")


if parse_warnings:
    with st.expander("Parse warnings", expanded=True):
        for warning in parse_warnings:
            st.warning(warning)

samples = merge_samples(all_samples)
if not samples:
    st.error("No timestamped netstat samples were parsed from the supplied files.")
    st.stop()

intervals = build_intervals(samples, max_gap_seconds=max_gap_seconds)
interval_frame = pd.DataFrame(interval_rows(intervals, aggregate_capacity_gbps))
interface_frame = pd.DataFrame(interface_delta_rows(intervals))
sample_frame = pd.DataFrame(sample_rows(samples))

findings = findings_for_intervals(intervals, aggregate_capacity_gbps)
highest_severity = findings[0].severity if findings else "Info"
if highest_severity == "Critical":
    st.error("Conclusion: critical network-capacity or network-health evidence was detected; correlate before taking remediation.")
elif highest_severity == "Warning":
    st.warning("Conclusion: network counter or capacity findings were detected; correlate them with fabric and workload evidence.")
else:
    st.success("Conclusion: no obvious network counter or capacity finding was detected in the valid intervals.")


st.subheader("Capture Overview")
metric_1, metric_2, metric_3, metric_4 = st.columns(4)
metric_1.metric("Files parsed", file_count)
metric_2.metric("Unique samples", len(samples))
metric_3.metric("Valid rate intervals", len(intervals))
metric_4.metric("Hosts / groups", len({sample.group for sample in samples}))

if metadata_rows:
    with st.expander("Capture Metadata", expanded=False):
        st.dataframe(pd.DataFrame(metadata_rows), use_container_width=True, hide_index=True)


st.subheader("Automatic Findings")
finding_frame = pd.DataFrame(
    [
        {
            "severity": item.severity,
            "timestamp": item.timestamp,
            "group": item.group,
            "source_file": item.source_file,
            "finding": item.title,
            "detail": item.detail,
            "recommendation": item.recommendation,
        }
        for item in findings
    ]
)
if finding_frame.empty:
    st.success("No threshold-based network health or capacity findings were detected.")
else:
    st.dataframe(finding_frame, use_container_width=True, hide_index=True)


tab_overview, tab_throughput, tab_health, tab_interfaces, tab_raw = st.tabs(
    ["Overview", "Throughput", "TCP/IP Health", "Interfaces", "Raw Data"]
)

with tab_overview:
    if not intervals:
        st.info("No rate summary is available.")
    else:
        summary_frame = pd.DataFrame(throughput_summary(intervals))
        for column in ("min_mbps", "avg_mbps", "p95_mbps", "p99_mbps", "max_mbps"):
            if column in summary_frame:
                summary_frame[column] = summary_frame[column].round(2)
        st.subheader("Throughput Statistics")
        st.dataframe(summary_frame, use_container_width=True, hide_index=True)

        peak = max(intervals, key=lambda item: item.total_mbps)
        peak_1, peak_2, peak_3, peak_4 = st.columns(4)
        peak_1.metric("Peak total", _format_gbps(peak.total_mbps / 1_000))
        peak_2.metric("Peak inbound", _format_gbps(peak.in_mbps / 1_000))
        peak_3.metric("Peak outbound", _format_gbps(peak.out_mbps / 1_000))
        peak_4.metric("Peak timestamp", _format_timestamp(peak.timestamp))

        st.subheader("Counter Deltas")
        counters = pd.DataFrame(counter_summary(intervals))
        if counters.empty:
            st.info("No supported cumulative TCP/IP counters were available in the valid intervals.")
        else:
            st.dataframe(counters, use_container_width=True, hide_index=True)

with tab_throughput:
    if interval_frame.empty:
        st.info("No throughput intervals were parsed.")
    else:
        _plot_if_available(interval_frame, "in_gbps", "Inbound Throughput (Gbps)")
        _plot_if_available(interval_frame, "out_gbps", "Outbound Throughput (Gbps)")
        _plot_if_available(interval_frame, "total_gbps", "Total Bidirectional Throughput (Gbps)")
        if aggregate_capacity_gbps > 0:
            _plot_if_available(interval_frame, "utilization_pct", "Configured Capacity Utilization (%)")
        st.subheader("Top Throughput Intervals")
        top_frame = interval_frame.sort_values("total_gbps", ascending=False).head(20).copy()
        st.dataframe(top_frame, use_container_width=True, hide_index=True)

with tab_health:
    if interval_frame.empty:
        st.info("No health intervals were parsed.")
    else:
        _plot_if_available(interval_frame, "tcp_retransmission_pct", "TCP Retransmission Ratio (%)")
        health_columns = [
            "tcp_timeouts_delta",
            "udp_receive_errors_delta",
            "udp_receive_buffer_errors_delta",
            "ip_incoming_discarded_delta",
            "ip_route_drops_delta",
            "tcp_backlog_drops_delta",
        ]
        available_health = [column for column in health_columns if column in interval_frame.columns and interval_frame[column].fillna(0).sum() > 0]
        if available_health:
            health_long = interval_frame[["timestamp", "group"] + available_health].melt(
                id_vars=["timestamp", "group"], var_name="counter", value_name="delta"
            )
            health_long["counter"] = health_long["counter"].str.replace("_delta", "", regex=False).map(
                lambda key: COUNTER_LABELS.get(key, key)
            )
            fig = px.line(health_long, x="timestamp", y="delta", color="counter", line_group="group", title="Network Health Counter Deltas")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(health_long[health_long["delta"].fillna(0) > 0], use_container_width=True, hide_index=True)
        else:
            st.success("No positive timeout, UDP receive-error, IP discard/route-drop, or TCP backlog-drop deltas were observed.")

with tab_interfaces:
    summaries = pd.DataFrame(interface_summary(intervals))
    if summaries.empty:
        st.info("No interface table rows with deltas were parsed.")
    else:
        st.dataframe(summaries, use_container_width=True, hide_index=True)
        event_columns = [
            "rx_err_delta",
            "rx_drop_delta",
            "rx_ovr_delta",
            "tx_err_delta",
            "tx_drop_delta",
            "tx_ovr_delta",
        ]
        if not interface_frame.empty:
            available_events = [column for column in event_columns if column in interface_frame.columns]
            event_frame = interface_frame[["timestamp", "group", "interface"] + available_events].melt(
                id_vars=["timestamp", "group", "interface"], var_name="counter", value_name="delta"
            )
            event_frame = event_frame[event_frame["delta"].fillna(0) > 0]
            if not event_frame.empty:
                fig = px.bar(
                    event_frame,
                    x="timestamp",
                    y="delta",
                    color="counter",
                    facet_row="interface",
                    title="Interface Error/Drop Deltas",
                )
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(event_frame, use_container_width=True, hide_index=True)

with tab_raw:
    st.subheader("Timestamped Samples")
    st.dataframe(sample_frame.head(show_raw_rows), use_container_width=True, hide_index=True)
    sample_csv = io.StringIO()
    sample_frame.to_csv(sample_csv, index=False)
    st.download_button("Download samples as CSV", sample_csv.getvalue(), "netstat_samples.csv", "text/csv")

    st.subheader("Rate Intervals")
    if interval_frame.empty:
        st.info("No rate intervals were parsed.")
    else:
        st.dataframe(interval_frame.head(show_raw_rows), use_container_width=True, hide_index=True)
        interval_csv = io.StringIO()
        interval_frame.to_csv(interval_csv, index=False)
        st.download_button("Download intervals as CSV", interval_csv.getvalue(), "netstat_intervals.csv", "text/csv")

    st.subheader("Interface Deltas")
    if interface_frame.empty:
        st.info("No interface deltas were parsed.")
    else:
        st.dataframe(interface_frame.head(show_raw_rows), use_container_width=True, hide_index=True)
        interface_csv = io.StringIO()
        interface_frame.to_csv(interface_csv, index=False)
        st.download_button("Download interface deltas as CSV", interface_csv.getvalue(), "netstat_interface_deltas.csv", "text/csv")
