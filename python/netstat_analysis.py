"""Parsing and analysis helpers for ExaWatcher netstat output.

The ExaWatcher netstat collector writes cumulative counters inside blocks that
start with ``zzz <timestamp>``.  This module deliberately uses only the Python
standard library so the parser can be smoke-tested without Streamlit or
pandas.  The Streamlit UI in :mod:`Netstat_Analyzer` converts the returned
dataclasses to tables and charts.
"""

from __future__ import annotations

import bz2
import lzma
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MAX_FILE_SIZE_MB = 200


COUNTER_LABELS = {
    "ip_in_octets": "IP input octets",
    "ip_out_octets": "IP output octets",
    "ip_total_packets_received": "IP packets received",
    "ip_requests_sent": "IP requests sent",
    "tcp_retransmitted": "TCP segments retransmitted",
    "tcp_timeouts": "TCP timeouts",
    "tcp_sent_segments": "TCP segments sent",
    "tcp_received_segments": "TCP segments received",
    "tcp_failed_connections": "TCP failed connection attempts",
    "tcp_resets_received": "TCP connection resets received",
    "udp_receive_errors": "UDP packet receive errors",
    "udp_receive_buffer_errors": "UDP receive buffer errors",
    "ip_invalid_addresses": "IP packets with invalid addresses",
    "ip_incoming_discarded": "IP incoming packets discarded",
    "ip_route_drops": "IP packets dropped for missing route",
    "ip_fragments_received": "IP fragments received",
    "ip_fragments_created": "IP fragments created",
    "tcp_fast_retransmits": "TCP fast retransmits",
    "tcp_syn_retrans": "TCP SYN retransmissions",
    "tcp_backlog_drops": "TCP backlog drops",
}

COUNTER_KEYS = tuple(COUNTER_LABELS)
INTERFACE_COUNTER_KEYS = (
    "rx_ok",
    "rx_err",
    "rx_drop",
    "rx_ovr",
    "tx_ok",
    "tx_err",
    "tx_drop",
    "tx_ovr",
)


@dataclass(frozen=True)
class NetstatMetadata:
    source_file: str
    starting_time: Optional[datetime]
    sample_interval: Optional[int]
    archive_count: Optional[int]
    collection_command: str
    host: str


@dataclass
class NetstatSample:
    timestamp: datetime
    group: str
    source_file: str
    counters: Dict[str, int] = field(default_factory=dict)
    interfaces: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class NetstatInterval:
    timestamp: datetime
    group: str
    source_file: str
    dt_seconds: float
    in_mbps: float
    out_mbps: float
    total_mbps: float
    counter_deltas: Dict[str, Optional[int]] = field(default_factory=dict)
    interface_deltas: Dict[str, Dict[str, Optional[int]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    severity: str
    title: str
    detail: str
    recommendation: str
    group: str = "all"
    source_file: str = ""
    timestamp: Optional[datetime] = None


_MARKER_RE = re.compile(r"^\s*zzz\s*<(?P<timestamp>[^>]+)>", re.IGNORECASE)
_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}(?:\s+[AP]M)?$", re.IGNORECASE)


def decode_bytes(name: str, data: bytes) -> str:
    """Decode a plain, ``.xz``, or ``.bz2`` ExaWatcher file."""

    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".xz":
            data = lzma.decompress(data)
        elif suffix == ".bz2":
            data = bz2.decompress(data)
    except (lzma.LZMAError, OSError, EOFError):
        # Keep the UI useful when a file was misnamed or partially copied.
        # The caller can still see the resulting parse warning.
        pass
    return data.decode("utf-8", errors="replace")


def _normalize_datetime(value: datetime) -> datetime:
    """Return a comparable naive UTC datetime for report timestamps."""

    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def parse_timestamp(value: str, default_date: Optional[date] = None) -> Optional[datetime]:
    """Parse common ExaWatcher, Linux, and ISO timestamp forms."""

    cleaned = value.strip().strip("<>").strip()
    if not cleaned:
        return None

    iso_candidate = cleaned[:-1] + "+00:00" if cleaned.endswith(("Z", "z")) else cleaned
    try:
        return _normalize_datetime(datetime.fromisoformat(iso_candidate))
    except ValueError:
        pass

    formats = (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    )
    for fmt in formats:
        try:
            return _normalize_datetime(datetime.strptime(cleaned, fmt))
        except ValueError:
            continue

    if default_date is not None:
        for fmt in ("%H:%M:%S", "%I:%M:%S %p"):
            try:
                parsed_time = datetime.strptime(cleaned, fmt).time()
                return datetime.combine(default_date, parsed_time)
            except ValueError:
                continue
    return None


def _header_value(lines: Sequence[str], key: str) -> str:
    prefix = f"{key.lower()}:"
    for raw in lines:
        line = raw.strip().lstrip("#").strip()
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _header_int(lines: Sequence[str], key: str) -> Optional[int]:
    value = _header_value(lines, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_host(lines: Sequence[str]) -> str:
    for raw in lines:
        line = raw.strip()
        if not line.startswith("Linux "):
            continue
        # ExaWatcher commonly uses the iostat/sar form:
        # ``Linux <kernel-release> (<host>) ...``.
        parenthesized = re.search(r"Linux\s+\S+\s+\((?P<host>[^)]+)\)", line)
        if parenthesized:
            return parenthesized.group("host").strip()
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in {"version", "kernel"}:
            return parts[1]
    return ""


def parse_metadata(text: str, source_file: str) -> NetstatMetadata:
    lines = text.splitlines()
    starting_text = _header_value(lines, "Starting Time")
    starting_time = parse_timestamp(starting_text) if starting_text else None
    return NetstatMetadata(
        source_file=source_file,
        starting_time=starting_time,
        sample_interval=_header_int(lines, "Sample Interval(s)"),
        archive_count=_header_int(lines, "Archive Count"),
        collection_command=_header_value(lines, "Collection Command"),
        host=_parse_host(lines),
    )


def _integer(value: str) -> Optional[int]:
    if value in {"-", "–", "—"}:
        return None
    try:
        return int(value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _normalize_interface_column(value: str) -> str:
    token = value.strip().lower().replace("_", "-")
    aliases = {
        "rx-ok": "rx_ok",
        "rxerr": "rx_err",
        "rx-err": "rx_err",
        "rxdrp": "rx_drop",
        "rx-drp": "rx_drop",
        "rxdrop": "rx_drop",
        "rxovr": "rx_ovr",
        "rx-ovr": "rx_ovr",
        "tx-ok": "tx_ok",
        "txerr": "tx_err",
        "tx-err": "tx_err",
        "txdrp": "tx_drop",
        "tx-drp": "tx_drop",
        "txdrop": "tx_drop",
        "txovr": "tx_ovr",
        "tx-ovr": "tx_ovr",
    }
    return aliases.get(token, token.replace("-", "_"))


def _interface_header(tokens: Sequence[str]) -> Optional[Dict[str, int]]:
    if not tokens or tokens[0].lower() not in {"iface", "interface"}:
        return None
    normalized = [_normalize_interface_column(token) for token in tokens]
    if "mtu" not in normalized or not any(item.startswith("rx_") for item in normalized):
        return None
    return {name: index for index, name in enumerate(normalized)}


def _parse_interface_row(
    tokens: Sequence[str], header: Optional[Mapping[str, int]]
) -> Optional[Tuple[str, Dict[str, int]]]:
    if len(tokens) < 2 or tokens[0].endswith(":") or _integer(tokens[1]) is None:
        return None
    interface = tokens[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", interface):
        return None

    if header is None:
        # Linux netstat -i without RX/TX header variations used by older
        # ExaWatcher releases.  The positions match the classic table:
        # Iface MTU RX-OK RX-ERR RX-DRP RX-OVR TX-OK TX-ERR TX-DRP TX-OVR Flg
        positions = {
            "rx_ok": 2,
            "rx_err": 3,
            "rx_drop": 4,
            "rx_ovr": 5,
            "tx_ok": 6,
            "tx_err": 7,
            "tx_drop": 8,
            "tx_ovr": 9,
        }
    else:
        positions = {key: header[key] for key in INTERFACE_COUNTER_KEYS if key in header}

    if not positions or max(positions.values()) >= len(tokens):
        return None
    values: Dict[str, int] = {}
    for key, index in positions.items():
        parsed = _integer(tokens[index])
        if parsed is not None:
            values[key] = parsed
    return (interface, values) if values else None


def _match_value(pattern: re.Pattern[str], line: str) -> Optional[int]:
    match = pattern.search(line)
    if not match:
        return None
    for group in match.groups():
        if group is not None:
            return int(group)
    return None


_COUNTER_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("ip_in_octets", re.compile(r"\bInOctets\s*:?\s*(\d+)", re.IGNORECASE)),
    ("ip_out_octets", re.compile(r"\bOutOctets\s*:?\s*(\d+)", re.IGNORECASE)),
    (
        "ip_total_packets_received",
        re.compile(r"^\s*(\d+)\s+total\s+packets\s+received\b", re.IGNORECASE),
    ),
    (
        "ip_requests_sent",
        re.compile(r"^\s*(\d+)\s+requests\s+sent\s+out\b", re.IGNORECASE),
    ),
    (
        "tcp_retransmitted",
        re.compile(r"^\s*(\d+)\s+segments\s+retransmitted\b", re.IGNORECASE),
    ),
    ("tcp_timeouts", re.compile(r"\bTCPTimeouts\s*:?\s*(\d+)", re.IGNORECASE)),
    (
        "tcp_received_segments",
        re.compile(r"^\s*(\d+)\s+segments\s+received\b", re.IGNORECASE),
    ),
    (
        "tcp_sent_segments",
        re.compile(r"^\s*(\d+)\s+segments\s+sent\b", re.IGNORECASE),
    ),
    (
        "tcp_failed_connections",
        re.compile(r"^\s*(\d+)\s+failed\s+connection(?:\s+attempts?)?\b", re.IGNORECASE),
    ),
    (
        "tcp_resets_received",
        re.compile(r"^\s*(\d+)\s+connection\s+resets\s+received\b", re.IGNORECASE),
    ),
    (
        "udp_receive_errors",
        re.compile(r"^\s*(\d+)\s+packet\s+receive\s+errors\b", re.IGNORECASE),
    ),
    (
        "udp_receive_buffer_errors",
        re.compile(r"^\s*(\d+)\s+receive\s+buffer\s+errors\b", re.IGNORECASE),
    ),
    (
        "ip_invalid_addresses",
        re.compile(r"^\s*(\d+)\s+with\s+invalid\s+addresses\b", re.IGNORECASE),
    ),
    (
        "ip_incoming_discarded",
        re.compile(r"^\s*(\d+)\s+incoming\s+packets\s+discarded\b", re.IGNORECASE),
    ),
    (
        "ip_route_drops",
        re.compile(r"^\s*(\d+)\s+dropped\s+because\s+of\s+missing\s+route\b", re.IGNORECASE),
    ),
    (
        "ip_fragments_received",
        re.compile(r"^\s*(\d+)\s+fragments\s+received\s+ok\b", re.IGNORECASE),
    ),
    (
        "ip_fragments_created",
        re.compile(r"^\s*(\d+)\s+fragments\s+created\b", re.IGNORECASE),
    ),
    (
        "tcp_fast_retransmits",
        re.compile(r"^\s*(\d+)\s+fast\s+retransmits\b", re.IGNORECASE),
    ),
    (
        "tcp_syn_retrans",
        re.compile(r"(?:\bTCPSynRetrans\s*:?\s*(\d+)|^\s*(\d+)\s+TCPSynRetrans\b)", re.IGNORECASE),
    ),
    (
        "tcp_backlog_drops",
        re.compile(r"(?:\bTCPBacklogDrop\s*:?\s*(\d+)|^\s*(\d+)\s+TCPBacklogDrop\b)", re.IGNORECASE),
    ),
)


def _counter_from_line(line: str) -> Optional[Tuple[str, int]]:
    for key, pattern in _COUNTER_PATTERNS:
        value = _match_value(pattern, line)
        if value is not None:
            return key, value
    return None


def parse_text(text: str, source_file: str) -> Tuple[NetstatMetadata, List[NetstatSample]]:
    """Parse one ExaWatcher netstat capture into timestamped samples."""

    metadata = parse_metadata(text, source_file)
    group = metadata.host or source_file
    default_date = metadata.starting_time.date() if metadata.starting_time else None
    samples: List[NetstatSample] = []
    current: Optional[NetstatSample] = None
    interface_header: Optional[Dict[str, int]] = None

    def finish() -> None:
        nonlocal current
        if current is not None and (current.counters or current.interfaces):
            samples.append(current)
        current = None

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        marker = _MARKER_RE.match(line)
        if marker:
            finish()
            timestamp = parse_timestamp(marker.group("timestamp"), default_date)
            if timestamp is not None:
                current = NetstatSample(timestamp, group, source_file)
            interface_header = None
            continue

        if current is None:
            continue

        tokens = line.split()
        if not tokens:
            continue

        header = _interface_header(tokens)
        if header is not None:
            interface_header = header
            continue

        interface_row = _parse_interface_row(tokens, interface_header)
        if interface_row is not None:
            name, values = interface_row
            current.interfaces[name] = values
            continue

        counter = _counter_from_line(line)
        if counter is not None:
            key, value = counter
            current.counters[key] = value

    finish()
    return metadata, samples


def merge_samples(samples: Iterable[NetstatSample]) -> List[NetstatSample]:
    """Sort samples and merge exact timestamp duplicates from overlapping archives."""

    merged: Dict[Tuple[str, datetime], NetstatSample] = {}
    for sample in samples:
        key = (sample.group, sample.timestamp)
        existing = merged.get(key)
        if existing is None:
            merged[key] = sample
            continue
        # Keep the first source for reproducible provenance, but fill fields
        # that a duplicate archive captured while the first did not.
        for counter, value in sample.counters.items():
            existing.counters.setdefault(counter, value)
        for interface, values in sample.interfaces.items():
            existing.interfaces.setdefault(interface, {})
            for counter, value in values.items():
                existing.interfaces[interface].setdefault(counter, value)

    return sorted(merged.values(), key=lambda item: (item.group, item.timestamp))


def _delta(previous: Optional[int], current: Optional[int]) -> Optional[int]:
    if previous is None or current is None or current < previous:
        return None
    return current - previous


def build_intervals(
    samples: Iterable[NetstatSample], max_gap_seconds: float = 120
) -> List[NetstatInterval]:
    """Build rate intervals, skipping gaps and cumulative-counter resets."""

    by_group: Dict[str, List[NetstatSample]] = defaultdict(list)
    for sample in samples:
        by_group[sample.group].append(sample)

    intervals: List[NetstatInterval] = []
    for group, group_samples in by_group.items():
        ordered = sorted(group_samples, key=lambda item: item.timestamp)
        for previous, current in zip(ordered, ordered[1:]):
            dt = (current.timestamp - previous.timestamp).total_seconds()
            if dt <= 0 or dt > max_gap_seconds:
                continue
            in_delta = _delta(
                previous.counters.get("ip_in_octets"), current.counters.get("ip_in_octets")
            )
            out_delta = _delta(
                previous.counters.get("ip_out_octets"), current.counters.get("ip_out_octets")
            )
            if in_delta is None or out_delta is None:
                continue

            in_mbps = in_delta * 8 / dt / 1_000_000
            out_mbps = out_delta * 8 / dt / 1_000_000
            counter_deltas = {
                key: _delta(previous.counters.get(key), current.counters.get(key))
                for key in COUNTER_KEYS
            }
            interface_deltas: Dict[str, Dict[str, Optional[int]]] = {}
            for name in sorted(set(previous.interfaces) | set(current.interfaces)):
                previous_values = previous.interfaces.get(name, {})
                current_values = current.interfaces.get(name, {})
                interface_deltas[name] = {
                    key: _delta(previous_values.get(key), current_values.get(key))
                    for key in INTERFACE_COUNTER_KEYS
                }
            intervals.append(
                NetstatInterval(
                    timestamp=current.timestamp,
                    group=group,
                    source_file=current.source_file,
                    dt_seconds=dt,
                    in_mbps=in_mbps,
                    out_mbps=out_mbps,
                    total_mbps=in_mbps + out_mbps,
                    counter_deltas=counter_deltas,
                    interface_deltas=interface_deltas,
                )
            )

    return sorted(intervals, key=lambda item: (item.group, item.timestamp))


def percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def interval_rows(
    intervals: Iterable[NetstatInterval], aggregate_capacity_gbps: float = 0
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in intervals:
        row: Dict[str, object] = {
            "timestamp": item.timestamp,
            "group": item.group,
            "source_file": item.source_file,
            "dt_seconds": item.dt_seconds,
            "in_mbps": item.in_mbps,
            "out_mbps": item.out_mbps,
            "total_mbps": item.total_mbps,
            "in_gbps": item.in_mbps / 1_000,
            "out_gbps": item.out_mbps / 1_000,
            "total_gbps": item.total_mbps / 1_000,
        }
        if aggregate_capacity_gbps > 0:
            row["utilization_pct"] = item.total_mbps / (aggregate_capacity_gbps * 1_000) * 100
        sent = item.counter_deltas.get("tcp_sent_segments")
        retransmitted = item.counter_deltas.get("tcp_retransmitted")
        row["tcp_retransmission_pct"] = (
            retransmitted / sent * 100 if sent is not None and sent > 0 and retransmitted is not None else None
        )
        for key in COUNTER_KEYS:
            row[f"{key}_delta"] = item.counter_deltas.get(key)
        rows.append(row)
    return rows


def sample_rows(samples: Iterable[NetstatSample]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in samples:
        row: Dict[str, object] = {
            "timestamp": item.timestamp,
            "group": item.group,
            "source_file": item.source_file,
            "interface_count": len(item.interfaces),
        }
        row.update(item.counters)
        rows.append(row)
    return rows


def interface_delta_rows(intervals: Iterable[NetstatInterval]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in intervals:
        for interface, values in item.interface_deltas.items():
            row: Dict[str, object] = {
                "timestamp": item.timestamp,
                "group": item.group,
                "source_file": item.source_file,
                "interface": interface,
                "dt_seconds": item.dt_seconds,
            }
            row.update({f"{key}_delta": value for key, value in values.items()})
            rows.append(row)
    return rows


def throughput_summary(intervals: Iterable[NetstatInterval]) -> List[Dict[str, object]]:
    materialized = list(intervals)
    output: List[Dict[str, object]] = []
    for label, attribute in (
        ("in_mbps", "in_mbps"),
        ("out_mbps", "out_mbps"),
        ("total_mbps", "total_mbps"),
    ):
        values = [float(getattr(item, attribute)) for item in materialized]
        if not values:
            continue
        peak = max(materialized, key=lambda item: float(getattr(item, attribute)))
        output.append(
            {
                "metric": label,
                "min_mbps": min(values),
                "avg_mbps": sum(values) / len(values),
                "p95_mbps": percentile(values, 0.95),
                "p99_mbps": percentile(values, 0.99),
                "max_mbps": max(values),
                "peak_timestamp": peak.timestamp,
                "peak_group": peak.group,
            }
        )
    return output


def counter_summary(intervals: Iterable[NetstatInterval]) -> List[Dict[str, object]]:
    materialized = list(intervals)
    output: List[Dict[str, object]] = []
    for key in COUNTER_KEYS:
        values = [
            item.counter_deltas.get(key)
            for item in materialized
            if item.counter_deltas.get(key) is not None
        ]
        if not values:
            continue
        output.append(
            {
                "counter": key,
                "label": COUNTER_LABELS[key],
                "total_delta": sum(values),
                "max_interval_delta": max(values),
                "event_intervals": sum(1 for value in values if value > 0),
                "observed_intervals": len(values),
            }
        )
    return output


def interface_summary(intervals: Iterable[NetstatInterval]) -> List[Dict[str, object]]:
    totals: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    maxes: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    observed: Dict[Tuple[str, str], int] = defaultdict(int)
    for item in intervals:
        for interface, values in item.interface_deltas.items():
            key = (item.group, interface)
            observed[key] += 1
            for counter, value in values.items():
                if value is None:
                    continue
                totals[key][counter] += value
                maxes[key][counter] = max(maxes[key][counter], value)

    rows: List[Dict[str, object]] = []
    for (group, interface), values in sorted(totals.items()):
        row: Dict[str, object] = {
            "group": group,
            "interface": interface,
            "observed_intervals": observed[(group, interface)],
        }
        for counter in INTERFACE_COUNTER_KEYS:
            row[f"total_{counter}"] = values.get(counter, 0)
            row[f"max_{counter}_interval"] = maxes[(group, interface)].get(counter, 0)
        rows.append(row)
    return rows


def _peak(intervals: Sequence[NetstatInterval], attribute: str) -> Optional[NetstatInterval]:
    return max(intervals, key=lambda item: float(getattr(item, attribute))) if intervals else None


def findings_for_intervals(
    intervals: Iterable[NetstatInterval], aggregate_capacity_gbps: float = 0
) -> List[Finding]:
    """Generate conservative network/fabric findings from interval deltas."""

    materialized = list(intervals)
    if not materialized:
        return [
            Finding(
                severity="Warning",
                title="No complete throughput intervals",
                detail=(
                    "At least two timestamped samples with non-reset InOctets and "
                    "OutOctets are required to calculate network rates."
                ),
                recommendation=(
                    "Check that the capture contains netstat -s -e output and that "
                    "sample gaps are not larger than the configured maximum."
                ),
            )
        ]

    findings: List[Finding] = []
    by_group: Dict[str, List[NetstatInterval]] = defaultdict(list)
    for item in materialized:
        by_group[item.group].append(item)

    for group, group_intervals in by_group.items():
        if aggregate_capacity_gbps > 0:
            peak = _peak(group_intervals, "total_mbps")
            if peak is not None:
                utilization = peak.total_mbps / (aggregate_capacity_gbps * 1_000) * 100
                if utilization >= 95:
                    findings.append(
                        Finding(
                            severity="Critical",
                            title="Network capacity nearly saturated",
                            detail=(
                                f"{group} reached {peak.total_mbps / 1_000:.2f} Gbps "
                                f"({utilization:.1f}% of the configured {aggregate_capacity_gbps:.2f} Gbps aggregate capacity)."
                            ),
                            recommendation=(
                                "Correlate this interval with application latency, NIC/link "
                                "counters, and ExaWatcher Rocestat/RDSinfo before changing traffic paths."
                            ),
                            group=group,
                            source_file=peak.source_file,
                            timestamp=peak.timestamp,
                        )
                    )
                elif utilization >= 80:
                    findings.append(
                        Finding(
                            severity="Warning",
                            title="High network capacity utilization",
                            detail=(
                                f"{group} reached {peak.total_mbps / 1_000:.2f} Gbps "
                                f"({utilization:.1f}% of the configured {aggregate_capacity_gbps:.2f} Gbps aggregate capacity)."
                            ),
                            recommendation=(
                                "Review the sustained throughput window and correlate with "
                                "Rocestat/RDSinfo congestion and host/application symptoms."
                            ),
                            group=group,
                            source_file=peak.source_file,
                            timestamp=peak.timestamp,
                        )
                    )

        sent = sum(
            item.counter_deltas.get("tcp_sent_segments") or 0 for item in group_intervals
        )
        retransmitted = sum(
            item.counter_deltas.get("tcp_retransmitted") or 0 for item in group_intervals
        )
        if sent > 0 and retransmitted > 0:
            ratio = retransmitted / sent * 100
            severity = "Critical" if ratio >= 5 else "Warning" if ratio >= 1 else "Info"
            if severity != "Info":
                peak = max(
                    group_intervals,
                    key=lambda item: item.counter_deltas.get("tcp_retransmitted") or 0,
                )
                findings.append(
                    Finding(
                        severity=severity,
                        title="Elevated TCP retransmission ratio",
                        detail=(
                            f"{group} retransmitted {retransmitted:,} of {sent:,} sent "
                            f"segments ({ratio:.4f}%) across valid intervals."
                        ),
                        recommendation=(
                            "Correlate the retransmission timestamps with NIC errors/drops, "
                            "Rocestat/RDSinfo, MTU/path changes, and the remote endpoint."
                        ),
                        group=group,
                        source_file=peak.source_file,
                        timestamp=peak.timestamp,
                    )
                )

        event_counters = (
            "tcp_timeouts",
            "udp_receive_errors",
            "udp_receive_buffer_errors",
            "ip_incoming_discarded",
            "ip_route_drops",
            "tcp_backlog_drops",
        )
        for key in event_counters:
            total = sum(item.counter_deltas.get(key) or 0 for item in group_intervals)
            if total <= 0:
                continue
            peak = max(group_intervals, key=lambda item: item.counter_deltas.get(key) or 0)
            findings.append(
                Finding(
                    severity="Warning",
                    title=f"{COUNTER_LABELS[key]} increased",
                    detail=f"{group} recorded a delta of {total:,} for {COUNTER_LABELS[key].lower()}.",
                    recommendation=(
                        "Align the event timestamp with interface counters and application "
                        "symptoms; cumulative netstat counters alone do not identify the fault domain."
                    ),
                    group=group,
                    source_file=peak.source_file,
                    timestamp=peak.timestamp,
                )
            )

        by_interface: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for item in group_intervals:
            for interface, values in item.interface_deltas.items():
                for key in ("rx_err", "rx_drop", "rx_ovr", "tx_err", "tx_drop", "tx_ovr"):
                    by_interface[interface][key] += values.get(key) or 0
        for interface, values in by_interface.items():
            total = sum(values.values())
            if total <= 0:
                continue
            peak = next(
                item
                for item in group_intervals
                if sum(
                    (item.interface_deltas.get(interface, {}).get(key) or 0)
                    for key in ("rx_err", "rx_drop", "rx_ovr", "tx_err", "tx_drop", "tx_ovr")
                )
                > 0
            )
            details = ", ".join(f"{key}={value:,}" for key, value in values.items() if value)
            findings.append(
                Finding(
                    severity="Warning",
                    title=f"Interface errors or drops on {interface}",
                    detail=f"{group} observed {details} across valid intervals.",
                    recommendation=(
                        "Check the physical/logical link, driver statistics, bonding state, "
                        "MTU consistency, and matching ExaWatcher fabric collectors."
                    ),
                    group=group,
                    source_file=peak.source_file,
                    timestamp=peak.timestamp,
                )
            )

    severity_order = {"Critical": 0, "Warning": 1, "Info": 2}
    findings.sort(
        key=lambda item: (
            severity_order.get(item.severity, 99),
            item.timestamp or datetime.min,
            item.group,
        )
    )
    return findings
