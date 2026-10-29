"""Shared Streamlit renderer for Apache ECharts charts."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

import streamlit.components.v1 as components


ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__} for ECharts")


def _clean_non_finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_non_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_non_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return _clean_non_finite(value.item())
    return value


def render_echarts(option: dict[str, Any], height: int = 540) -> None:
    """Render an ECharts option in a responsive Streamlit iframe."""
    option.setdefault("textStyle", {"color": "#cbd5e1", "fontFamily": "Inter, ui-sans-serif, system-ui, sans-serif"})
    option_json = json.dumps(
        _clean_non_finite(option),
        default=_json_default,
        ensure_ascii=False,
        allow_nan=False,
    ).replace("<", "\\u003c")
    components.html(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body, #chart {{ width: 100%; height: 100%; margin: 0; overflow: hidden; }}
    body {{ background: transparent; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
  </style>
  <script src="{ECHARTS_CDN}"></script>
</head>
<body>
  <div id="chart" role="img" aria-label="Interactive analysis chart"></div>
  <script>
    const chart = echarts.init(document.getElementById('chart'), null, {{ renderer: 'canvas' }});
    chart.setOption({option_json}, {{ notMerge: true, lazyUpdate: true }});
    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    new ResizeObserver(resize).observe(document.getElementById('chart'));
  </script>
</body>
</html>""",
        height=height,
        scrolling=False,
    )
