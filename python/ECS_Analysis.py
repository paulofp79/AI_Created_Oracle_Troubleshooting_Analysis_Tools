"""
ECS_Analysis.py - ECStatJSONExaWatcher Disk Metrics Analyzer

A Streamlit application for parsing and visualizing cell disk metrics
from ECStatJSONExaWatcher .dat files.

Author: Paulo Portugal - Oracle XTeam
Date: 05-Aug-2025
"""

import re
import json
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st
import plotly.express as px

# Constants
MAX_FILE_SIZE_MB = 200
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = ['.dat', '.json', '.txt', '.log']

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


# ----------------------------
# Main UI
# ----------------------------

uploaded = st.file_uploader(
    "Upload your .dat file (ECStatJSONExaWatcher)",
    type=["dat", "json", "txt", "log"],
    help=f"Maximum file size: {MAX_FILE_SIZE_MB}MB"
)

if not uploaded:
    st.info("Upload your .dat file to get started.")
    st.stop()

# Validate file
if not validate_file(uploaded):
    st.stop()

# Read file content
try:
    raw_text = uploaded.read().decode("utf-8", errors="ignore")
except UnicodeDecodeError as e:
    st.error(f"Could not decode file: {e}")
    st.stop()

# Parse sample interval
interval_sec = parse_sample_interval(raw_text, default_sec=5)

with st.expander("Header Details", expanded=False):
    st.write(f"Sample Interval (s): **{interval_sec}**")
    st.write(f"File size: **{len(raw_text) / 1024:.1f} KB**")

# Parse JSON blocks
with st.spinner("Parsing JSON blocks..."):
    blocks = json_blocks_from_dat(raw_text)

if not blocks:
    st.error("No JSON blocks found after 'zzz <...>' markers. Please check the file format.")
    st.stop()

st.success(f"Found {len(blocks)} JSON blocks")

# Convert to DataFrame
df = to_long_rows(blocks)

if df.empty:
    st.error("File parsed but no metrics found in 'stats' or 'IOReasons' sections.")
    st.stop()

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
    st.dataframe(plot_df, use_container_width=True)

    # CSV download button
    csv = plot_df.to_csv(index=False)
    st.download_button(
        label="Download as CSV",
        data=csv,
        file_name="ecstat_metrics.csv",
        mime="text/csv"
    )
