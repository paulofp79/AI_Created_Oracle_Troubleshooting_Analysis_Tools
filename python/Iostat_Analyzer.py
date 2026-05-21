"""
Iostat_Analyzer.py - ExaWatcher iostat analysis tool

Streamlit app for uploading one or more iostat outputs collected by
ExaWatcher. The parser handles plain text, .bz2, and .xz files, then surfaces
device latency, utilization, queue depth, throughput, CPU iowait, and automatic
findings for common storage symptoms.
"""

from __future__ import annotations

import bz2
import io
import lzma
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st


MAX_UPLOAD_MB = 200


@dataclass(frozen=True)
class Finding:
    severity: str
    title: str
    detail: str
    recommendation: str
    source_file: str
    device: str = "all"
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class IostatMetadata:
    source_file: str
    starting_time: Optional[datetime]
    sample_interval: Optional[int]
    archive_count: Optional[int]
    collection_command: str
    host: str
    hard_disks: Tuple[str, ...]
    flash_disks: Tuple[str, ...]


def _decode_upload(name: str, data: bytes) -> str:
    suffixes = Path(name).suffixes
    try:
        if suffixes and suffixes[-1].lower() == ".xz":
            return lzma.decompress(data).decode("utf-8", errors="ignore")
        if suffixes and suffixes[-1].lower() == ".bz2":
            return bz2.decompress(data).decode("utf-8", errors="ignore")
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return data.decode("utf-8", errors="ignore")


def _parse_timestamp(value: str) -> Optional[datetime]:
    cleaned = value.strip().strip("<>").strip()
    formats = (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%H:%M:%S",
        "%I:%M:%S %p",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.year == 1900:
                return None
            return parsed
        except ValueError:
            continue
    return None


def _parse_time_only(value: str) -> Optional[datetime]:
    cleaned = value.strip()
    for fmt in ("%H:%M:%S", "%I:%M:%S %p"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _parse_header_timestamp(lines: Iterable[str]) -> Optional[datetime]:
    for raw in lines:
        line = raw.strip("# ").strip()
        if line.startswith("Starting Time:"):
            return _parse_timestamp(line.split(":", 1)[1])
    return None


def _parse_sample_interval(lines: Iterable[str]) -> Optional[int]:
    for raw in lines:
        line = raw.strip("# ").strip()
        if line.startswith("Sample Interval(s):"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _parse_int_header(lines: Iterable[str], key: str) -> Optional[int]:
    prefix = f"{key}:"
    for raw in lines:
        line = raw.strip("# ").strip()
        if line.startswith(prefix):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _parse_text_header(lines: Iterable[str], key: str) -> str:
    prefix = f"{key}:"
    for raw in lines:
        line = raw.strip("# ").strip()
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _device_name(path_or_name: str) -> str:
    return Path(path_or_name.strip()).name


def _parse_misc_devices(misc: str, label: str) -> Tuple[str, ...]:
    match = re.search(rf"{label}:\s*(.*?)(?:;\s*\w+:|$)", misc)
    if not match:
        return ()
    return tuple(_device_name(part) for part in match.group(1).split() if part.strip())


def _parse_host(lines: Iterable[str]) -> str:
    linux_re = re.compile(r"Linux\s+\S+\s+\((?P<host>[^)]+)\)")
    for raw in lines:
        match = linux_re.search(raw)
        if match:
            return match.group("host")
    return ""


def parse_iostat_metadata(text: str, source_file: str) -> IostatMetadata:
    lines = text.splitlines()
    misc = _parse_text_header(lines, "Misc Info")
    return IostatMetadata(
        source_file=source_file,
        starting_time=_parse_header_timestamp(lines),
        sample_interval=_parse_sample_interval(lines),
        archive_count=_parse_int_header(lines, "Archive Count"),
        collection_command=_parse_text_header(lines, "Collection Command"),
        host=_parse_host(lines),
        hard_disks=_parse_misc_devices(misc, "HardDisk"),
        flash_disks=_parse_misc_devices(misc, "FlashDisk"),
    )


def _classify_device(device: str, metadata: IostatMetadata) -> str:
    if device in metadata.hard_disks:
        return "Exadata hard disk"
    if device in metadata.flash_disks:
        return "Exadata flash disk"
    if re.fullmatch(r"md\d+", device):
        return "ASM md volume"
    if re.fullmatch(r"md\d+p\d+", device):
        return "md partition"
    if re.fullmatch(r"nvme\d+n\d+p\d+", device):
        return "NVMe partition"
    if re.fullmatch(r"nvme\d+n\d+", device):
        return "Other NVMe"
    if re.fullmatch(r"sd[a-z]+\d+", device):
        return "SCSI partition"
    if re.fullmatch(r"sd[a-z]+", device):
        return "Other SCSI disk"
    return "Other"


def _normalize_column(name: str) -> str:
    normalized = name.strip().lower().replace("%", "pct_")
    normalized = normalized.replace("/", "_per_").replace("-", "_")
    normalized = normalized.replace(".", "_")
    return re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")


def _coerce_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text == "-":
            return None
        return float(text)
    except ValueError:
        return None


def parse_iostat_text(text: str, source_file: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    lines = text.splitlines()
    metadata = parse_iostat_metadata(text, source_file)
    header_timestamp = metadata.starting_time
    sample_interval = metadata.sample_interval or 1
    current_timestamp: Optional[datetime] = None
    sample_number = 0

    cpu_records: List[Dict[str, object]] = []
    device_records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz\s*<(?P<ts>[^>]+)>", re.IGNORECASE)
    full_datetime_re = re.compile(
        r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}(?:\s+[AP]M)?$",
        re.IGNORECASE,
    )
    time_only_re = re.compile(r"^\d{1,2}:\d{2}:\d{2}(?:\s+[AP]M)?$", re.IGNORECASE)

    pending_device_headers: Optional[List[str]] = None
    pending_cpu_headers: Optional[List[str]] = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("Linux "):
            continue

        zzz_match = zzz_re.match(line)
        if zzz_match:
            parsed = _parse_timestamp(zzz_match.group("ts"))
            if parsed is not None:
                if current_timestamp != parsed:
                    sample_number += 1
                current_timestamp = parsed
            pending_device_headers = None
            pending_cpu_headers = None
            continue

        if full_datetime_re.match(line):
            parsed = _parse_timestamp(line)
            if parsed is not None:
                if current_timestamp != parsed:
                    sample_number += 1
                current_timestamp = parsed
            pending_device_headers = None
            pending_cpu_headers = None
            continue

        if time_only_re.match(line) and header_timestamp is not None:
            parsed_time = _parse_time_only(line)
            if parsed_time is None:
                continue
            parsed_timestamp = datetime.combine(header_timestamp.date(), parsed_time.time())
            if current_timestamp != parsed_timestamp:
                sample_number += 1
            current_timestamp = parsed_timestamp
            pending_device_headers = None
            pending_cpu_headers = None
            continue

        if line.startswith("avg-cpu:"):
            tokens = re.split(r"\s+", line.replace("avg-cpu:", "").strip())
            pending_cpu_headers = [_normalize_column(token) for token in tokens]
            continue

        if pending_cpu_headers:
            values = re.split(r"\s+", line)
            if len(values) >= len(pending_cpu_headers):
                record: Dict[str, object] = {
                    "timestamp": current_timestamp or header_timestamp,
                    "source_file": source_file,
                    "sample_number": sample_number,
                }
                for header, value in zip(pending_cpu_headers, values):
                    record[header] = _coerce_float(value)
                cpu_records.append(record)
                pending_cpu_headers = None
                continue

        if line.lower().startswith("device"):
            tokens = re.split(r"\s+", line)
            pending_device_headers = [_normalize_column(token) for token in tokens]
            continue

        if pending_device_headers:
            tokens = re.split(r"\s+", line)
            if len(tokens) >= len(pending_device_headers):
                if current_timestamp is None and header_timestamp is not None:
                    current_timestamp = header_timestamp + timedelta(
                        seconds=max(sample_number - 1, 0) * sample_interval
                    )
                record = {
                    "timestamp": current_timestamp,
                    "source_file": source_file,
                    "sample_number": sample_number,
                }
                for header, value in zip(pending_device_headers, tokens):
                    if header == "device":
                        record["device"] = value
                    else:
                        record[header] = _coerce_float(value)
                device_records.append(record)
                continue

    cpu_df = pd.DataFrame(cpu_records)
    device_df = pd.DataFrame(device_records)

    for df in (cpu_df, device_df):
        if not df.empty and "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df.sort_values(["timestamp", "source_file"], inplace=True)
            df.reset_index(drop=True, inplace=True)

    if not cpu_df.empty:
        cpu_df["host"] = metadata.host
        cpu_df["sample_interval"] = metadata.sample_interval
    if not device_df.empty:
        device_df["host"] = metadata.host
        device_df["device_class"] = device_df["device"].map(
            lambda value: _classify_device(str(value), metadata)
        )
        if {"r_per_s", "w_per_s"}.issubset(device_df.columns):
            device_df["iops"] = device_df["r_per_s"].fillna(0) + device_df["w_per_s"].fillna(0)
        if {"rmb_per_s", "wmb_per_s"}.issubset(device_df.columns):
            device_df["mb_per_s"] = device_df["rmb_per_s"].fillna(0) + device_df["wmb_per_s"].fillna(0)
        elif {"rkb_per_s", "wkb_per_s"}.issubset(device_df.columns):
            device_df["mb_per_s"] = (
                device_df["rkb_per_s"].fillna(0) + device_df["wkb_per_s"].fillna(0)
            ) / 1024

    return cpu_df, device_df


def _first_existing(frame: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def _active_device_rows(device_df: pd.DataFrame) -> pd.DataFrame:
    if device_df.empty:
        return device_df
    active = pd.Series(False, index=device_df.index)
    if "iops" in device_df.columns:
        active = active | (device_df["iops"].fillna(0) >= 1)
    if "mb_per_s" in device_df.columns:
        active = active | (device_df["mb_per_s"].fillna(0) >= 0.1)
    if "pct_util" in device_df.columns:
        active = active | (device_df["pct_util"].fillna(0) >= 1)
    return device_df[active].copy()


def _latency_thresholds(device_class: str) -> Tuple[float, float]:
    if device_class == "Exadata flash disk":
        return 5.0, 10.0
    if device_class in {"Exadata hard disk", "Other SCSI disk"}:
        return 25.0, 50.0
    if device_class == "ASM md volume":
        return 15.0, 30.0
    if device_class == "Other NVMe":
        return 5.0, 10.0
    return 25.0, 50.0


def _findings_for_data(cpu_df: pd.DataFrame, device_df: pd.DataFrame) -> List[Finding]:
    findings: List[Finding] = []

    if not cpu_df.empty:
        iowait_col = _first_existing(cpu_df, ("iowait", "pct_iowait", "wa"))
        idle_col = _first_existing(cpu_df, ("idle", "pct_idle", "id"))
        if iowait_col:
            hot_cpu = cpu_df[cpu_df[iowait_col].fillna(0) >= 20]
            if not hot_cpu.empty:
                peak = hot_cpu.sort_values(iowait_col, ascending=False).iloc[0]
                findings.append(
                    Finding(
                        severity="Critical",
                        title="High host CPU iowait",
                        detail=f"CPU iowait peaked at {peak[iowait_col]:.1f}%.",
                        recommendation=(
                            "Correlate with device await/utilization and Exadata cell "
                            "metrics for storage-side or fabric-side saturation."
                        ),
                        source_file=str(peak["source_file"]),
                        timestamp=peak.get("timestamp"),
                    )
                )
            warn_cpu = cpu_df[
                (cpu_df[iowait_col].fillna(0) >= 10)
                & (cpu_df[iowait_col].fillna(0) < 20)
            ]
            if not warn_cpu.empty:
                peak = warn_cpu.sort_values(iowait_col, ascending=False).iloc[0]
                findings.append(
                    Finding(
                        severity="Warning",
                        title="Elevated host CPU iowait",
                        detail=f"CPU iowait peaked at {peak[iowait_col]:.1f}%.",
                        recommendation=(
                            "Review the same time window in device latency and DB wait "
                            "events such as db file sequential/scattered read."
                        ),
                        source_file=str(peak["source_file"]),
                        timestamp=peak.get("timestamp"),
                    )
                )
        if idle_col:
            low_idle = cpu_df[cpu_df[idle_col].fillna(100) <= 5]
            if not low_idle.empty:
                peak = low_idle.iloc[0]
                findings.append(
                    Finding(
                        severity="Warning",
                        title="Very low CPU idle",
                        detail=f"CPU idle dropped to {peak[idle_col]:.1f}%.",
                        recommendation=(
                            "Check whether storage latency coincides with CPU pressure; "
                            "high CPU alone can make I/O response times appear worse."
                        ),
                        source_file=str(peak["source_file"]),
                        timestamp=peak.get("timestamp"),
                    )
                )

    if device_df.empty:
        return findings

    signal_df = _active_device_rows(device_df)
    if "device_class" in signal_df.columns:
        signal_df = signal_df[
            ~signal_df["device_class"].isin({"md partition", "NVMe partition", "SCSI partition"})
        ].copy()
    if signal_df.empty:
        return findings

    await_col = _first_existing(signal_df, ("await",))
    r_await_col = _first_existing(signal_df, ("r_await", "rwait"))
    w_await_col = _first_existing(signal_df, ("w_await", "wwait"))
    util_col = _first_existing(signal_df, ("pct_util", "util"))
    queue_col = _first_existing(signal_df, ("avgqu_sz", "aqu_sz"))

    for column, label in ((await_col, "overall"), (r_await_col, "read"), (w_await_col, "write")):
        if not column:
            continue
        grouped = signal_df.groupby(["source_file", "device", "device_class"], dropna=False)
        for (source_file, device, device_class), group in grouped:
            warning_threshold, critical_threshold = _latency_thresholds(str(device_class))
            p95 = group[column].dropna().quantile(0.95)
            if pd.isna(p95) or p95 < warning_threshold:
                continue
            peak = group.sort_values(column, ascending=False).iloc[0]
            severity = "Critical" if p95 >= critical_threshold else "Warning"
            findings.append(
                Finding(
                    severity=severity,
                    title=f"High {label} I/O latency on {device_class}",
                    detail=(
                        f"{device} {column} p95 was {p95:.1f} ms "
                        f"(peak {peak[column]:.1f} ms)."
                    ),
                    recommendation=(
                        "Correlate this device and timestamp with DB wait events, ASM, "
                        "cell disk/flash metrics, and storage server alerts."
                    ),
                    source_file=str(source_file),
                    device=str(device),
                    timestamp=peak.get("timestamp"),
                )
            )

    if util_col:
        saturated = signal_df[signal_df[util_col].fillna(0) >= 90]
        if not saturated.empty:
            peak = saturated.sort_values(util_col, ascending=False).iloc[0]
            findings.append(
                Finding(
                    severity="Warning",
                    title="High device utilization",
                    detail=f"{peak['device']} utilization reached {peak[util_col]:.1f}%.",
                    recommendation=(
                        "Sustained high utilization with high await usually indicates an "
                        "I/O bottleneck; short spikes may be normal."
                    ),
                    source_file=str(peak["source_file"]),
                    device=str(peak["device"]),
                    timestamp=peak.get("timestamp"),
                )
            )

    if queue_col:
        queued = signal_df[signal_df[queue_col].fillna(0) >= 4]
        if not queued.empty:
            peak = queued.sort_values(queue_col, ascending=False).iloc[0]
            findings.append(
                Finding(
                    severity="Warning",
                    title="High I/O queue depth",
                    detail=f"{peak['device']} queue depth reached {peak[queue_col]:.1f}.",
                    recommendation=(
                        "Queue growth plus high await points to saturation; compare with "
                        "throughput to see whether demand exceeded service capacity."
                    ),
                    source_file=str(peak["source_file"]),
                    device=str(peak["device"]),
                    timestamp=peak.get("timestamp"),
                )
            )

    severity_order = {"Critical": 0, "Warning": 1, "Info": 2}
    findings.sort(
        key=lambda item: (
            severity_order.get(item.severity, 99),
            item.timestamp or datetime.min,
            item.device,
        )
    )
    return findings


def _summary_by_device(device_df: pd.DataFrame) -> pd.DataFrame:
    if device_df.empty:
        return pd.DataFrame()

    aggregations: Dict[str, Tuple[str, str]] = {}
    for column in ("await", "r_await", "w_await", "pct_util", "util", "avgqu_sz", "aqu_sz"):
        if column in device_df.columns:
            aggregations[f"max_{column}"] = (column, "max")
            aggregations[f"avg_{column}"] = (column, "mean")
            aggregations[f"p95_{column}"] = (column, lambda series: series.quantile(0.95))
    for column in (
        "r_per_s",
        "w_per_s",
        "rmb_per_s",
        "wmb_per_s",
        "rkb_per_s",
        "wkb_per_s",
        "iops",
        "mb_per_s",
        "tps",
    ):
        if column in device_df.columns:
            aggregations[f"max_{column}"] = (column, "max")

    if not aggregations:
        return pd.DataFrame()

    return (
        device_df.groupby(["source_file", "device_class", "device"], dropna=False)
        .agg(**aggregations)
        .reset_index()
        .sort_values(list(aggregations.keys()), ascending=False)
    )


def _find_metric(frame: pd.DataFrame, label: str, candidates: Sequence[str]) -> Optional[str]:
    column = _first_existing(frame, candidates)
    if not column:
        st.info(f"{label} is not available in this iostat format.")
    return column


def _render_findings(findings: Sequence[Finding]) -> None:
    st.subheader("Automatic Findings")
    if not findings:
        st.success("No obvious iostat threshold findings detected in the loaded data.")
        return

    rows = []
    for item in findings:
        rows.append(
            {
                "severity": item.severity,
                "timestamp": item.timestamp,
                "source_file": item.source_file,
                "device": item.device,
                "finding": item.title,
                "detail": item.detail,
                "recommendation": item.recommendation,
            }
        )
    findings_df = pd.DataFrame(rows)
    st.dataframe(findings_df, use_container_width=True, hide_index=True)


def _render_chart(frame: pd.DataFrame, y_column: str, title: str, color: str = "device") -> None:
    chart_df = frame.dropna(subset=[y_column]).copy()
    if chart_df.empty:
        st.info(f"No rows available for {title}.")
        return
    x_column = "timestamp" if "timestamp" in chart_df.columns and chart_df["timestamp"].notna().any() else "sample_number"
    fig = px.line(
        chart_df,
        x=x_column,
        y=y_column,
        color=color if color in chart_df.columns else None,
        hover_data=["source_file", "device"] if "device" in chart_df.columns else ["source_file"],
        title=title,
    )
    fig.update_layout(legend_title_text=color.replace("_", " ").title())
    st.plotly_chart(fig, use_container_width=True)


st.set_page_config(
    page_title="ExaWatcher Iostat Analyzer",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ExaWatcher Iostat Analyzer")
st.caption(
    "Upload one or more ExaWatcher iostat command outputs to chart storage "
    "latency, throughput, queue depth, utilization, CPU iowait, and automatic findings."
)

with st.sidebar:
    st.header("Load Files")
    uploaded_files = st.file_uploader(
        "Iostat output files",
        type=["dat", "txt", "log", "out", "xz", "bz2"],
        accept_multiple_files=True,
        help="Supports plain text, .xz, and .bz2 ExaWatcher iostat captures.",
    )
    path_input = st.text_area(
        "Optional server-side file paths",
        value="",
        help="One path per line. Useful when the ExaWatcher files already exist on the app host.",
    )
    show_raw_rows = st.slider("Rows to show", 50, 5000, 500, 50)
    st.divider()
    st.header("Finding Thresholds")
    st.write(
        "Findings use class-aware thresholds and ignore inactive partition rows "
        "to reduce false positives from ExaWatcher -p output."
    )


local_paths = [Path(line.strip()).expanduser() for line in path_input.splitlines() if line.strip()]

if not uploaded_files and not local_paths:
    st.info("Upload one or more ExaWatcher iostat files, or provide server-side file paths, to begin.")
    st.stop()

cpu_parts: List[pd.DataFrame] = []
device_parts: List[pd.DataFrame] = []
metadata_rows: List[Dict[str, object]] = []
parse_errors: List[str] = []


def _process_file_payload(name: str, data: bytes, size_mb: float) -> None:
    if size_mb > MAX_UPLOAD_MB:
        parse_errors.append(f"{name}: skipped because it is {size_mb:.1f} MB.")
        return
    try:
        text = _decode_upload(name, data)
        metadata = parse_iostat_metadata(text, name)
        metadata_rows.append(
            {
                "source_file": metadata.source_file,
                "host": metadata.host,
                "starting_time": metadata.starting_time,
                "sample_interval": metadata.sample_interval,
                "archive_count": metadata.archive_count,
                "hard_disks": len(metadata.hard_disks),
                "flash_disks": len(metadata.flash_disks),
                "collection_command": metadata.collection_command,
            }
        )
        cpu_df, device_df = parse_iostat_text(text, name)
        if not cpu_df.empty:
            cpu_parts.append(cpu_df)
        if not device_df.empty:
            device_parts.append(device_df)
    except Exception as exc:
        parse_errors.append(f"{name}: {exc}")


for upload in uploaded_files:
    _process_file_payload(upload.name, upload.getvalue(), upload.size / 1024 / 1024)

for local_path in local_paths:
    if not local_path.exists() or not local_path.is_file():
        parse_errors.append(f"{local_path}: path not found or not a file.")
        continue
    _process_file_payload(
        str(local_path),
        local_path.read_bytes(),
        local_path.stat().st_size / 1024 / 1024,
    )

if parse_errors:
    with st.expander("Parse warnings", expanded=True):
        for error in parse_errors:
            st.warning(error)

cpu_df = pd.concat(cpu_parts, ignore_index=True) if cpu_parts else pd.DataFrame()
device_df = pd.concat(device_parts, ignore_index=True) if device_parts else pd.DataFrame()

if cpu_df.empty and device_df.empty:
    st.error("No iostat CPU or device rows were parsed from the uploaded files.")
    st.stop()

if not device_df.empty:
    classes = sorted(device_df["device_class"].dropna().unique().tolist())
    default_classes = [
        value
        for value in classes
        if value in {"Exadata hard disk", "Exadata flash disk", "ASM md volume"}
    ]
    selected_classes = st.sidebar.multiselect(
        "Device classes",
        options=classes,
        default=default_classes or classes,
    )
    class_scoped_df = (
        device_df[device_df["device_class"].isin(selected_classes)].copy()
        if selected_classes
        else device_df.copy()
    )
    devices = sorted(class_scoped_df["device"].dropna().unique().tolist())
    selected_devices = st.sidebar.multiselect(
        "Devices",
        options=devices,
        default=devices[: min(len(devices), 12)],
    )
    if selected_devices:
        filtered_device_df = class_scoped_df[class_scoped_df["device"].isin(selected_devices)].copy()
    else:
        filtered_device_df = class_scoped_df.copy()
else:
    filtered_device_df = device_df

st.subheader("Loaded Dataset")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Files", len(uploaded_files))
col2.metric("CPU Samples", len(cpu_df))
col3.metric("Device Samples", len(device_df))
col4.metric("Devices", int(device_df["device"].nunique()) if not device_df.empty else 0)

metadata_df = pd.DataFrame(metadata_rows)
if not metadata_df.empty:
    with st.expander("Capture Metadata", expanded=False):
        st.dataframe(metadata_df, use_container_width=True, hide_index=True)

findings = _findings_for_data(cpu_df, filtered_device_df)
_render_findings(findings)

tab_overview, tab_latency, tab_throughput, tab_cpu, tab_raw = st.tabs(
    ["Overview", "Latency & Utilization", "Throughput", "CPU", "Raw Data"]
)

with tab_overview:
    st.subheader("Per-Device Summary")
    summary = _summary_by_device(filtered_device_df)
    if summary.empty:
        st.info("No device summary metrics are available.")
    else:
        st.subheader("Device Classes")
        class_summary = (
            filtered_device_df.groupby("device_class", dropna=False)
            .agg(
                devices=("device", "nunique"),
                samples=("device", "size"),
                max_iops=("iops", "max") if "iops" in filtered_device_df.columns else ("device", "size"),
                max_mb_per_s=("mb_per_s", "max") if "mb_per_s" in filtered_device_df.columns else ("device", "size"),
                max_pct_util=("pct_util", "max") if "pct_util" in filtered_device_df.columns else ("device", "size"),
            )
            .reset_index()
        )
        st.dataframe(class_summary, use_container_width=True, hide_index=True)

        st.subheader("Per-Device Summary")
        st.dataframe(summary, use_container_width=True, hide_index=True)

with tab_latency:
    if filtered_device_df.empty:
        st.info("No device rows parsed.")
    else:
        await_col = _find_metric(filtered_device_df, "Await latency", ("await",))
        r_await_col = _first_existing(filtered_device_df, ("r_await", "rwait"))
        w_await_col = _first_existing(filtered_device_df, ("w_await", "wwait"))
        util_col = _first_existing(filtered_device_df, ("pct_util", "util"))
        queue_col = _first_existing(filtered_device_df, ("avgqu_sz", "aqu_sz"))

        if await_col:
            _render_chart(filtered_device_df, await_col, "Device Await Latency (ms)")
        if r_await_col:
            _render_chart(filtered_device_df, r_await_col, "Read Await Latency (ms)")
        if w_await_col:
            _render_chart(filtered_device_df, w_await_col, "Write Await Latency (ms)")
        if util_col:
            _render_chart(filtered_device_df, util_col, "Device Utilization (%)")
        if queue_col:
            _render_chart(filtered_device_df, queue_col, "Average Queue Depth")

with tab_throughput:
    if filtered_device_df.empty:
        st.info("No device rows parsed.")
    else:
        read_col = _first_existing(filtered_device_df, ("rmb_per_s", "rkb_per_s", "rkbs"))
        write_col = _first_existing(filtered_device_df, ("wmb_per_s", "wkb_per_s", "wkbs"))
        read_iops_col = _first_existing(filtered_device_df, ("r_per_s", "r_s", "rs"))
        write_iops_col = _first_existing(filtered_device_df, ("w_per_s", "w_s", "ws"))
        total_mb_col = _first_existing(filtered_device_df, ("mb_per_s",))
        total_iops_col = _first_existing(filtered_device_df, ("iops",))
        tps_col = _first_existing(filtered_device_df, ("tps",))

        if total_mb_col:
            _render_chart(filtered_device_df, total_mb_col, "Total Throughput (MB/s)")
        if read_col:
            unit = "MB/s" if read_col == "rmb_per_s" else "kB/s"
            _render_chart(filtered_device_df, read_col, f"Read Throughput ({unit})")
        if write_col:
            unit = "MB/s" if write_col == "wmb_per_s" else "kB/s"
            _render_chart(filtered_device_df, write_col, f"Write Throughput ({unit})")
        if total_iops_col:
            _render_chart(filtered_device_df, total_iops_col, "Total IOPS")
        if read_iops_col:
            _render_chart(filtered_device_df, read_iops_col, "Read IOPS")
        if write_iops_col:
            _render_chart(filtered_device_df, write_iops_col, "Write IOPS")
        if tps_col:
            _render_chart(filtered_device_df, tps_col, "Transfers Per Second")
        if not any((read_col, write_col, read_iops_col, write_iops_col, tps_col)):
            st.info("No throughput columns were found in this iostat output.")

with tab_cpu:
    if cpu_df.empty:
        st.info("No avg-cpu rows parsed.")
    else:
        st.dataframe(cpu_df.head(show_raw_rows), use_container_width=True, hide_index=True)
        for column in ("iowait", "pct_iowait", "user", "pct_user", "system", "pct_system", "idle", "pct_idle"):
            if column in cpu_df.columns:
                _render_chart(cpu_df, column, column.replace("_", " ").title(), color="source_file")

with tab_raw:
    st.subheader("Device Rows")
    if filtered_device_df.empty:
        st.info("No device rows parsed.")
    else:
        st.dataframe(
            filtered_device_df.head(show_raw_rows),
            use_container_width=True,
            hide_index=True,
        )
        csv_buffer = io.StringIO()
        filtered_device_df.to_csv(csv_buffer, index=False)
        st.download_button(
            "Download device rows as CSV",
            csv_buffer.getvalue(),
            file_name="iostat_device_rows.csv",
            mime="text/csv",
        )

    st.subheader("CPU Rows")
    if cpu_df.empty:
        st.info("No CPU rows parsed.")
    else:
        st.dataframe(cpu_df.head(show_raw_rows), use_container_width=True, hide_index=True)
