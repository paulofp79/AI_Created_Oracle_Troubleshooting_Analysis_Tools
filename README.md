# Oracle Exadata Troubleshooting Analysis Tools

Browser-based and Streamlit-based tools for Oracle Exadata troubleshooting, ExaWatcher exploration, Oracle alert-log review, AWR mining, and host-level OS analysis.

Author: Paulo Portugal - Oracle XTeam

## Overview

This repository currently contains:

- A static dashboard served on port `8079`
- Browser-only HTML tools for Exadata and Oracle troubleshooting
- Streamlit apps for ECStat and AWR repository analysis
- Extra standalone utilities that are not part of the main dashboard cards
- Helper scripts to start and stop the local services
- Reference PDFs and sample data under `KB/`

Most HTML tools run entirely in the browser. The Streamlit apps are the only parts that require Python and a running local service.

## Quick Start

### Prerequisites

- Python `3.12` recommended
- `lsof` installed
- A modern browser
- `python3` available on `PATH` for the built-in static web server

### Create the virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --index-url https://pypi.org/simple -r requirements.txt
```

### Start the main services

```bash
./startall.sh
```

This starts:

- Static dashboard and HTML tools on `http://localhost:8079/`
- `ECS_Analysis.py` on `http://localhost:8501/`
- `AWR_Repository_Explorer.py` on `http://localhost:8502/`

### Stop the main services

```bash
./stopall.sh
```

## What `startall.sh` and `stopall.sh` do

### `startall.sh`

`startall.sh` is the recommended local startup path. It:

- Verifies that `.venv/bin/python` exists
- Calls `./stopall.sh` first to clear old listeners
- Starts a static web server with:

```bash
python3 -m http.server 8079 --bind 0.0.0.0
```

- Starts Streamlit for `python/ECS_Analysis.py` on port `8501`
- Starts Streamlit for `python/AWR_Repository_Explorer.py` on port `8502`
- Writes logs to:
  - `exaweb.log`
  - `streamlit_ecs.log`
  - `streamlit_awr.log`

### `stopall.sh`

`stopall.sh` kills listeners on these ports:

- `8079`
- `8501`
- `8502`

It does this with `lsof -tiTCP:<port> -sTCP:LISTEN`, then `kill`.

## Ports and Logs

| Port | Service | Started By | Notes |
| --- | --- | --- | --- |
| `8079` | Static dashboard and all HTML tools | `startall.sh` | Serves `index.html`, `html/*`, and root HTML files |
| `8501` | `python/ECS_Analysis.py` | `startall.sh` | ECStat / cell-disk Streamlit UI |
| `8502` | `python/AWR_Repository_Explorer.py` | `startall.sh` | Oracle AWR repository Streamlit UI |

Generated logs:

- `exaweb.log`
- `streamlit_ecs.log`
- `streamlit_awr.log`
- `streamlit.log` may also exist from manual Streamlit runs

## Main Tool Catalog

### Dashboard

- `index.html`
  - Main landing page for the primary toolset
  - Intended entry point when the static server is running on `8079`

### Browser Tools Served From `html/`

- `html/CPU_Charts_From_ATP_Files.html`
  - Loads zipped `CPUManager_*.json` data
  - Supports drag-and-drop and batch upload
  - Supports long-range charting with adjustable X-axis interval
  - Keeps already loaded files in memory while changing the chart interval
  - Shows summary statistics and chart-based CPU trend analysis

- `html/RDS_Info_Analysis.html`
  - Analyzes RDS congestion counters from ExaWatcher and AHF-style data
  - Supports multi-file loading
  - Supports `.xz` compressed input
  - Includes trend views and summary statistics for counter growth

- `html/CellSqlStat_Analyzer.html`
  - Parses `cellsqlstat --detail --batch` output from Exadata storage cells
  - Supports filtering by `CDBID`, `DBID`, `SQLID`, `DBNAME`, ranking section, and time range
  - Merges repeated ranking sections into unified SQL records per snapshot
  - Includes dataset summary, current-scope summary, top SQL summary, and charting
  - Supports CSV export
  - Uses `assets/cellsqlstat_parser.js`

- `html/Exa_Cell_Metrics_Chart.html`
  - ExaCC Metrics Dashboard
  - Interactive charting for Exadata Cloud@Customer cell metrics
  - Separate hard-disk (`CD_`) and flash-disk (`FD_`) target-average charts
  - Guide-based tabs for:
    - Flash Cache Performance Alerts
    - Smart I/O Performance Alerts
    - Cell Disk I/O Performance Alerts
    - Summary
  - Summary tab consolidates recommended metrics derived from Exadata documentation in `KB/`

- `html/alertlog_analyzer.html`
  - Oracle Alert Log Analyzer
  - Directory-based upload using `webkitdirectory`
  - Large alert-log friendly browser parsing
  - Filters by instance pattern, exclude pattern, and time range
  - Valid ORA-code extraction without reporting invalid `ORA-0`
  - DBA summary cards and top issue lists
  - Background process issue detection and filtering
  - Event timeline chart and CSV export

- `html/vmstat_multi_plot_with_swap_alerts_highlight.html`
  - VMStat Continuous Plotter
  - Supports plain-text vmstat files and `.xz` compressed ExaWatcher-style vmstat files
  - Handles ExaWatcher headers such as `# Starting Time:` and `# Sample Interval(s):`
  - Parses CPU, run queue, blocked processes, swap, I/O, interrupts, context switches, and steal time
  - Adds OS findings, hot-interval scoring, per-file health summary, and richer charting
  - Useful for OS-level triage, not just swap detection

- `html/Metric_Explorer.html`
  - Generic Exadata TSV visualizer
  - Upload one or more tab-separated metric files
  - Explore headers, filter rows, build charts, and inspect summary cards
  - Standalone browser utility not currently exposed in the main dashboard cards

### Root-Level Browser Tool

- `listener_log_analyzer.html`
  - Listener log analyzer for connection trends
  - Aggregates by hour, minute, or total
  - Can group by service name
  - Uses `listener_worker.js` for background parsing
  - Supports charting and CSV download

## Streamlit Apps

- `python/ECS_Analysis.py`
  - Streamlit UI for `ECStatJSONExaWatcher` disk metrics
  - Supports `.dat`, `.json`, `.txt`, `.log`, and `.xz`
  - Supports SD versus MD analysis
  - Converts cumulative counters into rates
  - Offers interactive metric selection and charting
  - Enforces a `200 MB` upload limit inside the app
  - Started automatically by `startall.sh` on port `8501`

- `python/AWR_Repository_Explorer.py`
  - Streamlit UI for connecting to an Oracle database that contains imported AWR data
  - Discovers available `DBID` values from `DBA_HIST_DATABASE_INSTANCE`
  - Includes built-in analysis tabs such as:
    - `GC Event Preset`
    - `Daily Load & GC`
    - Top SQL and drill-down workflows
    - Segment analysis
    - Custom `DBA_HIST_%` query runner
  - Saves recent connection strings in `.awr_recent_connections.json`
  - Keeps latest tab results until rerun instead of clearing them when switching tabs
  - Started automatically by `startall.sh` on port `8502`

- `python/ExaWatcher_Streamlit.py`
  - Streamlit frontend for exploring raw ExaWatcher collector directories
  - Uses `python/exawatcher_framework.py`
  - Can browse collectors such as:
    - `vmstat`
    - `iostat`
    - `toppid`
    - `mpstat`
    - `top`
    - `ps`
    - `meminfo`
  - Not started by `startall.sh`

## Additional Python Utilities

- `ecstat_viewer.py`
  - Older optimized Streamlit ECStat viewer
  - Chunked parsing and caching for large ECStat files
  - Useful as an alternative ECStat workflow
  - Not started by `startall.sh`

- `python/exawatcher_framework.py`
  - Reusable parsing framework used by `python/ExaWatcher_Streamlit.py`
  - Library module, not a standalone UI

## Manual Run Commands

### Static dashboard only

```bash
python3 -m http.server 8079 --bind 0.0.0.0
```

Then open:

- `http://localhost:8079/`
- `http://localhost:8079/html/CPU_Charts_From_ATP_Files.html`
- `http://localhost:8079/html/RDS_Info_Analysis.html`
- `http://localhost:8079/html/CellSqlStat_Analyzer.html`
- `http://localhost:8079/html/Exa_Cell_Metrics_Chart.html`
- `http://localhost:8079/html/alertlog_analyzer.html`
- `http://localhost:8079/html/vmstat_multi_plot_with_swap_alerts_highlight.html`
- `http://localhost:8079/html/Metric_Explorer.html`
- `http://localhost:8079/listener_log_analyzer.html`

### Streamlit tools started by `startall.sh`

```bash
source .venv/bin/activate
python -m streamlit run python/ECS_Analysis.py --server.port 8501 --server.address 0.0.0.0
python -m streamlit run python/AWR_Repository_Explorer.py --server.port 8502 --server.address 0.0.0.0
```

### Additional Streamlit tools not started by `startall.sh`

Example ports:

```bash
source .venv/bin/activate
python -m streamlit run python/ExaWatcher_Streamlit.py --server.port 8503 --server.address 0.0.0.0
python -m streamlit run ecstat_viewer.py --server.port 8504 --server.address 0.0.0.0
```

## Common Input Sources

- CPU charts: zipped `CPUManager_*.json` exports
- RDS analysis: ExaWatcher or AHF RDS collector output, including `.xz`
- Cell SQL analyzer: `cellsqlstat --detail --batch` output from storage cells
- ExaCC dashboard: metric extracts for ExaCC / cell metrics
- Alert-log analyzer: Oracle alert logs and `alert_*.log` directories
- VMStat plotter: vmstat outputs from ExaWatcher or raw vmstat captures, including `.xz`
- ECS analysis: `ECStatJSONExaWatcher` `.dat` or similar JSON collector output
- AWR explorer: Oracle database with populated `DBA_HIST_%` views
- ExaWatcher dataset explorer: raw collector directories under an ExaWatcher capture root
- Listener analyzer: `listener.log` files

## Supporting Files and Data

- `assets/common.js`
  - Shared browser helper code

- `assets/cellsqlstat_parser.js`
  - Parser logic used by the Cell SQL Stat Analyzer

- `listener_worker.js`
  - Web Worker for listener log parsing

- `samples/`
  - Small sample inputs for several tools

- `KB/`
  - Reference material and working data
  - Currently includes:
    - `system-overview-exadata-database-machine-dbmso.pdf`
    - `system-software-users-guide-exadata-database-machine-sagug.pdf`
    - `2026_04_01_10_44_24_CellSqlStatExaWatcher_gru126171exdcl02.oraclecloud.internal.dat`

- `.awr_recent_connections.json`
  - Saved recent connection profiles for `python/AWR_Repository_Explorer.py`

## Optional Developer Dependencies

The repository also includes `package.json` with optional development-time dependencies:

- `jsdom`
- `playwright`

These are useful for DOM-level and browser validation, but they are not required to run the end-user tools.

## Python Dependencies

`requirements.txt` currently includes:

- `streamlit`
- `pandas`
- `plotly`
- `oracledb`

Install them with:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Repository Layout

| Path | Purpose |
| --- | --- |
| `index.html` | Main dashboard |
| `html/` | Primary browser tools |
| `python/` | Streamlit apps and shared Python modules |
| `assets/` | Shared JavaScript helpers |
| `KB/` | Reference PDFs and working sample data |
| `samples/` | Small example files |
| `startall.sh` | Start dashboard plus main Streamlit apps |
| `stopall.sh` | Stop listeners on `8079`, `8501`, and `8502` |

## Notes

- The recommended local port for the dashboard is `8079`.
- `startall.sh` only launches the dashboard, `ECS_Analysis.py`, and `AWR_Repository_Explorer.py`.
- `Metric_Explorer.html`, `listener_log_analyzer.html`, `python/ExaWatcher_Streamlit.py`, and `ecstat_viewer.py` are present in the repository but require direct access or manual startup.
- If you run locally, use the `localhost` URLs documented above.
