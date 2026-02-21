"""
ExaWatcher_Streamlit.py - Interactive ExaWatcher Dataset Explorer

Streamlit frontend that wraps the reusable parsing utilities in
`exawatcher_framework.py`. Users can point the app at a directory
containing raw ExaWatcher collector outputs, explore the catalog, and
inspect parsed metrics for supported collectors (vmstat, iostat,
toppid, mpstat, top, ps, meminfo).

Author: Paulo Portugal - Oracle XTeam
Date: 21-Feb-2026
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

# Ensure the local module is importable when running from repo root
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from exawatcher_framework import COLLECTOR_PARSERS, ExaWatcherDataset


st.set_page_config(
    page_title="ExaWatcher Dataset Explorer",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ExaWatcher Dataset Explorer")
st.caption(
    "Streamlit interface for parsing raw Oracle ExaWatcher collector outputs. "
    "Point to an archive directory to inspect metrics without relying on the "
    "packaged HTML charts."
)


# -----------------------------------------------------------------------------
# Sidebar configuration
# -----------------------------------------------------------------------------

with st.sidebar:
    st.header("Configuration")

    default_root = Path(
        "/home/paportug/Exawatcher_Files/"
        "ExaWatcher_brtlvlts1891fu_2025-12-16_06_00_00_6h00m00s"
    )

    root_input = st.text_input(
        "ExaWatcher dataset directory",
        value=str(default_root) if default_root.exists() else "",
        help=(
            "Directory containing the ExaWatcher collector sub-folders (e.g. "
            "Vmstat.ExaWatcher, Iostat.ExaWatcher, Top.ExaWatcher)."
        ),
    )

    root_path = Path(root_input).expanduser() if root_input else None

    limit_rows = st.slider(
        "Row limit per section",
        min_value=50,
        max_value=5000,
        value=500,
        step=50,
        help="Max rows to render per collector section (prevents browser overload).",
    )


@st.cache_resource(show_spinner=False)
def load_dataset(root: Path) -> Optional[ExaWatcherDataset]:
    if not root.exists() or not root.is_dir():
        return None
    dataset = ExaWatcherDataset(root)
    dataset.build_index()
    return dataset


@st.cache_data(show_spinner=False)
def get_index_frame(dataset: ExaWatcherDataset) -> pd.DataFrame:
    return dataset.build_index().copy()


def _min_max_dates(index_df: pd.DataFrame) -> Optional[tuple[datetime, datetime]]:
    if index_df["start_time"].isnull().all():
        return None
    min_dt = index_df["start_time"].min()
    max_dt = index_df["start_time"].max()
    if pd.isna(min_dt) or pd.isna(max_dt):
        return None
    return min_dt.to_pydatetime(), max_dt.to_pydatetime()


def _filter_dataframe_by_time(
    frame: pd.DataFrame,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
) -> pd.DataFrame:
    if "timestamp" not in frame.columns:
        return frame
    result = frame
    if start_time is not None:
        result = result[result["timestamp"] >= start_time]
    if end_time is not None:
        result = result[result["timestamp"] <= end_time]
    return result


if root_path is None:
    st.warning("Provide an ExaWatcher directory in the sidebar to begin.")
    st.stop()

dataset = load_dataset(root_path)

if dataset is None:
    st.error(f"Path not found or not a directory: {root_path}")
    st.stop()

index_df = get_index_frame(dataset)

if index_df.empty:
    st.error("No collector files detected beneath the provided directory.")
    st.stop()


# -----------------------------------------------------------------------------
# Overview section
# -----------------------------------------------------------------------------

st.subheader("Collector Inventory")

counts = index_df["collector"].value_counts().sort_index()
summary_col1, summary_col2 = st.columns((2, 1))
with summary_col1:
    st.dataframe(
        counts.rename("files" ).to_frame(),
        use_container_width=True,
    )
with summary_col2:
    st.bar_chart(counts)

collector_options: List[str] = sorted(counts.index.tolist())

st.divider()


# -----------------------------------------------------------------------------
# Collector exploration
# -----------------------------------------------------------------------------

st.subheader("Collector Details")

collector = st.selectbox(
    "Choose collector",
    options=collector_options,
    format_func=lambda key: key.upper(),
)

collector_rows = index_df[index_df["collector"] == collector]
st.markdown(
    f"**Files**: {len(collector_rows)} | "
    f"Sample interval (median): {collector_rows['sample_interval'].median()} sec"
)
st.dataframe(
    collector_rows[
        [
            "start_time",
            "sample_interval",
            "sample_count",
            "path",
        ]
    ],
    use_container_width=True,
)

date_range = _min_max_dates(collector_rows)
start_filter: Optional[datetime] = None
end_filter: Optional[datetime] = None

if date_range:
    min_dt, max_dt = date_range
    with st.expander("Time window filter", expanded=False):
        col_start, col_end = st.columns(2)
        with col_start:
            start_input = st.date_input(
                "Start date",
                value=min_dt.date(),
                min_value=min_dt.date(),
                max_value=max_dt.date(),
                key="start_date",
            )
            start_time_input = st.time_input(
                "Start time",
                value=min_dt.time(),
                key="start_time",
            )
            start_filter = datetime.combine(start_input, start_time_input)
        with col_end:
            end_input = st.date_input(
                "End date",
                value=max_dt.date(),
                min_value=min_dt.date(),
                max_value=max_dt.date(),
                key="end_date",
            )
            end_time_input = st.time_input(
                "End time",
                value=max_dt.time(),
                key="end_time",
            )
            end_filter = datetime.combine(end_input, end_time_input)
        if start_filter > end_filter:
            st.warning("Start time exceeds end time — filters will be ignored.")
            start_filter = end_filter = None


@st.cache_data(show_spinner=True)
def load_collector_sections(
    root: str,
    collector_name: str,
) -> Dict[str, pd.DataFrame]:
    dataset_local = ExaWatcherDataset(Path(root))
    dataset_local.build_index()
    return dataset_local.load_collector(collector_name)


sections = load_collector_sections(str(root_path), collector)

if not sections:
    st.info("No parsed rows returned for this collector (maybe unsupported parser).")
    st.stop()

tab_labels = list(sections.keys())
tabs = st.tabs([label.upper() for label in tab_labels])

for tab, section_name in zip(tabs, tab_labels):
    with tab:
        frame = sections[section_name]
        filtered = _filter_dataframe_by_time(frame, start_filter, end_filter)
        if filtered.empty:
            st.warning("No rows in the selected time window.")
            continue

        st.write(f"Rows: {len(filtered)}")
        if len(filtered) > limit_rows:
            st.info(
                f"Displaying first {limit_rows} rows; adjust the sidebar limit to view more."
            )
        display_df = filtered.head(limit_rows)
        st.dataframe(display_df, use_container_width=True)

        csv_export = display_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_export,
            file_name=f"{collector}_{section_name}.csv",
            mime="text/csv",
        )

        numeric_cols = [col for col in display_df.columns if pd.api.types.is_numeric_dtype(display_df[col])]
        if numeric_cols:
            default_y = numeric_cols[: min(3, len(numeric_cols))]
            with st.expander("Quick chart", expanded=False):
                y_axes = st.multiselect(
                    "Numeric columns to plot",
                    options=numeric_cols,
                    default=default_y,
                    key=f"chart_{collector}_{section_name}",
                )
                if y_axes:
                    chart_df = display_df[["timestamp", *y_axes]].copy()
                    chart_df.set_index("timestamp", inplace=True)
                    st.line_chart(chart_df)

st.success("Loaded collector data successfully.")

