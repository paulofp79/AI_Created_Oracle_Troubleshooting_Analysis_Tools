##Paulo Portugal - Oracle XTeam
##05-Aug-2025
# ecstat_viewer.py
import re
import json
from datetime import datetime
from collections import defaultdict
 
import pandas as pd
import streamlit as st
import plotly.express as px
 
st.set_page_config(page_title="ECStat Disk Charts (SD vs MD)", layout="wide")
 
st.title("ECStat Disk Charts (SD vs MD) - Paulo Portugal - XTeam")
st.caption("Parser robusto de ECStatJSONExaWatcher .dat → seleção de métricas → gráfico agregado por SD (HD) e MD (FD)")
 
# ----------------------------
# Helpers
# ----------------------------
 
HEADER_INTERVAL_RE = re.compile(r"#\s*Sample\s+Interval\(s\):\s*(\d+)")
MARKER_RE = re.compile(r"^zzz\s*<")
 
def parse_sample_interval(text: str, default_sec: int = 5) -> int:
    for line in text.splitlines():
        m = HEADER_INTERVAL_RE.search(line)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        if MARKER_RE.match(line):
            break
    return default_sec
 
def normalize_devices_from_block(block):
    """
    Recebe um JSON já carregado (dict/list) e devolve uma lista de 'devices'.
    - Pode vir como {"celldisk stats": [ ... ]}
    - Pode vir como [ ... ]
    - Pode vir como { ...device... }
    """
    if isinstance(block, dict):
        if "celldisk stats" in block and isinstance(block["celldisk stats"], list):
            return block["celldisk stats"]
        # Caso seja um único device
        # (pouco provável, mas suportamos)
        return [block]
    elif isinstance(block, list):
        return block
    else:
        return []
 
def json_blocks_from_dat(text: str):
    """
    Extrai blocos JSON entre linhas que começam com 'zzz <...>'.
    Usa contagem de chaves/colchetes para fechar o bloco corretamente.
    Ignora o cabeçalho antes do primeiro 'zzz <'.
    """
    lines = text.splitlines()
    started = False
    brace_depth = 0
    bracket_depth = 0
    collecting = False
    buf = []
 
    def flush_buf():
        nonlocal buf
        raw = "\n".join(buf).strip()
        buf = []
        if not raw:
            return None
        # O bloco pode ter lixo antes de '{' ou '[' (defensivo)
        start_idx_obj = raw.find("{")
        start_idx_arr = raw.find("[")
        starts = [i for i in [start_idx_obj, start_idx_arr] if i != -1]
        if not starts:
            return None
        start = min(starts)
        raw = raw[start:]
        try:
            return json.loads(raw)
        except Exception:
            # Tenta remover trailing após o último '}' ou ']'
            last_brace = raw.rfind("}")
            last_bracket = raw.rfind("]")
            last_pos = max(last_brace, last_bracket)
            if last_pos != -1:
                try:
                    return json.loads(raw[:last_pos+1])
                except Exception:
                    return None
            return None
 
    blocks = []
 
    for line in lines:
        if not started:
            if MARKER_RE.match(line):
                started = True
            else:
                continue
            # Não coletamos a linha do marker, o JSON vem depois
            continue
 
        if MARKER_RE.match(line):
            # Novo sample → fechar o anterior se estivermos coletando
            if collecting and brace_depth == 0 and bracket_depth == 0:
                blk = flush_buf()
                if blk is not None:
                    blocks.append(blk)
            collecting = False
            brace_depth = 0
            bracket_depth = 0
            continue
 
        # detectar início do JSON
        if not collecting:
            if "{" in line or "[" in line:
                collecting = True
                # começa a contar profundidade
                brace_depth += line.count("{")
                brace_depth -= line.count("}")
                bracket_depth += line.count("[")
                bracket_depth -= line.count("]")
                buf.append(line)
        else:
            brace_depth += line.count("{")
            brace_depth -= line.count("}")
            bracket_depth += line.count("[")
            bracket_depth -= line.count("]")
            buf.append(line)
            if brace_depth == 0 and bracket_depth == 0:
                # bloco completo
                blk = flush_buf()
                if blk is not None:
                    blocks.append(blk)
                collecting = False
 
    # EOF: flush se sobrou algo completo
    if collecting and brace_depth == 0 and bracket_depth == 0:
        blk = flush_buf()
        if blk is not None:
            blocks.append(blk)
 
    return blocks
 
def to_long_rows(blocks):
    """
    Converte blocos JSON em linhas normalizadas:
    cols: timestamp(dt), ts_str, dtype_label(SD/MD), category(stats/readsIOReasons/writesIOReasons),
          metric, iops, bytes
    """
    rows = []
    for block in blocks:
        devices = normalize_devices_from_block(block)
        for dev in devices:
            ts_str = dev.get("timestampFormatted")
            if not ts_str:
                # tenta por 'timestamp' (ms)
                ts_ms = dev.get("timestamp")
                if ts_ms:
                    ts_str = datetime.utcfromtimestamp(int(ts_ms)/1000).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    continue
            try:
                ts = pd.to_datetime(ts_str)
            except Exception:
                # último recurso, pega string
                ts = ts_str
 
            dtype = dev.get("intendedDeviceType", "").upper()
            # Mapear para SD/MD como você usa: FD=MD (flash), HD=SD (hard)
            dtype_label = "MD" if dtype == "FD" else ("SD" if dtype == "HD" else dtype or "UNK")
 
            # stats
            stats = dev.get("stats", {})
            for mname, obj in stats.items():
                iops = obj.get("iops", 0) or 0
                byts = obj.get("bytes", 0) or 0
                rows.append([ts, ts_str, dtype_label, "stats", mname, int(iops), int(byts)])
 
            # IOReasons
            io = dev.get("IOReasons", {})
            reads = io.get("readsIOReasons", {})
            for mname, obj in reads.items():
                iops = obj.get("iops", 0) or 0
                byts = obj.get("bytes", 0) or 0
                rows.append([ts, ts_str, dtype_label, "readsIOReasons", mname, int(iops), int(byts)])
 
            writes = io.get("writesIOReasons", {})
            for mname, obj in writes.items():
                iops = obj.get("iops", 0) or 0
                byts = obj.get("bytes", 0) or 0
                rows.append([ts, ts_str, dtype_label, "writesIOReasons", mname, int(iops), int(byts)])
 
    df = pd.DataFrame(rows, columns=["timestamp", "ts_str", "dtype_label", "category", "metric", "iops_raw", "bytes_raw"])
    if df.empty:
        return df
    # Ordena por tempo
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df
 
def rate_from_cumulative(df_series: pd.Series, interval_sec: int) -> pd.Series:
    """
    Converte contadores cumulativos em taxa por segundo (delta/intervalo).
    Evita negativos (reinícios/rollovers).
    """
    delta = df_series.diff()
    if delta.isnull().any():
        delta = delta.fillna(0)
    delta = delta.clip(lower=0)
    return delta / max(1, interval_sec)
 
# ----------------------------
# UI
# ----------------------------
 
uploaded = st.file_uploader("Faça upload do arquivo .dat (ECStatJSONExaWatcher)", type=["dat","json","txt","log"])
if not uploaded:
    st.info("Carregue seu arquivo .dat para começar.")
    st.stop()
 
raw_text = uploaded.read().decode("utf-8", errors="ignore")
interval_sec = parse_sample_interval(raw_text, default_sec=5)
 
with st.expander("Detalhes do header", expanded=False):
    st.write(f"Sample Interval (s): **{interval_sec}**")
 
# Parse
blocks = json_blocks_from_dat(raw_text)
if not blocks:
    st.error("Não encontrei blocos JSON após os marcadores 'zzz <...>'. Revise o arquivo.")
    st.stop()
 
df = to_long_rows(blocks)
if df.empty:
    st.error("Arquivo lido, mas não encontrei métricas em 'stats' / 'IOReasons'.")
    st.stop()
 
# Seletores
categories = ["stats", "readsIOReasons", "writesIOReasons", "ALL"]
col1, col2, col3 = st.columns([1, 2, 1])
 
with col1:
    chosen_category = st.selectbox("Categoria", categories, index=0)
 
if chosen_category == "ALL":
    metric_pool = (df["category"] + ":" + df["metric"]).unique()
else:
    metric_pool = (df.loc[df["category"] == chosen_category, "category"] + ":" + df.loc[df["category"] == chosen_category, "metric"]).unique()
 
metric_pool = sorted(metric_pool)
 
with col2:
    chosen_metrics = st.multiselect("Métricas (pode selecionar várias)", metric_pool,
                                    default=metric_pool[:1] if metric_pool else [])
 
with col3:
    unit = st.selectbox("Unidade", ["IOPS/s", "MB/s"], index=0)
 
use_delta = st.checkbox("Calcular como taxa (delta/intervalo)", value=True,
                        help="Converte contadores cumulativos em IOPS/s ou MB/s usando o Sample Interval do header.")
 
if not chosen_metrics:
    st.warning("Selecione ao menos uma métrica.")
    st.stop()
 
# Monta chave (category, metric)
key_pairs = []
for k in chosen_metrics:
    if ":" in k:
        c, m = k.split(":", 1)
        key_pairs.append((c, m))
    else:
        # fallback: assume 'stats'
        key_pairs.append(("stats", k))
 
plot_rows = []
for (cat, met) in key_pairs:
    sdf = df[df["category"].eq(cat) & df["metric"].eq(met)]
 
    # Agrega por timestamp + dtype_label somando todos os discos
    if unit == "IOPS/s":
        series_col = "iops_raw"
    else:
        series_col = "bytes_raw"
 
    grp = sdf.groupby(["timestamp", "dtype_label"], as_index=False)[series_col].sum()
 
    # Reindex para ter as duas séries (SD, MD) em todas as timestamps
    all_ts = sorted(grp["timestamp"].unique())
    for dtype in ["SD", "MD"]:
        sub = grp[grp["dtype_label"] == dtype].set_index("timestamp").reindex(all_ts).fillna(0)
 
        if use_delta:
            rate = rate_from_cumulative(sub[series_col], interval_sec)
            if unit == "MB/s":
                rate = rate / (1024 * 1024)  # bytes -> MB
            y = rate.values
        else:
            # valor direto (se escolher MB/s sem delta, converte bytes para MB)
            if unit == "MB/s":
                y = (sub[series_col] / (1024 * 1024)).values
            else:
                y = sub[series_col].values
 
        plot_rows.append(pd.DataFrame({
            "timestamp": all_ts,
            "dtype": dtype,
            "series": f"{cat}:{met}",
            "value": y
        }))
 
plot_df = pd.concat(plot_rows, ignore_index=True)
 
# Gráfico
fig = px.line(
    plot_df,
    x="timestamp",
    y="value",
    color="dtype",
    line_dash="series" if len(key_pairs) > 1 else None,
    title=f"Métricas: {', '.join([f'{c}:{m}' for c,m in key_pairs])} ({unit})",
    labels={"value": unit, "timestamp": "Timestamp", "dtype": "Tipo (SD/MD)"},
)
 
fig.update_layout(template="plotly_dark", height=550, legend_title_text="Tipo / Série")
 
st.plotly_chart(fig, use_container_width=True)
 
# Tabela opcional (debug/inspeção)
with st.expander("Ver dados agregados", expanded=False):
    st.dataframe(plot_df, use_container_width=True)