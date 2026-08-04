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

### 7. FlashcacheContent Analysis Tool
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
pip install streamlit pandas plotly
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
python3 -m streamlit run python/ECS_Analysis.py --server.port 8501

# Or simply
streamlit run python/ECS_Analysis.py
```

Then open http://localhost:8501 in your browser.

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

### FlashcacheContent Analysis Tool
- **Raw format:** Timestamped `flashcache_*.csv` output from `scripts/extract_flashcache.sh`
- **Rollup format:** CSV export of `SELECT * FROM fc_by_object ORDER BY cached_mb DESC`
- **Collection prerequisite:** Run the extractor as `root` from an Exadata database node with `dcli` and root SSH equivalence to the storage cells.

---

## Project Structure

```
/
├── README.md                 # This documentation
├── requirements.txt          # Python dependencies
├── index.html                # Main dashboard
│
├── python/                   # Python scripts
│   └── ECS_Analysis.py       # Streamlit ECstat analyzer
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
