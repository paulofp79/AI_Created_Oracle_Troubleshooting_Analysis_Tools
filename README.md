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
- **AWR repository metrics** from `DBA_HIST_%` views with explicit DBID selection
- **VMStat memory/swap analysis** with anomaly detection
- **Oracle alert log parsing** with timeline visualization
- **ExaCC metrics dashboards** with performance alerts

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

### 2. CPU_Charts_From_ATP_Files.html
**Purpose:** Visualize CPU utilization from zipped CPUManager JSON exports

**Features:**
- Drag-and-drop file upload
- Multi-file batch processing
- CSV export capability
- Interactive chart with series toggles

### 3. RDS_Info_Analysis.html
**Purpose:** Analyze RDS (Relational Database Service) congestion counters

**Features:**
- Multi-file upload (supports `.xz` compression)
- Counter trend visualization
- Statistics calculation (total increase, per-interval increase)
- Key congestion counter reference

### 4. Exa_Cell_Metrics_Chart.html
**Purpose:** ExaCC (Exadata Cloud@Customer) metrics dashboard

**Features:**
- Line and bar chart visualizations
- Alert tabs for Flash Cache, Smart I/O, and Cell Disk I/O
- Color-coded severity levels (Normal/Warning/Critical)
- Click-to-navigate from alerts to charts

### 5. alertlog_analyzer.html
**Purpose:** Parse and analyze Oracle alert log files

**Features:**
- Directory-based file selection
- Instance name and exclude pattern filters
- Date/time range filtering
- Keyword search with context lines
- Syntax highlighting for ORA- errors, FATAL, FAIL
- Timeline visualization with Chart.js

### 6. vmstat_multi_plot_with_swap_alerts_highlight.html
**Purpose:** Analyze vmstat data for memory pressure issues

**Features:**
- Multi-file continuous timeline stitching
- Separate plots for Memory+CPU, Swap In, Swap Out
- Automatic swap utilization alerts
- CSV export

### 7. AWR_Repository_Explorer.py (Streamlit App)
**Purpose:** Connect to an Oracle AWR dump repository and chart `DBA_HIST_%` metrics by DBID

**Features:**
- Oracle login from the Streamlit UI
- DBID discovery from `DBA_HIST_DATABASE_INSTANCE`
- Built-in chart for events such as `gc current block congested`
- Custom read-only `DBA_HIST_%` SQL with enforced `:dbid` bind
- Interactive chart builder for query results

---

## Installation

### Prerequisites

- Modern web browser (Chrome, Firefox, Edge, Safari)
- Python 3.8+ (for Streamlit-based tools)

### Recommended Python Environment

This repository now uses a local Python 3.12 virtual environment at `.venv`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --index-url https://pypi.org/simple -r requirements.txt
```

Use the virtualenv Python for all project commands:

```bash
source .venv/bin/activate
python --version
```

### Start/Stop Helpers

To stop all app processes:

```bash
./stopall.sh
```

This stops anything listening on ports `8080`, `8501`, and `8502`.

To start the dashboard and both Streamlit apps:

```bash
./startall.sh
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install streamlit pandas plotly oracledb
```

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
source .venv/bin/activate
python -m streamlit run python/ECS_Analysis.py --server.port 8501

# Or simply
streamlit run python/ECS_Analysis.py
```

Then open http://localhost:8501 in your browser.

### Python Tool (AWR_Repository_Explorer.py)

```bash
source .venv/bin/activate
python -m streamlit run python/AWR_Repository_Explorer.py --server.port 8502
```

Then open http://localhost:8502 in your browser.

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
│   └── AWR_Repository_Explorer.py  # Streamlit AWR repository chart explorer
│
├── html/                     # HTML-based tools
│   ├── alertlog_analyzer.html
│   ├── CPU_Charts_From_ATP_Files.html
│   ├── Exa_Cell_Metrics_Chart.html
│   ├── RDS_Info_Analysis.html
│   └── vmstat_multi_plot_with_swap_alerts_highlight.html
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
