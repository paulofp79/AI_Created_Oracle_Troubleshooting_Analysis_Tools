"""
ECS_Analysis.py - ECStatJSONExaWatcher Disk Metrics Analyzer

A Streamlit application for parsing and visualizing cell disk metrics
from ECStatJSONExaWatcher .dat files.

Author: Paulo Portugal - Oracle XTeam
Date: 05-Aug-2025
"""

import re
import json
import lzma
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st
import plotly.express as px

# Constants
MAX_FILE_SIZE_MB = 200
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = ['.dat', '.json', '.txt', '.log', '.xz']

# Page configuration
st.set_page_config(page_title="ECStat Disk Charts (SD vs MD)", layout="wide")

st.title("ECStat Disk Charts (SD vs MD) - Paulo Portugal - XTeam")
st.caption("Robust parser for ECStatJSONExaWatcher .dat files → metric selection → aggregated chart by SD (HD) and MD (FD)")

# ----------------------------
# Regular Expression Patterns
# ----------------------------

HEADER_INTERVAL_RE = re.compile(r"#\s*Sample\s+Interval\(s\):\s*(\d+)")
MARKER_RE = re.compile(r"^zzz\s*<")


# ----------------------------
# Helper Functions
# ----------------------------

def parse_sample_interval(text: str, default_sec: int = 5) -> int:
    """
    Parse the sample interval from the file header.

    Args:
        text: Raw file content
        default_sec: Default interval if not found

    Returns:
        Sample interval in seconds
    """
    for line in text.splitlines():
        match = HEADER_INTERVAL_RE.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError as e:
                st.warning(f"Could not parse interval value: {e}")
                return default_sec
        if MARKER_RE.match(line):
            break
    return default_sec


def normalize_devices_from_block(block: Any) -> List[Dict]:
    """
    Normalize a JSON block to extract device list.

    Handles various input formats:
    - {"celldisk stats": [...]}
    - [...]
    - {...single device...}

    Args:
        block: Parsed JSON block (dict or list)

    Returns:
        List of device dictionaries
    """
    if isinstance(block, dict):
        if "celldisk stats" in block and isinstance(block["celldisk stats"], list):
            return block["celldisk stats"]
        # Single device case
        return [block]
    elif isinstance(block, list):
        return block
    else:
        return []


def json_blocks_from_dat(text: str) -> List[Dict]:
    """
    Extract JSON blocks from .dat file content.

    Parses content between 'zzz <...>' markers using brace/bracket
    counting to determine block boundaries.

    Args:
        text: Raw file content

    Returns:
        List of parsed JSON objects
    """
    lines = text.splitlines()
    started = False
    brace_depth = 0
    bracket_depth = 0
    collecting = False
    buf: List[str] = []
    blocks: List[Dict] = []

    def flush_buffer() -> Optional[Dict]:
        """Attempt to parse accumulated buffer as JSON."""
        nonlocal buf
        raw = "\n".join(buf).strip()
        buf = []

        if not raw:
            return None

        # Find the start of JSON content
        start_idx_obj = raw.find("{")
        start_idx_arr = raw.find("[")
        starts = [i for i in [start_idx_obj, start_idx_arr] if i != -1]

        if not starts:
            return None

        start = min(starts)
        raw = raw[start:]

        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try trimming trailing content
        last_brace = raw.rfind("}")
        last_bracket = raw.rfind("]")
        last_pos = max(last_brace, last_bracket)

        if last_pos != -1:
            try:
                return json.loads(raw[:last_pos + 1])
            except json.JSONDecodeError:
                return None

        return None

    for line in lines:
        if not started:
            if MARKER_RE.match(line):
                started = True
            continue

        if MARKER_RE.match(line):
            # New sample marker - flush previous block
            if collecting and brace_depth == 0 and bracket_depth == 0:
                blk = flush_buffer()
                if blk is not None:
                    blocks.append(blk)
            collecting = False
            brace_depth = 0
            bracket_depth = 0
            continue

        # Detect start of JSON
        if not collecting:
            if "{" in line or "[" in line:
                collecting = True
                brace_depth += line.count("{") - line.count("}")
                bracket_depth += line.count("[") - line.count("]")
                buf.append(line)
        else:
            brace_depth += line.count("{") - line.count("}")
            bracket_depth += line.count("[") - line.count("]")
            buf.append(line)

            if brace_depth == 0 and bracket_depth == 0:
                # Complete block
                blk = flush_buffer()
                if blk is not None:
                    blocks.append(blk)
                collecting = False

    # Handle EOF with incomplete block
    if collecting and brace_depth == 0 and bracket_depth == 0:
        blk = flush_buffer()
        if blk is not None:
            blocks.append(blk)

    return blocks


def to_long_rows(blocks: List[Dict]) -> pd.DataFrame:
    """
    Convert JSON blocks to a normalized DataFrame.

    Creates rows with columns:
    - timestamp: datetime object
    - ts_str: original timestamp string
    - dtype_label: device type (SD/MD)
    - category: stats/readsIOReasons/writesIOReasons
    - metric: metric name
    - iops_raw: raw IOPS value
    - bytes_raw: raw bytes value

    Args:
        blocks: List of parsed JSON blocks

    Returns:
        Pandas DataFrame with normalized data
    """
    rows = []

    for block in blocks:
        devices = normalize_devices_from_block(block)

        for dev in devices:
            ts_str = dev.get("timestampFormatted")

            if not ts_str:
                # Try timestamp in milliseconds
                ts_ms = dev.get("timestamp")
                if ts_ms:
                    try:
                        ts_str = datetime.utcfromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    except (ValueError, OSError) as e:
                        st.warning(f"Invalid timestamp value: {ts_ms}")
                        continue
                else:
                    continue

            try:
                ts = pd.to_datetime(ts_str)
            except (ValueError, pd.errors.ParserError):
                ts = ts_str

            # Map device type: FD=MD (flash), HD=SD (hard)
            dtype = dev.get("intendedDeviceType", "").upper()
            dtype_label = "MD" if dtype == "FD" else ("SD" if dtype == "HD" else dtype or "UNK")

            # Extract stats
            stats = dev.get("stats", {})
            for mname, obj in stats.items():
                if isinstance(obj, dict):
                    iops = obj.get("iops", 0) or 0
                    byts = obj.get("bytes", 0) or 0
                    rows.append([ts, ts_str, dtype_label, "stats", mname, int(iops), int(byts)])

            # Extract IOReasons
            io = dev.get("IOReasons", {})

            reads = io.get("readsIOReasons", {})
            for mname, obj in reads.items():
                if isinstance(obj, dict):
                    iops = obj.get("iops", 0) or 0
                    byts = obj.get("bytes", 0) or 0
                    rows.append([ts, ts_str, dtype_label, "readsIOReasons", mname, int(iops), int(byts)])

            writes = io.get("writesIOReasons", {})
            for mname, obj in writes.items():
                if isinstance(obj, dict):
                    iops = obj.get("iops", 0) or 0
                    byts = obj.get("bytes", 0) or 0
                    rows.append([ts, ts_str, dtype_label, "writesIOReasons", mname, int(iops), int(byts)])

    df = pd.DataFrame(
        rows,
        columns=["timestamp", "ts_str", "dtype_label", "category", "metric", "iops_raw", "bytes_raw"]
    )

    if df.empty:
        return df

    # Sort by timestamp
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def rate_from_cumulative(df_series: pd.Series, interval_sec: int) -> pd.Series:
    """
    Convert cumulative counters to rate per second.

    Uses delta calculation and clips negative values (rollover handling).

    Args:
        df_series: Series of cumulative values
        interval_sec: Sample interval in seconds

    Returns:
        Series of rate values (units per second)
    """
    delta = df_series.diff().fillna(0)
    delta = delta.clip(lower=0)
    return delta / max(1, interval_sec)


def build_metric_rate_frame(df: pd.DataFrame, interval_sec: int) -> pd.DataFrame:
    """
    Build per-metric rates across timestamp, device type, category, and metric.

    ECStat counters are cumulative, so this function converts them into rates
    using the ExaWatcher sample interval.
    """
    if df.empty:
        return pd.DataFrame()

    grouped = (
        df.groupby(["timestamp", "dtype_label", "category", "metric"], as_index=False)[
            ["iops_raw", "bytes_raw"]
        ]
        .sum()
        .sort_values(["dtype_label", "category", "metric", "timestamp"])
    )

    group_keys = ["dtype_label", "category", "metric"]
    grouped["iops_rate"] = (
        grouped.groupby(group_keys)["iops_raw"]
        .diff()
        .fillna(0)
        .clip(lower=0)
        / max(1, interval_sec)
    )
    grouped["mb_rate"] = (
        grouped.groupby(group_keys)["bytes_raw"]
        .diff()
        .fillna(0)
        .clip(lower=0)
        / max(1, interval_sec)
        / (1024 * 1024)
    )
    return grouped.reset_index(drop=True)


def _classify_metric(metric: str, category: str) -> Dict[str, str]:
    """
    Classify ECStat metrics into diagnostic buckets.

    The goal is not to declare root cause from one counter. It highlights
    counters that deserve attention and explains why.
    """
    text = f"{category} {metric}".lower()

    if any(token in text for token in ["error", "fail", "timeout", "corrupt"]):
        return {
            "severity": "Critical",
            "status": "Potential problem",
            "reason": "Error/failure-related counter is active.",
            "recommendation": "Correlate the timestamp with cell alert logs, disk state, and database wait events.",
        }

    if "rejected" in text or "reject" in text:
        return {
            "severity": "Warning",
            "status": "Watch",
            "reason": "Rejected cacheline/cell activity can indicate pressure or inefficient caching.",
            "recommendation": "Check whether rejected activity lines up with workload bursts, flash pressure, or memory pressure.",
        }

    if "limit dirty buffer writes" in text:
        return {
            "severity": "Warning",
            "status": "Watch",
            "reason": "Dirty buffer write limiting can indicate write pressure or checkpoint pressure.",
            "recommendation": "Compare with redo/write-heavy DB activity and flash/disk write latency.",
        }

    if "miss" in text or "misses" in text:
        return {
            "severity": "Info",
            "status": "Review",
            "reason": "Miss counters are workload-dependent but high sustained miss rates may reduce cache benefit.",
            "recommendation": "Compare miss activity with hit counters and SQL/workload windows before treating it as a fault.",
        }

    if "smart scan" in text or "backup" in text or "rebalance" in text:
        return {
            "severity": "Info",
            "status": "Workload signal",
            "reason": "High activity is usually workload-driven, not automatically a problem.",
            "recommendation": "Use as context for IO load attribution and correlate with DB jobs or ASM operations.",
        }

    return {
        "severity": "OK",
        "status": "No obvious issue",
        "reason": "No problem keyword detected; activity may be normal workload.",
        "recommendation": "Review trend only if it coincides with DB symptoms or storage latency.",
    }


@st.cache_data(show_spinner=False)
def analyze_ecstat_health(df: pd.DataFrame, interval_sec: int) -> Dict[str, pd.DataFrame]:
    """
    Produce health findings and a per-metric summary from parsed ECStat data.
    """
    rate_df = build_metric_rate_frame(df, interval_sec)
    if rate_df.empty:
        return {
            "rate_df": rate_df,
            "metric_summary": pd.DataFrame(),
            "findings": pd.DataFrame(),
        }

    summary = (
        rate_df.groupby(["dtype_label", "category", "metric"], as_index=False)
        .agg(
            max_iops=("iops_rate", "max"),
            avg_iops=("iops_rate", "mean"),
            max_mb_s=("mb_rate", "max"),
            avg_mb_s=("mb_rate", "mean"),
            active_samples=("iops_rate", lambda s: int((s > 0).sum())),
        )
        .sort_values(["max_iops", "max_mb_s"], ascending=False)
        .reset_index(drop=True)
    )

    classified_rows = []
    for row in summary.itertuples(index=False):
        cls = _classify_metric(str(row.metric), str(row.category))
        classified_rows.append(
            {
                "severity": cls["severity"],
                "status": cls["status"],
                "dtype": row.dtype_label,
                "category": row.category,
                "metric": row.metric,
                "max_iops_s": row.max_iops,
                "avg_iops_s": row.avg_iops,
                "max_mb_s": row.max_mb_s,
                "avg_mb_s": row.avg_mb_s,
                "active_samples": row.active_samples,
                "reason": cls["reason"],
                "recommendation": cls["recommendation"],
            }
        )

    metric_summary = pd.DataFrame(classified_rows)

    active_summary = metric_summary[
        (metric_summary["active_samples"] > 0)
        & (
            (metric_summary["max_iops_s"] > 0)
            | (metric_summary["max_mb_s"] > 0)
        )
    ].copy()

    severity_rank = {"Critical": 0, "Warning": 1, "Info": 2, "OK": 3}
    active_summary["severity_rank"] = active_summary["severity"].map(severity_rank).fillna(9)

    findings = active_summary[
        active_summary["severity"].isin(["Critical", "Warning", "Info"])
    ].copy()

    # Add an imbalance signal where one disk class dominates a metric.
    pivot = summary.pivot_table(
        index=["category", "metric"],
        columns="dtype_label",
        values="max_iops",
        aggfunc="max",
        fill_value=0,
    ).reset_index()
    if {"MD", "SD"}.issubset(pivot.columns):
        for row in pivot.itertuples(index=False):
            md_val = float(getattr(row, "MD"))
            sd_val = float(getattr(row, "SD"))
            bigger = max(md_val, sd_val)
            smaller = max(min(md_val, sd_val), 1.0)
            if bigger >= 100 and bigger / smaller >= 20:
                dominant = "MD" if md_val >= sd_val else "SD"
                findings = pd.concat(
                    [
                        findings,
                        pd.DataFrame(
                            [
                                {
                                    "severity": "Info",
                                    "status": "Skewed activity",
                                    "dtype": dominant,
                                    "category": row.category,
                                    "metric": row.metric,
                                    "max_iops_s": bigger,
                                    "avg_iops_s": 0.0,
                                    "max_mb_s": 0.0,
                                    "avg_mb_s": 0.0,
                                    "active_samples": 0,
                                    "reason": "One disk class dominates this metric.",
                                    "recommendation": "Confirm whether this aligns with expected flash/hard-disk placement and workload.",
                                    "severity_rank": severity_rank["Info"],
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )

    findings = (
        findings.sort_values(
            ["severity_rank", "max_iops_s", "max_mb_s"],
            ascending=[True, False, False],
        )
        .drop(columns=["severity_rank"], errors="ignore")
        .reset_index(drop=True)
    )
    metric_summary["severity_rank"] = metric_summary["severity"].map(severity_rank).fillna(9)
    metric_summary = (
        metric_summary.sort_values(
            ["severity_rank", "max_iops_s", "max_mb_s"],
            ascending=[True, False, False],
        )
        .drop(columns=["severity_rank"], errors="ignore")
        .reset_index(drop=True)
    )

    return {
        "rate_df": rate_df,
        "metric_summary": metric_summary,
        "findings": findings,
    }


def validate_file(uploaded_file) -> bool:
    """
    Validate uploaded file for type and size.

    Args:
        uploaded_file: Streamlit uploaded file object

    Returns:
        True if file is valid, False otherwise
    """
    if uploaded_file is None:
        return False

    # Check file size
    file_size = len(uploaded_file.getvalue())
    if file_size > MAX_FILE_SIZE_BYTES:
        st.error(f"File too large. Maximum size is {MAX_FILE_SIZE_MB}MB. Your file is {file_size / (1024*1024):.1f}MB.")
        return False

    # Check file extension
    file_name = uploaded_file.name.lower()
    if not any(file_name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        st.error(f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
        return False

    return True


def read_uploaded_text(uploaded_file) -> str:
    """
    Read uploaded file content as text.

    Supports plain text uploads and .xz-compressed inputs.
    """
    file_name = uploaded_file.name.lower()
    raw_bytes = uploaded_file.getvalue()

    if file_name.endswith(".xz"):
        try:
            raw_bytes = lzma.decompress(raw_bytes)
        except lzma.LZMAError as e:
            raise ValueError(f"Could not decompress XZ file {uploaded_file.name}: {e}")

    try:
        return raw_bytes.decode("utf-8", errors="ignore")
    except UnicodeDecodeError as e:
        raise ValueError(f"Could not decode file {uploaded_file.name}: {e}")


def read_uploaded_text_from_payload(file_name: str, raw_bytes: bytes) -> str:
    """
    Read payload bytes as text.

    Supports plain text payloads and .xz-compressed inputs.
    """
    file_name = file_name.lower()

    if file_name.endswith(".xz"):
        try:
            raw_bytes = lzma.decompress(raw_bytes)
        except lzma.LZMAError as e:
            raise ValueError(f"Could not decompress XZ file {file_name}: {e}")

    try:
        return raw_bytes.decode("utf-8", errors="ignore")
    except UnicodeDecodeError as e:
        raise ValueError(f"Could not decode file {file_name}: {e}")


@st.cache_data(show_spinner=False)
def parse_uploaded_payloads(file_payloads: List[tuple]):
    """
    Parse uploaded file payloads once and cache the result across Streamlit reruns.
    """
    raw_texts = []
    intervals = []
    file_details = []

    for file_name, raw_bytes in file_payloads:
        raw_text = read_uploaded_text_from_payload(file_name, raw_bytes)
        raw_texts.append(raw_text)
        intervals.append(parse_sample_interval(raw_text, default_sec=5))
        file_details.append({
            "name": file_name,
            "size_kb": len(raw_bytes) / 1024,
        })

    interval_sec = intervals[0] if intervals else 5
    combined_text = "\n".join(raw_texts)
    blocks = json_blocks_from_dat(combined_text)
    df = to_long_rows(blocks)

    return {
        "interval_sec": interval_sec,
        "intervals": intervals,
        "file_details": file_details,
        "blocks": blocks,
        "df": df,
    }


# ----------------------------
# Main UI
# ----------------------------

uploaded_files = st.file_uploader(
    "Upload one or more ECStatJSONExaWatcher files",
    type=["dat", "json", "txt", "log", "xz"],
    accept_multiple_files=True,
    help=f"Maximum file size: {MAX_FILE_SIZE_MB}MB"
)

if not uploaded_files:
    st.info("Upload one or more `.dat` or `.xz` files to get started.")
    st.stop()

for uploaded in uploaded_files:
    if not validate_file(uploaded):
        st.stop()

file_payloads = [(uploaded.name, uploaded.getvalue()) for uploaded in uploaded_files]

try:
    parsed_payload = parse_uploaded_payloads(file_payloads)
except ValueError as e:
    st.error(str(e))
    st.stop()

interval_sec = parsed_payload["interval_sec"]
intervals = parsed_payload["intervals"]
file_details = parsed_payload["file_details"]
blocks = parsed_payload["blocks"]
df = parsed_payload["df"]

with st.expander("Header Details", expanded=False):
    st.write(f"Sample Interval (s): **{interval_sec}**")
    st.write(f"Files loaded: **{len(uploaded_files)}**")
    st.dataframe(pd.DataFrame(file_details))
    if len(set(intervals)) > 1:
        st.warning(f"Detected mixed sample intervals across files: {sorted(set(intervals))}. Using {interval_sec}s for rate calculations.")

if not blocks:
    st.error("No JSON blocks found after 'zzz <...>' markers. Please check the file format.")
    st.stop()

st.success(f"Found {len(blocks)} JSON blocks")

if df.empty:
    st.error("File parsed but no metrics found in 'stats' or 'IOReasons' sections.")
    st.stop()

# Automatic health summary
health = analyze_ecstat_health(df, interval_sec)
findings_df = health["findings"]
metric_summary_df = health["metric_summary"]

st.subheader("Automatic ECStat Health Summary")

summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
summary_col1.metric("Metrics Checked", metric_summary_df["metric"].nunique() if not metric_summary_df.empty else 0)
summary_col2.metric("Critical", int((findings_df["severity"] == "Critical").sum()) if not findings_df.empty else 0)
summary_col3.metric("Warnings", int((findings_df["severity"] == "Warning").sum()) if not findings_df.empty else 0)
summary_col4.metric("Review Items", int((findings_df["severity"] == "Info").sum()) if not findings_df.empty else 0)

if findings_df.empty:
    st.success("No active ECStat counters matched the current automatic problem rules.")
else:
    st.caption(
        "These are signals, not final root cause. Use them to decide which counters "
        "and time windows deserve closer review."
    )
    st.dataframe(
        findings_df.head(100),
        use_container_width=True,
    )

with st.expander("Metric Health Summary - all counters", expanded=False):
    st.write(
        "Each parsed counter is classified by name and activity. "
        "Counters marked OK can still matter if they line up with database symptoms."
    )
    st.dataframe(
        metric_summary_df,
        use_container_width=True,
    )

# Metric selection UI
categories = ["stats", "readsIOReasons", "writesIOReasons", "ALL"]
col1, col2, col3 = st.columns([1, 2, 1])

with col1:
    chosen_category = st.selectbox("Category", categories, index=0)

if chosen_category == "ALL":
    metric_pool = (df["category"] + ":" + df["metric"]).unique()
else:
    filtered_df = df.loc[df["category"] == chosen_category]
    metric_pool = (filtered_df["category"] + ":" + filtered_df["metric"]).unique()

metric_pool = sorted(metric_pool)

with col2:
    chosen_metrics = st.multiselect(
        "Metrics (select multiple)",
        metric_pool,
        default=metric_pool[:1] if metric_pool else []
    )

with col3:
    unit = st.selectbox("Unit", ["IOPS/s", "MB/s"], index=0)

use_delta = st.checkbox(
    "Calculate as rate (delta/interval)",
    value=True,
    help="Convert cumulative counters to IOPS/s or MB/s using the Sample Interval from the header."
)

if not chosen_metrics:
    st.warning("Please select at least one metric.")
    st.stop()

# Build plot data
key_pairs = []
for k in chosen_metrics:
    if ":" in k:
        c, m = k.split(":", 1)
        key_pairs.append((c, m))
    else:
        key_pairs.append(("stats", k))

plot_rows = []

for (cat, met) in key_pairs:
    sdf = df[df["category"].eq(cat) & df["metric"].eq(met)]

    series_col = "iops_raw" if unit == "IOPS/s" else "bytes_raw"
    grp = sdf.groupby(["timestamp", "dtype_label"], as_index=False)[series_col].sum()

    # Ensure both SD and MD are present for all timestamps
    all_ts = sorted(grp["timestamp"].unique())

    for dtype in ["SD", "MD"]:
        sub = grp[grp["dtype_label"] == dtype].set_index("timestamp").reindex(all_ts).fillna(0)

        if use_delta:
            rate = rate_from_cumulative(sub[series_col], interval_sec)
            if unit == "MB/s":
                rate = rate / (1024 * 1024)  # bytes -> MB
            y = rate.values
        else:
            if unit == "MB/s":
                y = (sub[series_col] / (1024 * 1024)).values
            else:
                y = sub[series_col].values

        plot_rows.append(pd.DataFrame({
            "timestamp": all_ts,
            "dtype": dtype,
            "series": f"{cat}:{met}",
            "value": y
        }))

plot_df = pd.concat(plot_rows, ignore_index=True)

# Create chart
fig = px.line(
    plot_df,
    x="timestamp",
    y="value",
    color="dtype",
    line_dash="series" if len(key_pairs) > 1 else None,
    title=f"Metrics: {', '.join([f'{c}:{m}' for c,m in key_pairs])} ({unit})",
    labels={"value": unit, "timestamp": "Timestamp", "dtype": "Type (SD/MD)"},
)

fig.update_layout(
    template="plotly_dark",
    height=550,
    legend_title_text="Type / Series"
)

st.plotly_chart(fig, use_container_width=True)

# Optional data table
with st.expander("View aggregated data", expanded=False):
    st.dataframe(plot_df)

    # CSV download button
    csv = plot_df.to_csv(index=False)
    st.download_button(
        label="Download as CSV",
        data=csv,
        file_name="ecstat_metrics.csv",
        mime="text/csv"
    )
