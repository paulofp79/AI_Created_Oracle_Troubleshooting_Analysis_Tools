"""Parsers and normalization helpers for ExaWatcher collector output."""

import lzma
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd


CATEGORIES = {
    "Acfs": "ACFS filesystem state",
    "Diskinfo": "Linux disk I/O counters",
    "EdvStat": "EDV virtual disk statistics",
    "IBCardInfo": "RDMA adapter resources",
    "IBprocs": "InfiniBand and RoCE diagnostics",
    "Lsof": "Open file descriptors",
    "Meminfo": "Linux memory statistics",
    "Mpstat": "Per-CPU utilization",
    "Netinfo": "Network interface inventory",
    "Nfsiostat": "NFS I/O statistics",
    "Numa": "NUMA memory and locality",
    "Ps": "Process snapshots",
    "Roceaudit": "RoCE audit checks",
    "Rocestat": "RoCE port counters",
    "Top": "System and process activity",
    "TopPid": "Selected process activity",
}

MAX_FILE_BYTES = 200 * 1024 * 1024


class FileParse:
    def __init__(self):
        self.metadata = {}
        self.records = []
        self.details = []
        self.warnings = []


class AnalysisResult:
    def __init__(self, category, metadata, records, details, warnings):
        self.category = category
        self.metadata = metadata
        self.records = records
        self.details = details
        self.warnings = warnings


def decode_file(name: str, payload: bytes) -> str:
    """Decode a plain, XZ, or bzip2 ExaWatcher file."""
    suffixes = [suffix.lower() for suffix in Path(name).suffixes]
    try:
        if suffixes and suffixes[-1] == ".xz":
            payload = lzma.decompress(payload)
        elif suffixes and suffixes[-1] == ".bz2":
            import bz2

            payload = bz2.decompress(payload)
    except (lzma.LZMAError, OSError, EOFError, ValueError) as exc:
        raise ValueError(f"could not decompress file: {exc}") from exc
    return payload.decode("utf-8", errors="replace")


@lru_cache(maxsize=8192)
def _parse_datetime(value: str, base: Optional[datetime] = None) -> Optional[datetime]:
    text = value.strip().strip("<>").strip()
    formats = (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%H:%M:%S",
        "%I:%M:%S %p",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.year == 1900 and base is not None:
                return datetime.combine(base.date(), parsed.time())
            return parsed
        except ValueError:
            continue
    return None


def _filename_timestamp(name: str) -> Optional[datetime]:
    match = re.search(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})", Path(name).name)
    if not match:
        return None
    try:
        return datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def parse_metadata(text: str, source_file: str) -> Dict[str, object]:
    fields: Dict[str, str] = {}
    for line in text.splitlines()[:50]:
        match = re.match(r"\s*#\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            fields[match.group(1).strip().lower()] = match.group(2).strip()
    started = _parse_datetime(fields.get("starting time", "")) or _filename_timestamp(source_file)
    try:
        interval = float(fields.get("sample interval(s)", ""))
    except ValueError:
        interval = None
    try:
        archive_count = int(fields.get("archive count", ""))
    except ValueError:
        archive_count = None
    return {
        "source_file": source_file,
        "starting_time": started,
        "sample_interval_seconds": interval,
        "archive_count": archive_count,
        "collection_module": fields.get("collection module", ""),
        "host": _parse_host(text),
    }


def _parse_host(text: str) -> str:
    for line in text.splitlines():
        match = re.search(r"Linux\s+\S+\s+\(([^)]+)\)", line)
        if match:
            return match.group(1).strip()
        match = re.search(r"EDV Base Version\s+\(([^)]+)\)", line)
        if match:
            return match.group(1).strip()
    return ""


def _samples(text: str, fallback: Optional[datetime]) -> List[Tuple[Optional[datetime], str]]:
    marker = re.compile(r"(?im)^\s*zzz\s*<([^>]+)>[^\n]*")
    matches = list(marker.finditer(text))
    if not matches:
        return [(fallback, text)]
    samples: List[Tuple[Optional[datetime], str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        timestamp = _parse_datetime(match.group(1), fallback)
        chunk = text[match.end() : end]
        if samples and timestamp is not None and samples[-1][0] == timestamp:
            samples[-1] = (timestamp, samples[-1][1] + "\n" + chunk)
        else:
            samples.append((timestamp or fallback, chunk))
    return samples


def _num(value: object) -> Optional[float]:
    try:
        result = float(str(value).strip().replace(",", ""))
        return result if pd.notna(result) else None
    except (TypeError, ValueError):
        return None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _record(timestamp: Optional[datetime], source: str, entity: str, **values: object) -> Dict[str, object]:
    return {"timestamp": timestamp, "source_file": source, "entity": entity, **values}


def _quantity_mib(value: str) -> Optional[float]:
    match = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*([kmgtpe]?i?b?)?", value, re.I)
    if not match:
        return None
    number = _num(match.group(1))
    unit = (match.group(2) or "").upper().replace("IB", "B")
    if number is None:
        return None
    factor = {
        "": 1 / (1024 * 1024), "B": 1 / (1024 * 1024),
        "K": 1 / 1024, "KB": 1 / 1024, "M": 1, "MB": 1,
        "G": 1024, "GB": 1024, "T": 1024 * 1024, "TB": 1024 * 1024,
    }
    return number * factor.get(unit, 1)


def _parse_diskinfo(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        for line in block.splitlines():
            parts = line.split()
            if len(parts) < 14 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            device = parts[2]
            values = [_num(part) for part in parts[3:]]
            if any(value is None for value in values[:11]):
                continue
            raw = {
                "major": int(parts[0]), "minor": int(parts[1]),
                "reads_completed_counter": values[0], "reads_merged_counter": values[1],
                "sectors_read_counter": values[2], "read_time_ms_counter": values[3],
                "writes_completed_counter": values[4], "writes_merged_counter": values[5],
                "sectors_written_counter": values[6], "write_time_ms_counter": values[7],
                "in_flight": values[8], "io_time_ms_counter": values[9],
                "weighted_io_time_ms_counter": values[10],
            }
            out.records.append(_record(timestamp, source, device, **raw))


def _derive_diskinfo(frame: pd.DataFrame, warnings: List[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    ordered = frame.sort_values(["entity", "timestamp"], kind="mergesort").drop_duplicates(
        ["entity", "timestamp"], keep="last"
    ).copy()
    elapsed = ordered.groupby("entity", sort=False)["timestamp"].diff().dt.total_seconds()
    valid_elapsed = elapsed.where(elapsed > 0)
    output = ordered[["timestamp", "source_file", "entity"]].copy()
    deltas: Dict[str, pd.Series] = {}
    reset_rows = pd.Series(False, index=ordered.index)
    counter_outputs = {
        "read_iops": "reads_completed_counter",
        "write_iops": "writes_completed_counter",
        "read_sectors_s": "sectors_read_counter",
        "write_sectors_s": "sectors_written_counter",
        "busy_time_ms": "io_time_ms_counter",
        "weighted_io_time_ms": "weighted_io_time_ms_counter",
        "read_time_ms": "read_time_ms_counter",
        "write_time_ms": "write_time_ms_counter",
    }
    for name, column in counter_outputs.items():
        delta = ordered.groupby("entity", sort=False)[column].diff()
        deltas[name] = delta
        reset_rows |= delta < 0
        if name in {"read_iops", "write_iops", "read_sectors_s", "write_sectors_s", "busy_time_ms"}:
            output[name] = delta.where(delta >= 0) / valid_elapsed
    output["read_MB_s"] = output.pop("read_sectors_s") * 512 / 1_000_000
    output["write_MB_s"] = output.pop("write_sectors_s") * 512 / 1_000_000
    output["busy_pct"] = output.pop("busy_time_ms") / 10
    for io_name, count_name, time_name in (
        ("read_await_ms", "read_iops", "read_time_ms"),
        ("write_await_ms", "write_iops", "write_time_ms"),
    ):
        count_delta, time_delta = deltas[count_name], deltas[time_name]
        output[io_name] = time_delta.where((count_delta > 0) & (time_delta >= 0)) / count_delta
    output["avg_queue_depth"] = deltas["weighted_io_time_ms"].where(
        deltas["weighted_io_time_ms"] >= 0
    ) / valid_elapsed / 1000
    output["in_flight"] = ordered["in_flight"]
    if reset_rows.any():
        examples = ordered.loc[reset_rows, ["entity", "timestamp"]].drop_duplicates().head(10)
        example_text = ", ".join(
            f"{row.entity} at {row.timestamp}" for row in examples.itertuples(index=False)
        )
        suffix = " ..." if int(reset_rows.sum()) > len(examples) else ""
        warnings.append(
            f"Diskinfo counter resets affected {int(reset_rows.sum())} device interval(s); "
            f"affected rates are omitted ({example_text}{suffix})."
        )
    rate_columns = ["read_iops", "write_iops", "read_MB_s", "write_MB_s", "busy_pct", "read_await_ms", "write_await_ms", "avg_queue_depth"]
    output = output[output[rate_columns].notna().any(axis=1)]
    if output.empty:
        warnings.append("Disk counters were parsed, but no consecutive timestamps were available to derive rates.")
        return pd.DataFrame(columns=["timestamp", "source_file", "entity"] + rate_columns)
    return output


def _parse_meminfo(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    wanted = {"MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SReclaimable", "Dirty", "Writeback", "SwapTotal", "SwapFree", "SwapCached", "Active", "Inactive", "AnonPages", "Shmem", "Slab", "Unevictable", "Mlocked"}
    metric_names = {
        "MemTotal": "mem_total", "MemFree": "mem_free", "MemAvailable": "mem_available",
        "Buffers": "buffers", "Cached": "cached", "SReclaimable": "s_reclaimable",
        "Dirty": "dirty", "Writeback": "writeback", "SwapTotal": "swap_total",
        "SwapFree": "swap_free", "SwapCached": "swap_cached", "Active": "active",
        "Inactive": "inactive", "AnonPages": "anon_pages", "Shmem": "shmem",
        "Slab": "slab", "Unevictable": "unevictable", "Mlocked": "mlocked",
    }
    for timestamp, block in samples:
        values: Dict[str, float] = {}
        for line in block.splitlines():
            match = re.match(r"\s*([A-Za-z_()]+):\s*(\d+)\s*(kB)?", line)
            if match and match.group(1) in wanted:
                values[match.group(1)] = float(match.group(2))
        if not values:
            continue
        record: Dict[str, object] = {}
        for name, value in values.items():
            record[f"{metric_names[name]}_mib"] = value / 1024
        if "MemAvailable" in values and "MemTotal" in values and values["MemTotal"]:
            record["mem_available_pct"] = 100 * values["MemAvailable"] / values["MemTotal"]
        if "SwapTotal" in values and "SwapFree" in values:
            record["swap_used_mib"] = (values["SwapTotal"] - values["SwapFree"]) / 1024
        out.records.append(_record(timestamp, source, "system", **record))


def _parse_mpstat(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        for line in block.splitlines():
            parts = line.split()
            if len(parts) < 8 or not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", parts[0]):
                continue
            index = 1
            if len(parts) > 1 and parts[1].upper() in {"AM", "PM"}:
                index = 2
            if len(parts) <= index + 2:
                continue
            row_time = _parse_datetime(" ".join(parts[:index]), timestamp)
            cpu = parts[index]
            vals = parts[index + 1 :]
            names = ["usr", "nice", "sys", "iowait", "irq", "soft", "steal", "guest", "gnice", "idle"]
            if len(vals) < 6:
                continue
            record: Dict[str, object] = {"cpu": cpu}
            for name, value in zip(names, vals):
                parsed = _num(value)
                if parsed is not None:
                    record[f"{name}_pct"] = parsed
            idle = _num(record.get("idle_pct"))
            if idle is not None:
                record["busy_pct"] = 100 - idle
            out.records.append(_record(row_time or timestamp, source, cpu, **record))


def _parse_nfsiostat(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    mount_re = re.compile(r"^\s*(.*?)\s+mounted on\s+(.+?):\s*$", re.I)
    current_export = ""
    current_mount = ""
    pending: Optional[str] = None
    for timestamp, block in samples:
        current_export = current_mount = ""
        pending = None
        for line in block.splitlines():
            mount = mount_re.match(line)
            if mount:
                current_export, current_mount = mount.group(1).strip(), mount.group(2).strip()
                continue
            lower = line.strip().lower()
            if lower.startswith("read:"):
                pending = "read"
                continue
            if lower.startswith("write:"):
                pending = "write"
                continue
            if "ops/s" in lower and "rpc bklog" in lower:
                continue
            if pending and current_mount:
                nums = [_num(value) for value in re.findall(r"[-+]?\d+(?:\.\d+)?", line)]
                if len(nums) >= 8:
                    row: Dict[str, object] = {
                        "export": current_export,
                        f"{pending}_ops_s": nums[0],
                        f"{pending}_kb_s": nums[1],
                        f"{pending}_kb_op": nums[2],
                        f"{pending}_retrans": nums[3],
                        f"{pending}_retrans_pct": nums[4],
                        f"{pending}_avg_rtt_ms": nums[5],
                        f"{pending}_avg_exec_ms": nums[6],
                        f"{pending}_avg_queue_ms": nums[7],
                    }
                    if len(nums) > 8:
                        row[f"{pending}_errors"] = nums[8]
                    out.records.append(_record(timestamp, source, current_mount, **row))
                pending = None
                continue
            if current_mount and pending is None and re.match(r"\s*\d", line):
                values = re.findall(r"[-+]?\d+(?:\.\d+)?", line)
                if len(values) >= 2:
                    out.records.append(_record(timestamp, source, current_mount, export=current_export, rpc_ops_s=_num(values[0]), rpc_backlog=_num(values[1])))


def _parse_rocestat(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        time_markers = list(re.finditer(r"(?im)^\s*time:\s*(.+?)\s*$", block))
        if time_markers:
            segments = []
            for index, marker in enumerate(time_markers):
                end = time_markers[index + 1].start() if index + 1 < len(time_markers) else len(block)
                sample_time = _parse_datetime(marker.group(1), timestamp) or timestamp
                segments.append((sample_time, block[marker.end() : end]))
        else:
            segments = [(timestamp, block)]
        for current_time, sample_block in segments:
            ports: List[str] = []
            rows: Dict[str, Dict[str, object]] = {}
            for line in sample_block.splitlines():
                stripped = line.strip()
                columns = stripped.split()
                if columns and all(re.fullmatch(r"mlx\d+(?:_\d+)+", part) for part in columns):
                    ports = columns
                    for port in ports:
                        rows.setdefault(port, {})
                    continue
                if not ports or not columns:
                    continue
                metric = _key(columns[0])
                matches = re.findall(r"([-+]?\d+(?:\.\d+)?)\s*\(\s*([-+]?\d+(?:\.\d+)?)\s*\)", stripped[len(columns[0]) :])
                if len(matches) != len(ports):
                    continue
                for port, (total, rate) in zip(ports, matches):
                    if metric in {"port_rcv_data", "port_xmit_data"}:
                        rows[port][f"{metric}_total_counter"] = _num(total)
                        rows[port][f"{metric}_gbps"] = _num(rate)
                    else:
                        rows[port][f"{metric}_total_counter"] = _num(total)
                        rows[port][f"{metric}_per_s"] = _num(rate)
            for port, values in rows.items():
                if values:
                    out.records.append(_record(current_time, source, port, **values))


def _parse_acfs(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        section = ""
        current_fs = "ACFS"
        rows: Dict[str, Dict[str, object]] = {}
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("="):
                section = stripped.strip("= ").lower()
                continue
            if section == "acfsutil info fs" and stripped.startswith("/") and " " not in stripped:
                current_fs = stripped
                rows.setdefault(current_fs, {})
                continue
            if ":" not in stripped:
                if section and stripped and not stripped.startswith("["):
                    out.details.append({"timestamp": timestamp, "source_file": source, "section": section, "entity": current_fs, "detail": stripped[:500]})
                continue
            label, value = stripped.split(":", 1)
            label, value = label.strip(), value.strip()
            key = _key(label)
            out.details.append({"timestamp": timestamp, "source_file": source, "section": section, "entity": current_fs, "field": label, "value": value})
            if section != "acfsutil info fs":
                continue
            values = rows.setdefault(current_fs, {})
            if key in {"total_size", "total_free", "size", "free", "snapshot_space_usage"}:
                quantity = _quantity_mib(value)
                if quantity is not None:
                    values[f"{key}_mib"] = quantity
            elif key in {"state", "replication_status", "compression_status", "mount_point", "acfs_version"}:
                values[key] = value
        for entity, values in rows.items():
            if "total_size_mib" in values and "total_free_mib" in values and values["total_size_mib"]:
                values["used_pct"] = 100 * (values["total_size_mib"] - values["total_free_mib"]) / values["total_size_mib"]
            if values:
                out.records.append(_record(timestamp, source, entity, **values))


def _parse_edvstat(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        lines = block.splitlines()
        device_headers: List[List[str]] = []
        reading_devices = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if "cellsrvstat returned NULL data" in stripped:
                out.warnings.append(f"{source} at {timestamp}: cellsrvstat returned NULL data; device metrics are incomplete.")
            if stripped.startswith("avg-cpu:"):
                cpu_headers = [_key(v.strip("%")) + "_pct" for v in stripped.split(":", 1)[1].split()]
                if index + 1 < len(lines):
                    values = [_num(v) for v in lines[index + 1].split()]
                    row = {name: value for name, value in zip(cpu_headers, values) if value is not None}
                    if row:
                        out.records.append(_record(timestamp, source, "system", **row))
                continue
            if "Kernel_device_file" in stripped:
                device_headers = [[_key(v) for v in part.split()] for part in stripped.split("|")]
                reading_devices = True
                continue
            if stripped.startswith("Showing"):
                if "0 of 0 devices" in stripped:
                    out.details.append({"timestamp": timestamp, "source_file": source, "section": "EDV devices", "status": "no rows", "detail": stripped})
                reading_devices = False
                continue
            if reading_devices and device_headers and stripped and not stripped.startswith(("Linux ", "avg-cpu:")):
                chunks = [part.split() for part in stripped.split("|")]
                first = chunks[0] if chunks else []
                if len(first) < 2 or _num(first[0]) is not None:
                    continue
                entity = first[0]
                values: Dict[str, object] = {}
                for group_index, (header_group, value_group) in enumerate(zip(device_headers, chunks)):
                    start = 1 if group_index == 0 else 0
                    value_start = 1 if group_index == 0 else 0
                    for label, value in zip(header_group[start:], value_group[value_start:]):
                        parsed = _num(value)
                        if parsed is not None:
                            values[f"group{group_index + 1}_{label}"] = parsed
                if values:
                    out.records.append(_record(timestamp, source, entity, **values))


def _parse_ibcardinfo(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    pattern = re.compile(r"^\s*\d+:\s+(\S+):\s*(.*)$")
    for timestamp, block in samples:
        for line in block.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            device, rest = match.groups()
            values = {_key(key): _num(value) for key, value in re.findall(r"([A-Za-z_]+)\s+([\d.]+)", rest)}
            values = {key: value for key, value in values.items() if value is not None}
            out.records.append(_record(timestamp, source, device, **values))


def _parse_ibprocs(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        section = "IBprocs"
        port_entity = ""
        row: Dict[str, object] = {}
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("===") and stripped.endswith("==="):
                if row:
                    out.records.append(_record(timestamp, source, port_entity or section, section=section, **row))
                    row = {}
                section = stripped.strip("= ")
                match = re.search(r"(mlx\d+\s+port:\s*\d+)", section, re.I)
                port_entity = match.group(1) if match else section
                continue
            if not stripped:
                continue
            out.details.append({"timestamp": timestamp, "source_file": source, "section": section, "detail": stripped[:500]})
            if "delta cm_rx|tx stats" in section.lower():
                pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:= ]\s*(-?\d+(?:\.\d+)?)", stripped)
                for name, value in pairs:
                    parsed = _num(value)
                    if parsed is not None:
                        row[_key(name)] = parsed
        if row:
            out.records.append(_record(timestamp, source, port_entity or section, section=section, **row))


def _parse_netinfo(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        current: Optional[Dict[str, object]] = None
        for line in block.splitlines():
            stripped = line.strip()
            iface = re.match(r"\d+:\s+([^:]+):\s+<([^>]*)>\s+mtu\s+(\d+)(.*)$", stripped)
            if iface:
                if current:
                    out.records.append(_record(timestamp, source, str(current.get("entity", "")), **{k: v for k, v in current.items() if k != "entity"}))
                name, flags, mtu, tail = iface.groups()
                attrs: Dict[str, object] = {"entity": name, "flags": flags, "mtu": int(mtu)}
                state = re.search(r"\bstate\s+(\S+)", tail)
                qlen = re.search(r"\bqlen\s+(\d+)", tail)
                qdisc = re.search(r"\bqdisc\s+(\S+)", tail)
                if state:
                    attrs["state"] = state.group(1)
                if qlen:
                    attrs["tx_queue_length"] = int(qlen.group(1))
                if qdisc:
                    attrs["qdisc"] = qdisc.group(1)
                current = attrs
                continue
            if current is None:
                continue
            link = re.match(r"link/\S+\s+(\S+)", stripped)
            if link:
                current["link_address"] = link.group(1)
            inet = re.match(r"inet6?\s+(\S+)", stripped)
            if inet:
                key = "ipv6" if stripped.startswith("inet6") else "ipv4"
                current[key] = (str(current.get(key, "")) + ", " + inet.group(1)).strip(", ")
        if current:
            out.records.append(_record(timestamp, source, str(current.get("entity", "")), **{k: v for k, v in current.items() if k != "entity"}))


def _parse_numa(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        table_name = ""
        node_names: List[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("Per-node system memory"):
                table_name = "system_memory_mib"
                continue
            if stripped.startswith("Per-node numastat"):
                table_name = "numastat_mib"
                continue
            header = re.findall(r"Node\s+\d+|Total", stripped, re.I)
            if header and "Node" in stripped:
                node_names = [re.sub(r"\s+", "_", value).lower() for value in header]
                continue
            if not table_name or not node_names or not stripped:
                continue
            parts = stripped.split()
            if len(parts) < len(node_names) + 1:
                continue
            metric = _key(parts[0])
            values = [_num(value) for value in parts[1 : 1 + len(node_names)]]
            if not any(value is not None for value in values):
                continue
            for node, value in zip(node_names, values):
                if value is not None:
                    out.records.append(_record(timestamp, source, node, **{f"{table_name}_{metric}": value}))


def _parse_roceaudit(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        section = "RoCE audit"
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("####") or stripped.startswith("["):
                section = stripped.strip("#[] ") or section
            lowered = stripped.lower()
            if "skip" in lowered or "not applicable" in lowered:
                status = "skipped"
            elif re.search(r"\b(fail|error|critical)\b", lowered):
                status = "failed"
            elif re.search(r"\b(pass|passed|success)\b", lowered):
                status = "passed"
            else:
                status = "information"
            out.details.append({"timestamp": timestamp, "source_file": source, "section": section, "status": status, "detail": stripped[:500]})


def _parse_lsof(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        counts: Dict[Tuple[str, str, str], int] = {}
        for line in block.splitlines():
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#") or stripped.lstrip().startswith("COMMAND"):
                continue
            parts = stripped.split(None, 10)
            if len(parts) < 10 or not parts[1].isdigit():
                continue
            command, pid, ppid, user, fd, kind, device, size, nlink, node = parts[:10]
            name = parts[10] if len(parts) > 10 else ""
            counts[(pid, command, kind)] = counts.get((pid, command, kind), 0) + 1
            out.details.append({"timestamp": timestamp, "source_file": source, "pid": pid, "ppid": ppid, "user": user, "command": command, "fd": fd, "type": kind, "device": device, "size_off": size, "nlink": nlink, "node": node, "name": name[:500]})
        for (pid, command, kind), count in counts.items():
            out.records.append(_record(timestamp, source, f"{pid}:{command}:{kind}", pid=pid, command=command, descriptor_type=kind, open_fd_count=count))


def _parse_ps(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        header_positions: List[Tuple[str, int]] = []
        for line in block.splitlines():
            if "PID" in line and "COMMAND" in line and "PPID" in line:
                header_positions = [(_key(match.group(0)), match.start()) for match in re.finditer(r"\S+", line)]
                continue
            if not header_positions:
                continue
            starts = [position for _, position in header_positions]
            values = [line[start : starts[index + 1] if index + 1 < len(starts) else None].strip() for index, start in enumerate(starts)]
            row = {name: value for (name, _), value in zip(header_positions, values)}
            pid = row.get("pid", "")
            if not pid.isdigit():
                continue
            command = row.get("cmd") or row.get("command") or ""
            rss = _num(row.get("rss"))
            record: Dict[str, object] = {"pid": pid, "ppid": row.get("ppid", ""), "user": row.get("ruser", ""), "state": row.get("s", ""), "command": command, "wchan": row.get("wide_wchan_column", "")}
            cpu_value = _num(row.get("pcpu") or row.get("%cpu") or row.get("c"))
            if cpu_value is not None:
                record["cpu_c"] = cpu_value
            if rss is not None:
                record["rss_mib"] = rss / 1024
            out.records.append(_record(timestamp, source, f"{pid}:{command[:80]}", **record))


def _size_in_mib(text: str) -> Optional[float]:
    match = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?)\s*(?:i?b)?\s*", text, re.I)
    if not match:
        return _num(text)
    value, unit = float(match.group(1)), match.group(2).upper()
    factors = {"": 1 / (1024 * 1024), "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    return value * factors[unit]


def _parse_top(samples: List[Tuple[Optional[datetime], str]], source: str, out: FileParse) -> None:
    for timestamp, block in samples:
        process_headers = False
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("%Cpu(s):"):
                values = {_key(name): _num(value) for value, name in re.findall(r"([\d.]+)\s*([a-zA-Z/]+)", stripped.split(":", 1)[1])}
                normalized = {f"cpu_{key}_pct": value for key, value in values.items() if value is not None}
                if normalized:
                    out.records.append(_record(timestamp, source, "system", **normalized))
                continue
            if stripped.startswith(("MiB Mem", "GiB Mem", "KiB Mem", "MiB Swap", "GiB Swap", "KiB Swap")):
                unit = stripped.split()[0].lower()
                row: Dict[str, object] = {}
                for value, label in re.findall(r"([\d.+]+)\s*([A-Za-z/]+)", stripped.split(":", 1)[1]):
                    parsed = _num(value.replace("+", ""))
                    if parsed is not None:
                        row[f"{_key(label)}_{unit[:3]}"] = parsed
                if row:
                    out.records.append(_record(timestamp, source, "system", **row))
                continue
            if stripped.startswith("PID USER") and "%CPU" in stripped:
                process_headers = True
                continue
            if process_headers:
                parts = stripped.split(maxsplit=11)
                if len(parts) < 11 or not parts[0].isdigit():
                    continue
                pid, user, pr, ni, virt, res, shr, state, cpu, mem, cputime = parts[:11]
                command = parts[11] if len(parts) > 11 else ""
                record: Dict[str, object] = {"pid": pid, "user": user, "state": state, "command": command, "cpu_pct": _num(cpu), "mem_pct": _num(mem), "resident_mib": _size_in_mib(res)}
                out.records.append(_record(timestamp, source, f"{pid}:{command[:80]}", **record))


_PARSERS: Dict[str, Callable[[List[Tuple[Optional[datetime], str]], str, FileParse], None]] = {
    "Acfs": _parse_acfs,
    "Diskinfo": _parse_diskinfo,
    "EdvStat": _parse_edvstat,
    "IBCardInfo": _parse_ibcardinfo,
    "IBprocs": _parse_ibprocs,
    "Lsof": _parse_lsof,
    "Meminfo": _parse_meminfo,
    "Mpstat": _parse_mpstat,
    "Netinfo": _parse_netinfo,
    "Nfsiostat": _parse_nfsiostat,
    "Numa": _parse_numa,
    "Ps": _parse_ps,
    "Roceaudit": _parse_roceaudit,
    "Rocestat": _parse_rocestat,
    "Top": _parse_top,
    "TopPid": _parse_top,
}


def _parse_one(category: str, source: str, text: str) -> FileParse:
    out = FileParse()
    metadata = parse_metadata(text, source)
    out.metadata = metadata
    samples = _samples(text, metadata.get("starting_time"))
    try:
        _PARSERS[category](samples, source, out)
    except Exception as exc:
        out.warnings.append(f"{source}: parser stopped with {type(exc).__name__}: {exc}")
    parsed_timestamps = {
        row.get("timestamp")
        for row in out.records + out.details
        if row.get("timestamp") is not None
    }
    sample_timestamps = {ts for ts, _ in samples if ts is not None}
    coverage_timestamps = parsed_timestamps or sample_timestamps
    metadata["observed_samples"] = len(coverage_timestamps)
    metadata["first_observed_sample"] = min(coverage_timestamps) if coverage_timestamps else None
    metadata["last_observed_sample"] = max(coverage_timestamps) if coverage_timestamps else None
    if not samples:
        out.warnings.append(f"{source}: no sample blocks found.")
    if not out.records and not out.details:
        out.warnings.append(f"{source}: no {category} rows were recognized.")
    return out


def _metric_columns(frame: pd.DataFrame) -> List[str]:
    excluded = {"timestamp", "source_file", "entity", "pid", "ppid", "major", "minor", "cpu", "user", "command", "state", "section", "export", "flags", "qdisc", "link_address", "ipv4", "ipv6", "starting_time", "sample_interval_seconds", "archive_count", "observed_samples", "possible_pid_reuse"}
    return [
        column for column in frame.columns
        if column not in excluded
        and not column.endswith("_counter")
        and pd.api.types.is_numeric_dtype(frame[column])
        and frame[column].notna().any()
    ]


def _coverage_warnings(records: pd.DataFrame, metadata: pd.DataFrame) -> List[str]:
    warnings: List[str] = []
    if records.empty or "timestamp" not in records.columns:
        return warnings
    timestamps = pd.to_datetime(records["timestamp"], errors="coerce").dropna().drop_duplicates().sort_values()
    if len(timestamps) < 3:
        return warnings
    deltas = timestamps.diff().dt.total_seconds().dropna()
    positive = deltas[deltas > 0]
    if positive.empty:
        return warnings
    interval = float(positive.median())
    if interval <= 0:
        return warnings
    gaps = deltas[deltas > max(interval * 2.5, interval + 1)]
    if not gaps.empty:
        warnings.append(f"Observed {len(gaps)} timestamp gap(s) larger than about 2.5x the typical {interval:g}s sample interval. Gaps were not filled.")
    if not metadata.empty and {"archive_count", "observed_samples"}.issubset(metadata.columns):
        mismatches = metadata[(metadata["archive_count"].notna()) & (metadata["observed_samples"].notna()) & (metadata["observed_samples"] < metadata["archive_count"])]
        if not mismatches.empty:
            warnings.append(f"{len(mismatches)} file(s) contain fewer timestamp markers than their Archive Count; inspect file-level metadata.")
    return warnings


def _snapshot_change_rows(category: str, records: pd.DataFrame, details: pd.DataFrame) -> pd.DataFrame:
    """Add explicit changes for inventory/configuration collectors."""
    changes: List[Dict[str, object]] = []
    if category in {"Acfs", "Netinfo"} and not records.empty and {"timestamp", "entity"}.issubset(records.columns):
        ignored = {"timestamp", "source_file", "entity"}
        for entity, group in records.sort_values("timestamp", kind="mergesort").groupby("entity", sort=False):
            previous: Optional[Dict[str, object]] = None
            for current in group.to_dict("records"):
                if previous is not None:
                    for field in (key for key in current if key not in ignored):
                        old, new = previous.get(field), current.get(field)
                        if pd.isna(old) or pd.isna(new) or old == new:
                            continue
                        changes.append({
                            "timestamp": current.get("timestamp"),
                            "source_file": current.get("source_file", ""),
                            "entity": entity,
                            "section": "snapshot change",
                            "status": "changed",
                            "field": field,
                            "old_value": old,
                            "new_value": new,
                            "detail": f"{field}: {old} -> {new}",
                        })
                previous = current
    elif category == "IBprocs" and not details.empty and {"timestamp", "section", "detail"}.issubset(details.columns):
        snapshots = details.dropna(subset=["timestamp"]).groupby(["timestamp", "section"], sort=True)
        previous_by_section: Dict[str, Tuple[object, set]] = {}
        for (timestamp, section), group in snapshots:
            current_lines = set(group["detail"].dropna().astype(str))
            if section in previous_by_section:
                prior_time, prior_lines = previous_by_section[section]
                for line in sorted(current_lines - prior_lines):
                    changes.append({"timestamp": timestamp, "source_file": group.iloc[0].get("source_file", ""), "section": "snapshot change", "status": "added", "field": str(section), "old_value": "", "new_value": line, "detail": f"{section}: added: {line}"})
                for line in sorted(prior_lines - current_lines):
                    changes.append({"timestamp": timestamp, "source_file": group.iloc[0].get("source_file", ""), "section": "snapshot change", "status": "removed", "field": str(section), "old_value": line, "new_value": "", "detail": f"{section}: removed: {line}"})
            previous_by_section[section] = (timestamp, current_lines)
    if not changes:
        return details
    change_frame = pd.DataFrame.from_records(changes)
    return pd.concat([details, change_frame], ignore_index=True, sort=False) if not details.empty else change_frame


def analyze_payloads(category: str, payloads: Iterable[Tuple[str, bytes]]) -> AnalysisResult:
    """Parse multiple uploaded or local files for one output category."""
    if category not in _PARSERS:
        raise ValueError(f"Unsupported ExaWatcher category: {category}")
    metadata_rows: List[Dict[str, object]] = []
    record_frames: List[pd.DataFrame] = []
    detail_frames: List[pd.DataFrame] = []
    warnings: List[str] = []
    for source, payload in payloads:
        if len(payload) > MAX_FILE_BYTES:
            warnings.append(f"{source}: skipped because it exceeds the 200 MiB per-file limit.")
            continue
        try:
            text = decode_file(source, payload)
            parsed = _parse_one(category, source, text)
            metadata_rows.append(parsed.metadata)
            if parsed.records:
                record_frames.append(pd.DataFrame.from_records(parsed.records))
            if parsed.details:
                detail_frames.append(pd.DataFrame.from_records(parsed.details))
            warnings.extend(parsed.warnings)
        except Exception as exc:
            warnings.append(f"{source}: could not be read: {type(exc).__name__}: {exc}")
    metadata = pd.DataFrame(metadata_rows)
    records = pd.concat(record_frames, ignore_index=True, copy=False) if record_frames else pd.DataFrame()
    details = pd.concat(detail_frames, ignore_index=True, copy=False) if detail_frames else pd.DataFrame()
    if not records.empty:
        records["timestamp"] = pd.to_datetime(records.get("timestamp"), errors="coerce")
        group_keys = [column for column in ("timestamp", "source_file", "entity") if column in records]
        if len(group_keys) == 3 and records.duplicated(group_keys).any():
            records = records.groupby(group_keys, as_index=False, sort=False, dropna=False).first()
        sort_columns = [column for column in ("timestamp", "source_file", "entity") if column in records]
        records.sort_values(sort_columns, inplace=True, kind="stable")
        if category == "Diskinfo":
            derived = _derive_diskinfo(records, warnings)
            if not derived.empty:
                records = derived
        if category == "TopPid" and {"pid", "command", "timestamp"}.issubset(records.columns):
            ordered = records.sort_values(["pid", "timestamp"], kind="mergesort")
            prior_command = ordered.groupby("pid", sort=False)["command"].shift(1)
            changed = prior_command.notna() & ordered["command"].ne(prior_command)
            records.loc[ordered.index, "possible_pid_reuse"] = changed.to_numpy()
            changed_pids = ordered.loc[changed, "pid"].drop_duplicates().tolist()
            if changed_pids:
                examples = ", ".join(str(pid) for pid in changed_pids[:10])
                suffix = " ..." if len(changed_pids) > 10 else ""
                warnings.append(
                    f"TopPid: command identity changed for {len(changed_pids)} PID(s) ({examples}{suffix}); "
                    "this can indicate PID reuse or exec and is not proof of either."
                )
        for column in ("entity", "source_file", "state", "user", "command", "section", "descriptor_type"):
            if column in records and records[column].nunique(dropna=False) < max(5000, len(records) // 3):
                records[column] = records[column].astype("category")
    if not details.empty and "timestamp" in details:
        details["timestamp"] = pd.to_datetime(details["timestamp"], errors="coerce")
        for column in ("source_file", "entity", "section", "status", "pid", "ppid", "user", "command", "fd", "type"):
            if column in details and details[column].nunique(dropna=False) < max(1000, len(details) // 3):
                details[column] = details[column].astype("category")
    coverage_frame = records if not records.empty else details
    warnings.extend(_coverage_warnings(coverage_frame, metadata))
    details = _snapshot_change_rows(category, records, details)
    warnings = list(dict.fromkeys(warnings))
    return AnalysisResult(category, metadata, records, details, warnings)


def metric_columns(frame: pd.DataFrame) -> List[str]:
    return _metric_columns(frame)


def bytes_for_supported_file(path: Path) -> bool:
    lower = path.name.lower()
    return path.is_file() and (lower.endswith(".dat") or lower.endswith(".dat.xz") or lower.endswith(".dat.bz2"))

