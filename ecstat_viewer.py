# Paulo Portugal - Oracle XTeam
# Optimized ECStat Disk Charts Parser
# 14-Nov-2025

import json, re, io
from datetime import datetime
from collections import defaultdict
import pandas as pd
import streamlit as st
import plotly.express as px

# -------------------------------------
# Streamlit config
# -------------------------------------
st.set_page_config(page_title="ECStat Disk Charts (Optimized)", layout="wide")
st.title("ECStat Disk Charts (Optimized) - Paulo Portugal - XTeam")
st.caption("Handles large ECStatJSONExaWatcher .dat files with chunked parsing, caching, and live progress.")

# -------------------------------------
# Regexes
# -------------------------------------
HEADER_INTERVAL_RE = re.compile(r"#\s*Sample\s+Interval\(s\):\s*(\d+)")
MARKER_RE = re.compile(r"^zzz\s*<")
MON = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12,
       "FEV":2,"ABR":4,"MAI":5,"AGO":8,"SET":9,"OUT":10,"DEZ":12}

# -------------------------------------
# Helpers
# -------------------------------------
def parse_sample_interval(file_obj, default_sec: int = 5):
    """Read first 200 lines to detect interval."""
    file_obj.seek(0)
    for i, line in enumerate(file_obj):
        if i > 200: break
        m = HEADER_INTERVAL_RE.search(line)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        if MARKER_RE.match(line):
            break
    file_obj.seek(0)
    return default_sec

def yield_json_blocks(file_obj):
    """Yield JSON blocks between 'zzz <' markers, streaming-friendly."""
    buf, depth_brace, depth_brack = [], 0, 0
    for line in file_obj:
        if MARKER_RE.match(line):
            if buf and depth_brace == 0 and depth_brack == 0:
                raw = "\n".join(buf).strip()
                buf.clear()
                start = min((raw.find("{") if "{" in raw else 1e9),
                            (raw.find("[") if "[" in raw else 1e9))
                if start < 1e9:
                    try:
                        yield json.loads(raw[start:])
                    except Exception:
                        # Try to fix truncated JSON
                        last = max(raw.rfind("}"), raw.rfind("]"))
                        if last > 0:
                            try:
                                yield json.loads(raw[start:last+1])
                            except: pass
            depth_brace = depth_brack = 0
            continue
        if "{" in line or "[" in line:
            depth_brace += line.count("{") - line.count("}")
            depth_brack += line.count("[") - line.count("]")
            buf.append(line)
        elif buf:
            buf.append(line)
    # flush last
    if buf:
        raw = "\n".join(buf).strip()
        start = min((raw.find("{") if "{" in raw else 1e9),
                    (raw.find("[") if "[" in raw else 1e9))
        if start < 1e9:
            try:
                yield json.loads(raw[start:])
            except Exception:
                pass

def normalize_devices(block):
    if isinstance(block, dict):
        if "celldisk stats" in block and isinstance(block["celldisk stats"], list):
            return block["celldisk stats"]
        return [block]
    elif isinstance(block, list):
        return block
    return []

def rate_from_cumulative(series, interval_sec):
    delta = series.diff().clip(lower=0).fillna(0)
    return delta / max(1, interval_sec)

# -------------------------------------
# Cached parsing
# -------------------------------------
@st.cache(allow_output_mutation=True, show_spinner=False,
           hash_funcs={type(re.compile('')): lambda _: None, '_json.Scanner': lambda _: None})
def parse_ecstat(file_bytes):
    """Parse uploaded ECStat file into DataFrame (cached)."""
    f = io.StringIO(file_bytes.decode("utf-8", errors="ignore"))
    interval_sec = parse_sample_interval(f)
    blocks = []
    total_lines = sum(1 for _ in f)
    f.seek(0)
    progress = st.progress(0)
    line_count = 0

    rows = []
    for blk in yield_json_blocks(f):
        devices = normalize_devices(blk)
        for dev in devices:
            ts_str = dev.get("timestampFormatted") or None
            if not ts_str and "timestamp" in dev:
                ts_ms = dev["timestamp"]
                ts_str = datetime.utcfromtimestamp(int(ts_ms)/1000).strftime("%Y-%m-%d %H:%M:%S")
            if not ts_str:
                continue
            try:
                ts = pd.to_datetime(ts_str)
            except:
                ts = ts_str

            dtype = dev.get("intendedDeviceType", "").upper()
            dtype_label = "MD" if dtype == "FD" else ("SD" if dtype == "HD" else dtype or "UNK")

            stats = dev.get("stats", {})
            for m, o in stats.items():
                rows.append([ts, dtype_label, "stats", m, int(o.get("iops",0)), int(o.get("bytes",0))])

            io_r = dev.get("IOReasons", {})
            for cat in ["readsIOReasons","writesIOReasons"]:
                for m, o in io_r.get(cat, {}).items():
                    rows.append([ts, dtype_label, cat, m, int(o.get("iops",0)), int(o.get("bytes",0))])
        line_count += 1
        if line_count % 2000 == 0:
            progress.progress(min(1.0, line_count/total_lines))

    progress.progress(1.0)
    df = pd.DataFrame(rows, columns=["timestamp","dtype","category","metric","iops_raw","bytes_raw"])
    df = df.sort_values("timestamp")
    return df, interval_sec

# -------------------------------------
# UI
# -------------------------------------
uploaded = st.file_uploader("Upload .dat (ECStatJSONExaWatcher)", type=["dat","json","txt","log"])
if not uploaded:
    st.info("Carregue seu arquivo .dat para começar.")
    st.stop()

with st.spinner("Parsing ECStat file (may take a few minutes for large files)..."):
    df, interval_sec = parse_ecstat(uploaded.getvalue())

if df.empty:
    st.error("Nenhuma métrica encontrada em stats / IOReasons.")
    st.stop()

with st.expander("Header Info", expanded=False):
    st.write(f"Sample Interval (s): **{interval_sec}**")
    st.write(f"Total Rows: {len(df):,}")

# -----------------------------
# Metric selectors
# -----------------------------
categories = ["stats","readsIOReasons","writesIOReasons","ALL"]
col1, col2, col3 = st.columns([1,2,1])

with col1:
    chosen_category = st.selectbox("Categoria", categories, index=0)

if chosen_category == "ALL":
    metric_pool = (df["category"] + ":" + df["metric"]).unique()
else:
    metric_pool = (df.loc[df["category"] == chosen_category, "category"] + ":" + df.loc[df["category"] == chosen_category, "metric"]).unique()

metric_pool = sorted(metric_pool)

with col2:
    chosen_metrics = st.multiselect("Métricas", metric_pool, default=metric_pool[:1] if metric_pool else [])

with col3:
    unit = st.selectbox("Unidade", ["IOPS/s","MB/s"], index=0)

use_delta = st.checkbox("Calcular taxa (delta/intervalo)", value=True)

if not chosen_metrics:
    st.warning("Selecione ao menos uma métrica.")
    st.stop()

# -----------------------------
# Build plot data
# -----------------------------
interval = interval_sec
plot_rows = []
for msel in chosen_metrics:
    cat, met = msel.split(":",1) if ":" in msel else ("stats", msel)
    subset = df[df["category"].eq(cat) & df["metric"].eq(met)]
    if subset.empty: continue
    value_col = "iops_raw" if unit=="IOPS/s" else "bytes_raw"

    grp = subset.groupby(["timestamp","dtype"],as_index=False)[value_col].sum()
    all_ts = sorted(grp["timestamp"].unique())
    for dtype in ["SD","MD"]:
        sub = grp[grp["dtype"]==dtype].set_index("timestamp").reindex(all_ts).fillna(0)
        y = sub[value_col]
        if use_delta:
            y = rate_from_cumulative(y, interval)
            if unit=="MB/s": y = y/(1024*1024)
        else:
            if unit=="MB/s": y = y/(1024*1024)
        plot_rows.append(pd.DataFrame({"timestamp":all_ts,"dtype":dtype,"series":f"{cat}:{met}","value":y.values}))

plot_df = pd.concat(plot_rows, ignore_index=True)
fig = px.line(plot_df, x="timestamp", y="value", color="dtype",
              line_dash="series" if len(chosen_metrics)>1 else None,
              title=f"Métricas: {', '.join(chosen_metrics)} ({unit})",
              labels={"value":unit,"timestamp":"Timestamp","dtype":"Tipo (SD/MD)"})
fig.update_layout(template="plotly_dark", height=550, legend_title_text="Tipo / Série")
st.plotly_chart(fig, use_container_width=True)

with st.expander("Ver dados agregados", expanded=False):
    st.dataframe(plot_df, use_container_width=True)

