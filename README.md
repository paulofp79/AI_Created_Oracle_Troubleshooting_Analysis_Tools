# Oracle Exadata Troubleshooting Analysis Tools

A collection of browser-based and Python tools for analyzing and visualizing Oracle Exadata performance metrics, designed to help DBAs and SREs troubleshoot issues efficiently.

**Author:** Paulo Portugal - Oracle XTeam
**Created:** August 2025

---

## Table of Contents

- [Overview](#overview)
- [Tools Included](#tools-included)
- [Installation](#installation)
- [Usage](#usage)
- [Input File Formats](#input-file-formats)
- [Project Structure](#project-structure)
- [Contributing](#contributing)

---

## Overview

This toolkit provides interactive visualization and analysis capabilities for:

- **CPU utilization** from ATP (Autonomous Transaction Processing) files
- **Cell disk metrics** from ECStatJSONExaWatcher data
- **RDS congestion counters** from ExaWatcher/AHF data
- **VMStat memory/swap analysis** with anomaly detection
- **Oracle alert log parsing** with timeline visualization
- **ExaCC metrics dashboards** with performance alerts
- **Smart Flash Cache content** by database, object, object type, hit ratio, and storage cell
- **Network traffic and health** from ExaWatcher netstat output, including `.xz` archives

All HTML-based tools run entirely in the browser with no server required. The Python tool uses Streamlit for an interactive web interface.

---

## Tools Included

### 1. ECS_Analysis.py (Streamlit App)
**Purpose:** Interactive analysis of ECStatJSONExaWatcher cell disk metrics

**Features:**
- Parses `.dat` files with JSON blocks marked by `zzz <...>` delimiters
- Visualizes SD (Storage Disk) vs MD (Flash Memory Disk) metrics
- Supports IOPS/s and MB/s unit conversion
- Delta calculation for cumulative counters
- Interactive metric selection and filtering

### 2. Netstat_Analyzer.py (Streamlit App)
**Purpose:** Analyze ExaWatcher netstat output for network throughput and health signals

**Features:**
- Upload plain text, `.xz`, or `.bz2` netstat captures, or provide server-side paths
- Calculates inbound, outbound, and total peak/average/p95/p99 throughput from `InOctets` and `OutOctets`
- Optionally calculates utilization against a user-supplied aggregate bidirectional capacity
- Delta-aligns TCP/IP counters and interface RX/TX errors, drops, and overruns
- Highlights retransmission ratios, timeouts, packet drops, and interface events with source timestamps
- CSV downloads for samples, rate intervals, and interface deltas

### 3. CPU_Charts_From_ATP_Files.html
**Purpose:** Visualize CPU utilization from zipped CPUManager JSON exports

**Features:**
- Drag-and-drop file upload
- Multi-file batch processing
- CSV export capability
- Interactive chart with series toggles

### 4. RDS_Info_Analysis.html
**Purpose:** Analyze RDS (Reliable Datagram Sockets) counters and peer latency

**Features:**
- Multi-file upload (supports `.xz` compression)
- Timestamp-aligned counter trend visualization
- Statistics calculation (total increase, per-interval increase)
- Automatic knowledge-base findings for increasing RDS counters
- **Network Flow:** animated source-to-target probe paths, sample playback, queue filters, mean/P95/maximum latency, selectable nodes/routes, a zoomable timeline and route heatmap, PNG export, and CSV export
- Apache ECharts 6.1.0; responsive layout and reduced-motion support
- Key congestion counter reference

Upload files once, then open **Network Flow**. The network requires timestamped
`rds-ping -I <source IP> <target IP>: ... usec` results. It displays measured
round-trip latency, not AWR block transfers or traffic volume. Missing routes
remain unmeasured, overlapping observations are deduplicated, and the highlight
threshold is user-configurable. Frames retain source-clock timestamps; multiple
hosts share a frame only when their collection markers match exactly. CSV
exports all collection times for the selected queue and node/route, regardless
of the network's top-route limit. Keep `html/rds_network.js` and
`html/rds_network.css` beside the HTML page when deploying it.

Validate network data calculations with `node --test tests/rds_network.test.js`.

### 5. Exa_Cell_Metrics_Chart.html
**Purpose:** ExaCC (Exadata Cloud@Customer) metrics dashboard

**Features:**
- Line and bar chart visualizations
- Alert tabs for Flash Cache, Smart I/O, and Cell Disk I/O
- Color-coded severity levels (Normal/Warning/Critical)
- Click-to-navigate from alerts to charts

### 6. alertlog_analyzer.html
**Purpose:** Parse and analyze Oracle alert log files

**Features:**
- Directory-based file selection
- Instance name and exclude pattern filters
- Date/time range filtering
- Keyword search with context lines
- Syntax highlighting for ORA- errors, FATAL, FAIL
- Interactive charts use Apache ECharts 6.1.0

### 7. vmstat_multi_plot_with_swap_alerts_highlight.html
**Purpose:** Analyze vmstat data for memory pressure issues

**Features:**
- Multi-file continuous timeline stitching
- Separate plots for Memory+CPU, Swap In, Swap Out
- Automatic swap utilization alerts
- CSV export

### 8. FlashcacheContent Analysis Tool
**Purpose:** Collect and explore Exadata Smart Flash Cache contents to identify cache consumers, cold-cache candidates, and cell skew.

**Workflow:**

1. On an Exadata database node as `root`, run `scripts/extract_flashcache.sh`. It uses `dcli` and the cell group file to collect `flashcachecontent` from every cell into a timestamped CSV.
2. Optionally run `sql/flashcache_views.sql` in the database to create the external table and rollup views, including `fc_by_object` and `fc_cold_cache_candidates`.
3. Open `html/flashcache_dashboard.html` from the main dashboard and upload either the raw collector CSV or a CSV export of `fc_by_object`.

**Features:**
- Browser-only CSV analysis; uploaded data remains local to the browser
- Per-object cache footprint, hit ratio, and cold-cache filtering
- Database and object-type cache distribution
- Storage-cell cache-skew visualization when raw collector CSV is used
- `CELL_FLASH_CACHE` tuning candidate visibility through the optional SQL views

### 9. ExaWatcher Analyzer Workbench
**Purpose:** Analyze ExaWatcher collectors that do not already have a dedicated tool.

**Output views:** ACFS, Diskinfo, EDVStat, IBCardInfo, IBprocs, Lsof, Meminfo, Mpstat, Netinfo, Nfsiostat, Numa, Ps, Roceaudit, Rocestat, Top, and TopPid.

**Features:**
- Load a capture-root directory or upload one or more plain, `.xz`, or `.bz2` output files
- Find the selected collector's `<Name>.ExaWatcher` directory under a capture root
- Chart timestamped metrics with entity filters, show capture metadata and gaps, and download chart or detail rows as CSV
- Keep snapshot reports searchable and distinguish incomplete or skipped output from zero-valued measurements

Start the workbench on port 8505:

```bash
streamlit run python/ExaWatcher_Workbench.py --server.port 8505
```

---

## AWR RAC Transfer Explorer

A separate Streamlit application on **port 8506** for historical RAC cache
transfers in an Oracle repository containing imported customer AWR data.
The file-based RDS analyzer continues to provide ping and OS-counter analysis.

```bash
python3 -m streamlit run python/AWR_RAC_Transfer_Explorer.py --server.port 8506
```

Use the repository's Python environment with `requirements.txt` installed
(Python 3.10+ recommended for current Streamlit releases). The dashboard's
**Python Tools** tab links to this service. `startall.sh`, `stopall.sh`, and
`status.sh` include it; its log is `streamlit_awr_transfer.log`. Run service
commands on the application host, which may differ from the source checkout.

1. Connect with host, port, service, and Oracle credentials. Passwords remain
   in Streamlit session memory; results are not shared through a global cache.
2. Select the customer DBID. The default window is the latest seven days
   actually retained in `DBA_HIST_SNAPSHOT`, using source-clock timestamps.
3. Load snapshots and available scopes. Select a snapshot container context
   and an exact `(CON_DBID, CON_ID)` transfer/cache scope.
4. Select **Generate / refresh dashboard**. Playback, scrubbing, block-class
   filters, route ranking, node/route selection, and matrix navigation then
   operate on the fetched dataset without querying Oracle.
5. Use **Reconcile a route against independent repository SQL** to check
   selected intervals and routes. The report records which checks matched;
   untested intervals remain explicitly unverified.

The animated directed network, transfer/congestion timeline, cache-activity
timeline, and full route matrix use vendored **Apache ECharts 6.1.0**. Each
chart has a PNG export. Download a standalone offline HTML report, filtered
route CSV, full transfer CSV, or a ZIP with HTML, transfer/cache CSVs,
metadata, and executable SQL with the selected bind values substituted.

### Measurement and coverage

Only `DBA_HIST_DATABASE_INSTANCE`, `DBA_HIST_SNAPSHOT`,
`DBA_HIST_INST_CACHE_TRANSFER`, `DBA_HIST_SGA`, and `DBA_HIST_SYSSTAT` are
queried. Column support is inspected through those views themselves. No
internal `SYS.WRH$_` fallback is used. Snapshot and transfer data are
required; missing optional cache datasets leave transfer analysis available.

- Transfer direction is `INSTANCE` (sender) → `INSTANCE_NUMBER` (receiver).
- Counters use adjacent snapshots within the same startup, including a
  preceding snapshot for the first interval. Gaps, counter decreases,
  missing class rows, and source/destination restarts make affected routes
  unavailable. Missing values are never filled with zero.
- Each route's rate uses the receiving instance's measured elapsed seconds.
  Overlapping boundary intervals retain their full rates and actual bounds.
  Source timezone offsets are retained in per-instance exports when present.
- CR/current received counts include ordinary, busy, and congested counts.
  Congestion share is congested counts divided by received counts; combined
  percentages and transfer means use matching counts as weights.
- Transfer timing is hidden by default. To enable microsecond means, verify
  the `CR_BLOCK_TIME` / `CURRENT_BLOCK_TIME` unit and total-time semantics
  for the source release, select the unit, and record the evidence. Busy and
  congested times are not added again to those documented total-time columns.
- Cache memory is **allocated GiB** from `Database Buffers`. Cache logical
  reads use `db block gets from cache` plus `consistent gets from cache`;
  `physical reads cache` is a **miss proxy**. Hits are estimates derived by
  subtraction. A negative estimate is unavailable, not clipped to zero.
- CDB totals and PDB rows are separate scopes. An optional view that cannot
  establish the selected scope is reported unavailable rather than mixed.

The charts describe **historical transfers at AWR snapshot resolution**.
Animation illustrates direction, not individual block delivery. Local
synthetic tests do not establish repository connectivity, imported-data
coverage, source timing units, or agreement with a live Oracle repository.
Use the SQL reconciliation control and exported SQL for that validation.

Oracle reference:
[DBA_HIST_INST_CACHE_TRANSFER](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/DBA_HIST_INST_CACHE_TRANSFER.html).
ECharts license and notice are included in `html/vendor/`.

Run calculation and query regression tests without Oracle:

```bash
python3 -m unittest discover -s tests -p 'test_awr_transfer*.py' -v
```

`tests/awr_transfer_browser.cjs` exercises synthetic offline reports with
Playwright Chromium, including playback, canvas drilldown, filtering, weighted
averages, CSV/PNG export, gaps, reduced motion, and desktop/mobile layouts.
Install Playwright in a development environment, make it available through
`NODE_PATH`, and set `PYTHON_BIN` to the Python interpreter used for fixtures.

---

## Installation

### Prerequisites

- Modern web browser (Chrome, Firefox, Edge, Safari)
- Python 3.8+ (for ECS_Analysis.py only)

### Python Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install streamlit pandas
```

The startup scripts use a virtual environment at `.venv` by default. The
Python version used to create it is not fixed; Python 3.11, 3.12, or another
supported version may be used:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`startall.sh` and `stopall.sh` validate that this virtual environment exists
before changing application state. If a virtual environment is already
activated, the scripts use it automatically. Otherwise, to use a virtual
environment elsewhere, set either variable when invoking the scripts:

```bash
PYTHON_ENV_DIR=/opt/exadata-tools-venv ./startall.sh
PYTHON_BIN=/opt/exadata-tools-venv/bin/python ./startall.sh
```

Each service writes to its own log in the project directory: `exaweb.log`,
`streamlit_ecs.log`, `streamlit_awr.log`, `streamlit_iostat.log`,
`streamlit_netstat.log`, and `streamlit_exawatcher.log`. `startall.sh`
clears these logs before launching a fresh set of services. Run `./status.sh` to see
whether each configured port has a listening process and where its log is located.

---

## Usage

### HTML Tools

1. Open the main dashboard:
   ```bash
   # Simply open in your browser
   open index.html
   # or
   xdg-open index.html  # Linux
   ```

2. Or open individual tool HTML files directly in your browser.

### Running a Local Web Server (Optional)

To serve the tools over a network or access them from other machines, start a simple HTTP server:

```bash
# Start HTTP server in background (Linux)
nohup python3 -m http.server 8080 --directory /path/to/exadata-tools > /path/to/exadata-tools/server.log 2>&1 &

# Example with specific paths:
nohup python3 -m http.server 8080 --directory /home/paportug/exadata-tools > /home/paportug/exadata-tools/exaweb.log 2>&1 &
```

Then access the dashboard at: `http://your-server:8080`

To stop the server:
```bash
# Find the process
ps aux | grep "http.server"

# Kill it
kill <PID>
```

### Python Tool (ECS_Analysis.py)

```bash
# Run with Streamlit
python3 -m streamlit run python/ECS_Analysis.py --server.port 8501

# Or simply
streamlit run python/ECS_Analysis.py
```

Then open http://localhost:8501 in your browser.

### Python Tool (Netstat_Analyzer.py)

```bash
python3 -m streamlit run python/Netstat_Analyzer.py --server.port 8504
```

Then open http://localhost:8504 in your browser.

### ExaWatcher Analyzer Workbench

```bash
python3 -m streamlit run python/ExaWatcher_Workbench.py --server.port 8505
```

Then open http://localhost:8505 in your browser. Choose a collector view, then
enter the ExaWatcher capture-root directory on the app host or upload matching
`.dat`, `.dat.xz`, or `.dat.bz2` files. When given a capture root, the
workbench loads the selected `<Name>.ExaWatcher` child directory.

---

## Input File Formats

### ECS_Analysis.py
- **Format:** `.dat` files from ECStatJSONExaWatcher
- **Structure:** Header with sample interval, followed by JSON blocks delimited by `zzz <timestamp>` markers

```
# Sample Interval(s): 5
zzz <2025-08-05T10:00:00>
{"celldisk stats": [...]}
zzz <2025-08-05T10:00:05>
{"celldisk stats": [...]}
```

### CPU_Charts_From_ATP_Files.html
- **Format:** `.zip` files containing `CPUManager_*.json`
- **Structure:** JSON with `statNames` array and `values` array

### RDS_Info_Analysis.html
- **Format:** `.dat`, `.txt`, or `.xz` compressed files
- **Structure:** Lines with `counter_name value` pairs

```
send_lock_contention 12345
cong_update_queued 67890
```

### Exa_Cell_Metrics_Chart.html
- **Format:** `.lst` or `.txt` tab-separated files
- **Structure:** `index  metric  target  value  timestamp`

### alertlog_analyzer.html
- **Format:** Oracle alert log files (`alert_*.log`)
- **Structure:** Standard Oracle alert log format with timestamps

### vmstat_multi_plot_with_swap_alerts_highlight.html
- **Format:** vmstat output files
- **Structure:** vmstat output with optional `# Starting Time:` header

### Netstat_Analyzer.py
- **Format:** ExaWatcher netstat output, plain text or `.xz`/`.bz2` compressed
- **Structure:** Repeated `zzz <timestamp>` sample blocks containing interface rows and cumulative `netstat -s -e` counters

```text
zzz <09/10/2026 10:00:00> subcount:
Iface MTU RX-OK RX-ERR RX-DRP RX-OVR TX-OK TX-ERR TX-DRP TX-OVR Flg
bondeth0 1500 100 0 0 0 200 0 0 0 BMRU
InOctets: 1000000
OutOctets: 2000000
10 segments retransmitted
100 segments sent
TCPTimeouts: 0
```

### FlashcacheContent Analysis Tool
- **Raw format:** Timestamped `flashcache_*.csv` output from `scripts/extract_flashcache.sh`
- **Rollup format:** CSV export of `SELECT * FROM fc_by_object ORDER BY cached_mb DESC`
- **Collection prerequisite:** Run the extractor as `root` from an Exadata database node with `dcli` and root SSH equivalence to the storage cells.

### ExaWatcher Analyzer Workbench
- **Format:** ExaWatcher `.dat` output, plain, `.xz`, or `.bz2` compressed
- **Structure:** Collector-specific records inside timestamped `zzz <...>` sample blocks, with capture metadata in the file header
- **Views:** One analyzer for each remaining collector directory; `Charts.ExaWatcher...` is generated chart output and is not treated as a collector

---

## Project Structure

```
/
├── README.md                 # This documentation
├── requirements.txt          # Python dependencies
├── index.html                # Main dashboard
│
├── python/                   # Python scripts
│   ├── ECS_Analysis.py       # Streamlit ECstat analyzer
│   ├── Iostat_Analyzer.py    # Streamlit ExaWatcher iostat analyzer
│   ├── Netstat_Analyzer.py   # Streamlit ExaWatcher netstat analyzer
│   ├── netstat_analysis.py   # Standard-library netstat parser and analysis helpers
│   ├── ExaWatcher_Workbench.py # Streamlit workbench for remaining ExaWatcher outputs
│   └── exawatcher_analysis.py # Collector parsers and normalized time-series rows
│
├── html/                     # HTML-based tools
│   ├── alertlog_analyzer.html
│   ├── CPU_Charts_From_ATP_Files.html
│   ├── Exa_Cell_Metrics_Chart.html
│   ├── flashcache_dashboard.html
│   ├── RDS_Info_Analysis.html
│   └── vmstat_multi_plot_with_swap_alerts_highlight.html
│
├── scripts/                  # Data collection scripts
│   └── extract_flashcache.sh # Collects flashcachecontent from storage cells
│
├── sql/                      # Optional database setup and rollup views
│   └── flashcache_views.sql
│
├── assets/                   # Shared resources
│   └── common.js             # Common utilities
│
└── samples/                  # Example input files
    └── README.md             # Sample file descriptions
```

---

## Key RDS Congestion Counters

When troubleshooting RDS congestion, focus on these counters:

| Counter | Description |
|---------|-------------|
| `send_lock_contention` | Lock contention on send operations |
| `send_lock_queue_raced` | Queue race conditions |
| `cong_update_queued` | Congestion updates queued |
| `cong_update_received` | Congestion updates received |
| `cong_send_error` | Send errors due to congestion |
| `ib_tx_ring_full` | InfiniBand transmit ring full |
| `ib_tx_stalled` | InfiniBand transmit stalled |

---

## Browser Compatibility

All HTML tools are tested with:
- Google Chrome 90+
- Mozilla Firefox 88+
- Microsoft Edge 90+
- Safari 14+

**Note:** Some features (like `webkitdirectory` for folder selection) may have limited support in non-Chromium browsers.

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with sample data
5. Submit a pull request

---

## License

Internal Oracle XTeam tool. Contact Paulo Portugal for usage permissions.
