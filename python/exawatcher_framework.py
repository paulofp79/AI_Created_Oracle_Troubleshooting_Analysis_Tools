"""
exawatcher_framework.py
========================

Utility helpers for indexing and parsing Oracle ExaWatcher raw collector
outputs. The script can be imported as a library or executed directly to
inspect datasets. Current focus is on two high-signal collectors:

* VmstatExaWatcher  → run queue, swap and CPU split metrics
* IostatExaWatcher  → CPU summary plus per-device latency and throughput

The code is intentionally modular so additional collectors can be wired in by
dropping new parser functions into the `COLLECTOR_PARSERS` registry.
"""

import argparse
import bz2
import lzma
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import pandas as pd


###############################################################################
# Utility helpers
###############################################################################


_FILENAME_TS_RE = re.compile(
    r"(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})"
)


def _open_text_file(path: Path) -> Iterator[str]:
    """Yield decoded lines from a possibly compressed ExaWatcher file."""

    suffix = path.suffix.lower()
    if suffix == ".bz2":
        opener = bz2.open  # type: ignore[assignment]
    elif suffix == ".xz":
        opener = lzma.open  # type: ignore[assignment]
    else:
        def opener(p: Path, mode: str):  # type: ignore[override]
            return open(p, mode, encoding="utf-8", errors="ignore")

    with opener(path, "rt") as handle:  # type: ignore[arg-type]
        for line in handle:
            yield line.rstrip("\n")


def _split_header_body(lines: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Split the raw lines into header metadata and body payload."""

    header: List[str] = []
    body: List[str] = []
    seen_body = False

    for line in lines:
        if not seen_body and (
            line.startswith("#") or line.startswith("###") or not line.strip()
        ):
            header.append(line)
        else:
            seen_body = True
            body.append(line)

    return header, body


def _parse_header_map(header_lines: Iterable[str]) -> Dict[str, str]:
    """Convert ExaWatcher header lines into a normalized mapping."""

    result: Dict[str, str] = {}
    for line in header_lines:
        stripped = line.strip("# ")
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def _timestamp_from_header(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S %p"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _timestamp_from_filename(path: Path) -> Optional[datetime]:
    match = _FILENAME_TS_RE.search(path.name)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items()}
    return datetime(
        parts["year"],
        parts["month"],
        parts["day"],
        parts["hour"],
        parts["minute"],
        parts["second"],
    )


###############################################################################
# Dataset index
###############################################################################


@dataclass(frozen=True)
class CollectorFile:
    path: Path
    collector: str
    module: Optional[str]
    start_time: Optional[datetime]
    sample_interval: Optional[int]
    sample_count: Optional[int]
    compression: str
    command: Optional[str]


class ExaWatcherDataset:
    """Catalog ExaWatcher archives and provide typed loaders per collector."""

    def __init__(self, root: Path):
        self.root = root
        self._index_df: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def build_index(self, refresh: bool = False) -> pd.DataFrame:
        if self._index_df is not None and not refresh:
            return self._index_df

        rows: List[Dict[str, object]] = []

        for suffix in ("*.dat", "*.dat.bz2", "*.dat.xz"):
            for file_path in self.root.rglob(suffix):
                if not file_path.is_file():
                    continue

                collector_name = file_path.parent.name.split(".")[0]
                normalized = collector_name.lower()

                lines = list(_open_text_file(file_path))
                header_lines, _ = _split_header_body(lines)
                header_map = _parse_header_map(header_lines)

                start_time = _timestamp_from_header(header_map.get("Starting Time"))
                if start_time is None:
                    start_time = _timestamp_from_filename(file_path)

                interval: Optional[int] = None
                if "Sample Interval(s)" in header_map:
                    try:
                        interval = int(header_map["Sample Interval(s)"])
                    except ValueError:
                        interval = None

                sample_count: Optional[int] = None
                if "Archive Count" in header_map:
                    try:
                        sample_count = int(header_map["Archive Count"])
                    except ValueError:
                        sample_count = None

                rows.append(
                    {
                        "collector": normalized,
                        "collector_dir": collector_name,
                        "path": file_path,
                        "module": header_map.get("Collection Module"),
                        "start_time": start_time,
                        "sample_interval": interval,
                        "sample_count": sample_count,
                        "compression": file_path.suffix.lower(),
                        "command": header_map.get("Collection Command"),
                    }
                )

        df = pd.DataFrame(rows)
        df.sort_values(["collector", "start_time", "path"], inplace=True)
        self._index_df = df.reset_index(drop=True)
        return self._index_df

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _ensure_index(self) -> pd.DataFrame:
        if self._index_df is None:
            return self.build_index()
        return self._index_df

    def load_collector(
        self,
        collector: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, pd.DataFrame]:
        key = collector.lower()
        parser = COLLECTOR_PARSERS.get(key)
        if parser is None:
            raise ValueError(f"Collector '{collector}' is not wired to a parser yet.")

        frames: Dict[str, List[pd.DataFrame]] = {}

        for record in self._ensure_index().itertuples():
            if record.collector != key:
                continue

            parsed = parser(Path(record.path))
            for section, df in parsed.items():
                filtered = df
                if start is not None:
                    filtered = filtered[filtered["timestamp"] >= start]
                if end is not None:
                    filtered = filtered[filtered["timestamp"] <= end]

                if not filtered.empty:
                    frames.setdefault(section, []).append(filtered)

        result = {}
        for section, parts in frames.items():
            if not parts:
                continue
            frame = pd.concat(parts)
            if "timestamp" in frame.columns:
                frame = frame.sort_values("timestamp")
            result[section] = frame.reset_index(drop=True)

        if limit is not None:
            for section, df in result.items():
                result[section] = df.head(limit)

        return result


###############################################################################
# Collector parsers
###############################################################################


def _parse_vmstat_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_date_str: Optional[str] = None
    if start_dt is not None:
        current_date_str = start_dt.strftime("%m/%d/%Y")

    columns = [
        "timestamp",
        "r",
        "b",
        "swpd",
        "free",
        "buff",
        "cache",
        "si",
        "so",
        "bi",
        "bo",
        "in",
        "cs",
        "us",
        "sy",
        "id",
        "wa",
        "st",
    ]

    records: List[Dict[str, object]] = []

    for raw in body_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("--time--") or stripped.startswith("procs"):
            continue
        if stripped.startswith("Linux"):
            continue
        if stripped.startswith("r  b"):
            continue

        parts = stripped.split()
        if len(parts) != len(columns):
            continue

        time_token = parts[0]
        timestamp: Optional[datetime] = None
        if current_date_str is not None:
            ts_str = f"{current_date_str} {time_token}"
            for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
                try:
                    timestamp = datetime.strptime(ts_str, fmt)
                    break
                except ValueError:
                    continue
        if timestamp is None and start_dt is not None:
            timestamp = start_dt

        record: Dict[str, object] = {"timestamp": timestamp}
        for idx, col in enumerate(columns[1:], start=1):
            try:
                record[col] = float(parts[idx])
            except ValueError:
                record[col] = None
        records.append(record)

    df = pd.DataFrame(records)
    return {"vmstat": df}


def _parse_iostat_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    current_timestamp: Optional[datetime] = _timestamp_from_header(
        header_map.get("Starting Time")
    )

    cpu_records: List[Dict[str, object]] = []
    device_records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz <(?P<ts>[^>]+)>")
    ampm_re = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} (AM|PM)")

    i = 0
    while i < len(body_lines):
        stripped = body_lines[i].strip()
        if not stripped:
            i += 1
            continue

        zzz_match = zzz_re.match(stripped)
        if zzz_match:
            try:
                current_timestamp = datetime.strptime(
                    zzz_match.group("ts"), "%m/%d/%Y %H:%M:%S"
                )
            except ValueError:
                pass
            i += 1
            continue

        if ampm_re.fullmatch(stripped):
            try:
                current_timestamp = datetime.strptime(
                    stripped, "%m/%d/%Y %H:%M:%S %p"
                )
            except ValueError:
                pass
            i += 1
            continue

        if stripped.startswith("Linux "):
            i += 1
            continue

        if stripped.startswith("avg-cpu:"):
            header_tokens = re.split(r"\s+", stripped.replace("avg-cpu:", "").strip())
            i += 1
            if i >= len(body_lines):
                break
            value_line = body_lines[i].strip()
            values = re.split(r"\s+", value_line)
            record: Dict[str, object] = {"timestamp": current_timestamp}
            for token, value in zip(header_tokens, values):
                key = token.strip("%")
                try:
                    record[f"cpu_{key.lower()}"] = float(value)
                except ValueError:
                    record[f"cpu_{key.lower()}"] = None
            cpu_records.append(record)
            i += 1
            continue

        if stripped.startswith("Device"):
            header_tokens = re.split(r"\s+", stripped)
            device_headers = [header_tokens[0].lower()] + header_tokens[1:]
            i += 1
            while i < len(body_lines):
                row_line = body_lines[i].strip()
                if not row_line:
                    i += 1
                    continue
                if (
                    row_line.startswith("Linux ")
                    or row_line.startswith("avg-cpu:")
                    or zzz_re.match(row_line)
                    or ampm_re.fullmatch(row_line)
                ):
                    break
                parts = re.split(r"\s+", row_line)
                if len(parts) < len(device_headers):
                    i += 1
                    continue
                record: Dict[str, object] = {
                    "timestamp": current_timestamp,
                    "device": parts[0],
                }
                for header_token, value in zip(device_headers[1:], parts[1:]):
                    try:
                        record[header_token.lower()] = float(value)
                    except ValueError:
                        record[header_token.lower()] = None
                device_records.append(record)
                i += 1
            continue

        i += 1

    cpu_df = pd.DataFrame(cpu_records)
    device_df = pd.DataFrame(device_records)
    return {"cpu": cpu_df, "device": device_df}


def _parse_toppid_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_timestamp = start_dt
    current_date_str: Optional[str] = (
        start_dt.strftime("%m/%d/%Y") if start_dt is not None else None
    )

    records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz <(?P<ts>[^>]+)>")

    i = 0
    while i < len(body_lines):
        line = body_lines[i].strip()
        if not line:
            i += 1
            continue

        zzz_match = zzz_re.match(line)
        if zzz_match:
            ts_str = zzz_match.group("ts")
            try:
                current_timestamp = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
                current_date_str = current_timestamp.strftime("%m/%d/%Y")
            except ValueError:
                pass
            i += 1
            continue

        if line.startswith("top -"):
            tokens = line.split()
            if len(tokens) >= 3 and current_date_str is not None:
                time_token = tokens[2]
                try:
                    current_timestamp = datetime.strptime(
                        f"{current_date_str} {time_token}", "%m/%d/%Y %H:%M:%S"
                    )
                except ValueError:
                    pass
            # skip subsequent summary lines
            i += 1
            while i < len(body_lines):
                candidate = body_lines[i].strip()
                if not candidate:
                    i += 1
                    continue
                if candidate.startswith("PID "):
                    break
                if candidate.startswith("%Cpu") or candidate.startswith("Tasks") or candidate.startswith("KiB") or candidate.startswith("MiB"):
                    i += 1
                    continue
                if candidate.startswith("zzz <") or candidate.startswith("top -"):
                    break
                i += 1
            continue

        if line.startswith("PID "):
            headers = re.split(r"\s+", line.strip())
            i += 1
            while i < len(body_lines):
                row = body_lines[i].rstrip()
                if not row or row.startswith("zzz <") or row.startswith("top -"):
                    break
                parts = re.split(r"\s+", row.strip(), maxsplit=len(headers) - 1)
                if len(parts) < len(headers):
                    i += 1
                    continue
                record: Dict[str, object] = {"timestamp": current_timestamp}
                for header, value in zip(headers, parts):
                    key = header.lower().strip("%")
                    if key == "pid":
                        record[key] = int(value)
                    elif key in {"pr", "ni", "s", "command", "user"}:
                        record[key] = value
                    else:
                        try:
                            record[key] = float(value)
                        except ValueError:
                            record[key] = value
                records.append(record)
                i += 1
            continue

        i += 1

    df = pd.DataFrame(records)
    return {"toppid": df}


def _parse_mpstat_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_date_str: Optional[str] = (
        start_dt.strftime("%m/%d/%Y") if start_dt is not None else None
    )

    records: List[Dict[str, object]] = []

    header_tokens: Optional[List[str]] = None

    for raw in body_lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Linux "):
            continue

        tokens = re.split(r"\s+", line)
        if len(tokens) < 3:
            continue

        # Header line pattern: HH:MM:SS AM CPU %usr ...
        if tokens[2].upper() == "CPU" and "%" in line:
            header_tokens = tokens[2:]
            continue

        if header_tokens is None:
            continue

        # Data line pattern: HH:MM:SS AM all 2.58 ...
        if len(tokens) >= len(header_tokens) + 2:
            time_token = f"{tokens[0]} {tokens[1]}"
            timestamp: Optional[datetime] = None
            if current_date_str is not None:
                try:
                    timestamp = datetime.strptime(
                        f"{current_date_str} {time_token}", "%m/%d/%Y %I:%M:%S %p"
                    )
                except ValueError:
                    timestamp = start_dt
            record_tokens = tokens[2 : 2 + len(header_tokens)]
            record: Dict[str, object] = {"timestamp": timestamp}
            for header, value in zip(header_tokens, record_tokens):
                key = header.lower().strip("%")
                if key == "cpu":
                    record[key] = value
                else:
                    try:
                        record[key] = float(value)
                    except ValueError:
                        record[key] = value
            records.append(record)

    df = pd.DataFrame(records)
    return {"mpstat": df}


def _parse_top_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_timestamp = start_dt
    current_date_str: Optional[str] = (
        start_dt.strftime("%m/%d/%Y") if start_dt else None
    )

    system_records: List[Dict[str, object]] = []
    process_records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz <(?P<ts>[^>]+)>")

    in_process_table = False
    process_headers: Optional[List[str]] = None

    i = 0
    while i < len(body_lines):
        line = body_lines[i].strip()
        if not line:
            i += 1
            continue

        zzz_match = zzz_re.match(line)
        if zzz_match:
            ts_str = zzz_match.group("ts")
            try:
                current_timestamp = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
                current_date_str = current_timestamp.strftime("%m/%d/%Y")
            except ValueError:
                pass
            in_process_table = False
            process_headers = None
            i += 1
            continue

        if line.startswith("top -"):
            tokens = line.split()
            if len(tokens) >= 3 and current_date_str:
                time_token = tokens[2]
                try:
                    current_timestamp = datetime.strptime(
                        f"{current_date_str} {time_token}", "%m/%d/%Y %H:%M:%S"
                    )
                except ValueError:
                    pass
            system_records.append({"timestamp": current_timestamp, "line": line})
            in_process_table = False
            process_headers = None
            i += 1
            continue

        if line.startswith("Threads") or line.startswith("Tasks"):
            system_records.append({"timestamp": current_timestamp, "line": line})
            i += 1
            continue

        if line.startswith("%Cpu") or line.startswith("Cpu(s)"):
            system_records.append({"timestamp": current_timestamp, "line": line})
            i += 1
            continue

        if line.startswith("KiB") or line.startswith("MiB") or line.startswith("GiB"):
            system_records.append({"timestamp": current_timestamp, "line": line})
            i += 1
            continue

        if line.startswith("PID "):
            process_headers = re.split(r"\s+", line.strip())
            in_process_table = True
            i += 1
            continue

        if in_process_table and process_headers is not None:
            if line.startswith("zzz <") or line.startswith("top -"):
                in_process_table = False
                continue
            parts = re.split(r"\s+", line.strip(), maxsplit=len(process_headers) - 1)
            if len(parts) < len(process_headers):
                i += 1
                continue
            record: Dict[str, object] = {"timestamp": current_timestamp}
            for header, value in zip(process_headers, parts):
                key = header.lower().strip("%")
                if key == "pid":
                    try:
                        record[key] = int(value)
                    except ValueError:
                        record[key] = value
                elif key in {"user", "pr", "ni", "s", "command"}:
                    record[key] = value
                else:
                    try:
                        record[key] = float(value)
                    except ValueError:
                        record[key] = value
            process_records.append(record)
        i += 1

    system_df = pd.DataFrame(system_records)
    process_df = pd.DataFrame(process_records)
    return {"system": system_df, "process": process_df}


def _parse_ps_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_timestamp = start_dt
    current_date_str: Optional[str] = (
        start_dt.strftime("%m/%d/%Y") if start_dt else None
    )

    records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz <(?P<ts>[^>]+)>")
    header_tokens: Optional[List[str]] = None

    for raw in body_lines:
        line = raw.rstrip()
        if not line:
            continue

        zzz_match = zzz_re.match(line)
        if zzz_match:
            ts_str = zzz_match.group("ts")
            try:
                current_timestamp = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
                current_date_str = current_timestamp.strftime("%m/%d/%Y")
            except ValueError:
                pass
            header_tokens = None
            continue

        if line.startswith("F "):
            header_tokens = re.split(r"\s+", line.strip())
            continue

        if header_tokens is None:
            continue

        parts = re.split(r"\s+", line.strip(), maxsplit=len(header_tokens) - 1)
        if len(parts) < len(header_tokens):
            continue

        record: Dict[str, object] = {"timestamp": current_timestamp}
        for header, value in zip(header_tokens, parts):
            key = header.lower().strip("%")
            if key in {"f", "c", "psr", "pri", "ni", "pid", "ppid"}:
                try:
                    record[key] = int(value)
                except ValueError:
                    record[key] = value
            elif key in {"rss", "sz"}:
                try:
                    record[key] = float(value)
                except ValueError:
                    record[key] = value
            else:
                record[key] = value
        records.append(record)

    df = pd.DataFrame(records)
    return {"ps": df}


def _parse_meminfo_file(path: Path) -> Dict[str, pd.DataFrame]:
    lines = list(_open_text_file(path))
    header_lines, body_lines = _split_header_body(lines)
    header_map = _parse_header_map(header_lines)

    start_dt = _timestamp_from_header(header_map.get("Starting Time"))
    current_timestamp = start_dt
    records: List[Dict[str, object]] = []

    zzz_re = re.compile(r"zzz <(?P<ts>[^>]+)>")

    current_metrics: Dict[str, float] = {}

    for raw in body_lines:
        line = raw.strip()
        if not line:
            continue

        zzz_match = zzz_re.match(line)
        if zzz_match:
            if current_metrics:
                record = {"timestamp": current_timestamp}
                record.update(current_metrics)
                records.append(record)
                current_metrics = {}

            ts_str = zzz_match.group("ts")
            try:
                current_timestamp = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
            except ValueError:
                pass
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            number = value.split()[0]
            try:
                current_metrics[key] = float(number)
            except ValueError:
                continue

    if current_metrics:
        record = {"timestamp": current_timestamp}
        record.update(current_metrics)
        records.append(record)

    df = pd.DataFrame(records)
    return {"meminfo": df}


COLLECTOR_PARSERS = {
    "vmstat": _parse_vmstat_file,
    "iostat": _parse_iostat_file,
    "toppid": _parse_toppid_file,
    "mpstat": _parse_mpstat_file,
    "top": _parse_top_file,
    "ps": _parse_ps_file,
    "meminfo": _parse_meminfo_file,
}


###############################################################################
# CLI entry point
###############################################################################


def _parse_cli_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Explore ExaWatcher datasets")
    parser.add_argument("root", type=Path, help="Path to ExaWatcher archive root")
    parser.add_argument(
        "--collector",
        help="Collector name to load (e.g. vmstat, iostat)",
    )
    parser.add_argument(
        "--section",
        help="Section within collector (e.g. cpu, device)",
    )
    parser.add_argument(
        "--start",
        help="ISO8601 lower bound (e.g. 2025-12-16T09:30:00)",
    )
    parser.add_argument(
        "--end",
        help="ISO8601 upper bound (e.g. 2025-12-16T12:00:00)",
    )
    parser.add_argument("--limit", type=int, help="Limit rows per section")
    parser.add_argument(
        "--refresh-index",
        action="store_true",
        help="Rebuild the on-disk index cache",
    )
    return parser.parse_args(argv)


def _parse_iso8601(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse datetime: {value}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_cli_args(argv)

    dataset = ExaWatcherDataset(args.root)
    index_df = dataset.build_index(refresh=args.refresh_index)

    print("== Collector Files ==")
    print(index_df[["collector", "start_time", "sample_interval", "sample_count", "path"]])

    if args.collector:
        start = _parse_iso8601(args.start)
        end = _parse_iso8601(args.end)
        loaded = dataset.load_collector(
            args.collector,
            start=start,
            end=end,
            limit=args.limit,
        )
        for section, frame in loaded.items():
            if args.section and section != args.section:
                continue
            print(f"\n== {args.collector}:{section} ==")
            print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
