/* RDS ping observations are latency measurements, never block-transfer counts. */
(function (root) {
  'use strict';
  const COLORS = { low: '#48d6c4', mid: '#f2bd68', high: '#ff7b87', blue: '#79b9ff', muted: '#9aacc0' };
  const number = value => Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : '—';
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const compareIp = (a, b) => a.localeCompare(b, undefined, { numeric: true });
  const routeKey = row => `${row.sourceIp} → ${row.targetIp}`;

  // Parse a wall clock explicitly, retaining its displayed fields in any browser timezone.
  function clockTime(text) {
    const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:\s*(?:Z|[+-]\d{2}:?\d{2}))?$/.exec(text || '');
    if (!match) return NaN;
    const [, y, m, d, h, min, sec] = match.map(Number);
    const time = Date.UTC(y, m - 1, d, h, min, sec);
    const date = new Date(time);
    return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d && h < 24 && min < 60 && sec < 60 ? time : NaN;
  }

  function statistics(rows) {
    if (!rows.length) return { count: 0, mean: null, p95: null, max: null };
    const values = rows.map(row => row.usec).sort((a, b) => a - b);
    return { count: values.length, mean: values.reduce((a, b) => a + b, 0) / values.length,
      p95: values[Math.ceil(values.length * .95) - 1], max: values[values.length - 1] };
  }

  function buildFrames(probes, collections) {
    const frames = new Map();
    const seen = new Set();
    let duplicates = 0;
    let skipped = 0;
    function frame(timestamp) {
      const time = clockTime(timestamp);
      if (!Number.isFinite(time)) return null;
      if (!frames.has(time)) frames.set(time, { time, timestamp: new Date(time).toISOString().slice(0, 19).replace('T', ' '), rows: [] });
      return frames.get(time);
    }
    collections.forEach(sample => frame(sample.timestamp));
    probes.forEach(row => {
      if (!Number.isFinite(clockTime(row.timestamp)) || !Number.isFinite(row.usec) || row.usec < 0 || !row.sourceIp || !row.targetIp || row.sourceIp === 'unknown' || row.targetIp === 'unknown') { skipped++; return; }
      const target = frame(row.frameTimestamp || row.timestamp);
      if (!target) { skipped++; return; }
      const key = JSON.stringify([row.timestamp, row.sourceIp, row.targetIp, row.queue, row.usec]);
      if (seen.has(key)) { duplicates++; return; }
      seen.add(key);
      target.rows.push(row);
    });
    return { frames: [...frames.values()].sort((a, b) => a.time - b.time), duplicates, skipped };
  }

  function aggregateRoutes(rows) {
    const routes = new Map();
    rows.forEach(row => {
      const key = routeKey(row);
      if (!routes.has(key)) routes.set(key, { key, sourceIp: row.sourceIp, targetIp: row.targetIp, rows: [] });
      routes.get(key).rows.push(row);
    });
    return [...routes.values()].map(route => ({ ...route, ...statistics(route.rows) }));
  }

  class RdsNetworkView {
    constructor() {
      this.frames = [];
      this.filteredFrames = [];
      this.index = 0;
      this.timer = null;
      this.focus = null;
      this.visible = false;
      this.charts = {};
      this.reducedMotion = root.matchMedia('(prefers-reduced-motion: reduce)');
      this.el = id => document.getElementById(id);
      this.el('flowFrame').addEventListener('input', event => { this.pause(); this.seek(Number(event.target.value)); });
      this.el('flowPrev').addEventListener('click', () => { this.pause(); this.seek(this.index - 1); });
      this.el('flowNext').addEventListener('click', () => { this.pause(); this.seek(this.index + 1); });
      this.el('flowPlay').addEventListener('click', () => this.timer ? this.pause() : this.play());
      this.el('flowSpeed').addEventListener('change', () => { if (this.timer) { this.pause(); this.play(); } });
      ['flowQueue', 'flowMetric', 'flowLimit', 'flowThreshold'].forEach(id => {
        this.el(id).addEventListener('change', () => { this.pause(); this.refresh(); });
      });
      this.el('flowReset').addEventListener('click', () => { this.focus = null; this.refresh(); });
      this.el('flowExport').addEventListener('click', () => this.download());
      this.el('flowRoutes').addEventListener('click', event => {
        const button = event.target.closest('[data-route]');
        if (button) this.select({ type: 'route', key: button.dataset.route });
      });
      this.el('tab-4').addEventListener('keydown', event => {
        if (/^(INPUT|SELECT|BUTTON|SUMMARY)$/.test(event.target.tagName)) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault(); this.pause(); this.seek(this.index + (event.key === 'ArrowRight' ? 1 : -1));
        } else if (event.code === 'Space') { event.preventDefault(); this.timer ? this.pause() : this.play(); }
      });
      document.addEventListener('visibilitychange', () => { if (document.hidden) this.pause(); });
      root.addEventListener('pagehide', () => this.pause());
      this.observer = new ResizeObserver(() => {
        if (this.visible) {
          Object.values(this.charts).forEach(chart => chart.resize());
          if ((this.charts.network && (this.charts.network.getWidth() < 500) !== this.compactNetwork) ||
              (this.charts.matrix && this.matrixColumns() !== this.matrixColumnCount)) this.render();
        }
      });
      this.observer.observe(this.el('tab-4'));
    }

    load(probes, collections) {
      this.pause();
      const data = buildFrames(probes, collections);
      this.frames = data.frames;
      this.duplicates = data.duplicates;
      this.skipped = data.skipped;
      this.index = 0;
      this.focus = null;
      const queues = [...new Set(this.frames.flatMap(frame => frame.rows.map(row => row.queue)))].sort(compareIp);
      this.el('flowQueue').innerHTML = '<option value="">All queues</option>' + queues.map(queue => `<option value="${escape(queue)}">${escape(queue)}</option>`).join('');
      this.hasData = this.frames.some(frame => frame.rows.length);
      this.el('flowEmpty').hidden = this.hasData;
      this.el('flowDashboard').hidden = !this.hasData;
      this.el('flowEmpty').innerHTML = probes.length && !this.hasData
        ? 'No network-ready probes were found. Each result needs a valid date/time, source IP, target IP, and numeric latency. Existing ping and counter views remain available.'
        : 'Load RDS files containing timestamped <code>rds-ping -I source target</code> results to explore the network. Counter-only files remain available in Counter Analytics.';
      this.el('flowFrame').max = Math.max(0, this.frames.length - 1);
      Object.values(this.charts).forEach(chart => chart.clear());
      this.refresh();
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) this.pause();
      else requestAnimationFrame(() => {
        this.refresh();
        Object.values(this.charts).forEach(chart => chart.resize());
      });
    }

    threshold() { return Math.max(1, Number(this.el('flowThreshold').value) || 1000); }
    metric() { return this.el('flowMetric').value; }
    matrixColumns() { return Math.max(3, Math.min(18, Math.floor((this.el('flowMatrix').clientWidth - 145) / 50))); }
    color(value) { return value >= this.threshold() ? COLORS.high : value >= this.threshold() / 2 ? COLORS.mid : COLORS.low; }
    matches(row) {
      return (!this.el('flowQueue').value || row.queue === this.el('flowQueue').value) &&
        (!this.focus || (this.focus.type === 'route' ? routeKey(row) === this.focus.key : row.sourceIp === this.focus.key || row.targetIp === this.focus.key));
    }

    refresh() {
      this.filteredFrames = this.frames.map(frame => {
        const rows = frame.rows.filter(row => this.matches(row));
        return { ...frame, rows, routes: aggregateRoutes(rows), stats: statistics(rows) };
      });
      if (!this.visible || !this.hasData) return;
      if (typeof root.echarts === 'undefined') {
        this.el('flowDashboard').hidden = true;
        this.el('flowEmpty').hidden = false;
        this.el('flowEmpty').textContent = 'The chart library did not load. Check the network connection and refresh the page.';
        return;
      }
      this.renderTimeline();
      this.render();
    }

    chart(name, id) {
      if (!this.charts[name]) {
        this.charts[name] = root.echarts.init(this.el(id), null, { renderer: 'canvas' });
        this.charts[name].on('click', params => {
          if (name === 'timeline' && params.data && Number.isInteger(params.data.frameIndex)) {
            this.pause(); this.seek(params.data.frameIndex);
          } else if (params.data && params.data.route) this.select({ type: 'route', key: params.data.route.key });
          else if (params.data && params.data.ip) this.select({ type: 'node', key: params.data.ip });
        });
      }
      return this.charts[name];
    }

    base(name) {
      return { backgroundColor: 'transparent', textStyle: { color: '#c8d9e6', fontFamily: 'Roboto, Arial, sans-serif' },
        animation: !this.reducedMotion.matches, animationDurationUpdate: 180,
        aria: { enabled: true },
        tooltip: { confine: true, backgroundColor: '#102333', borderColor: '#3a536a', textStyle: { color: '#edf3f9' } },
        toolbox: { right: 0, top: 0, iconStyle: { borderColor: '#9aacc0' }, feature: {
          saveAsImage: { title: 'Save image', name: `RDS-${name}`, pixelRatio: 2, backgroundColor: '#101d2b' }
        } } };
    }

    select(focus) {
      this.pause();
      this.focus = this.focus && this.focus.type === focus.type && this.focus.key === focus.key ? null : focus;
      this.refresh();
    }

    seek(index) {
      this.index = Math.max(0, Math.min(this.frames.length - 1, index));
      if (this.visible && this.hasData) { this.render(); this.markTime(); }
    }

    play() {
      if (!this.visible || !this.hasData || this.frames.length < 2) return;
      if (this.index === this.frames.length - 1) this.seek(0);
      this.el('flowPlay').textContent = 'Ⅱ Pause';
      this.el('flowPlay').setAttribute('aria-pressed', 'true');
      this.timer = root.setInterval(() => {
        if (this.index >= this.frames.length - 1) this.pause();
        else this.seek(this.index + 1);
      }, Number(this.el('flowSpeed').value));
    }

    pause() {
      root.clearInterval(this.timer);
      this.timer = null;
      this.el('flowPlay').textContent = '▶ Play';
      this.el('flowPlay').setAttribute('aria-pressed', 'false');
    }

    routeTooltip(route) {
      const queues = [...new Set(route.rows.map(row => row.queue))].sort(compareIp).join(', ');
      return `<strong>${escape(route.key)}</strong><br>Mean: ${number(route.mean)} µs<br>P95: ${number(route.p95)} µs<br>Maximum: ${number(route.max)} µs<br>${route.count} successful probes · queue(s) ${escape(queues)}`;
    }

    render() {
      const frame = this.filteredFrames[this.index];
      if (!frame) return;
      const routes = [...frame.routes].sort((a, b) => b[this.metric()] - a[this.metric()] || a.key.localeCompare(b.key));
      const limit = Number(this.el('flowLimit').value);
      const shown = limit ? routes.slice(0, limit) : routes;
      const slow = frame.rows.filter(row => row.usec >= this.threshold()).length;
      const endpoints = new Set(frame.rows.flatMap(row => [row.sourceIp, row.targetIp]));
      const metrics = [
        ['Mean latency', `${number(frame.stats.mean)} µs`, 'Weighted by probe count'],
        ['Maximum latency', `${number(frame.stats.max)} µs`, 'Highest observed probe'],
        ['Measured routes', number(routes.length), `${endpoints.size} IP endpoints`],
        ['Successful probes', number(frame.rows.length), 'Current sample and filters'],
        ['At / above highlight', frame.rows.length ? `${number(slow * 100 / frame.rows.length)}%` : '—', `Threshold ${number(this.threshold())} µs`]
      ];
      this.el('flowStats').innerHTML = metrics.map(([label, value, detail]) => `<div class="flow-stat"><span>${label}</span><strong>${value}</strong><small>${detail}</small></div>`).join('');
      this.el('flowFrame').value = this.index;
      this.el('flowTime').textContent = `${frame.timestamp} · ${this.index + 1} / ${this.frames.length}`;
      this.el('flowPrev').disabled = this.index === 0;
      this.el('flowNext').disabled = this.index >= this.frames.length - 1;
      this.el('flowPlay').disabled = this.frames.length < 2;
      this.el('flowReset').disabled = !this.focus;
      this.el('flowExport').disabled = !this.filteredFrames.some(item => item.rows.length);
      this.el('flowFocus').textContent = this.focus ? `${this.focus.type === 'node' ? 'Endpoint' : 'Route'}: ${this.focus.key}` : 'All measured endpoints';
      this.el('flowRouteCount').textContent = `${shown.length} of ${routes.length} measured routes · ranked by ${this.metric() === 'p95' ? 'P95' : this.metric()} latency`;
      const missing = this.filteredFrames.filter(item => !item.rows.length).length;
      this.el('flowCoverage').textContent = `${this.frames.length} collection times · ${missing} without matching probes · ${this.duplicates} overlapping observations removed · ${this.skipped} incomplete observations excluded. ${!frame.rows.length ? 'No matching measurement at this time.' : 'Only uploaded, successfully measured paths are shown.'}`;
      this.el('flowLegend').innerHTML = `<span style="--dot:${COLORS.low}">&lt; ${number(this.threshold() / 2)} µs</span><span style="--dot:${COLORS.mid}">${number(this.threshold() / 2)}–&lt;${number(this.threshold())} µs</span><span style="--dot:${COLORS.high}">≥ ${number(this.threshold())} µs</span>`;
      const max = routes[0] ? routes[0][this.metric()] : 1;
      this.el('flowRoutes').innerHTML = routes.length ? routes.map(route => `<button class="flow-route" data-route="${escape(route.key)}" aria-pressed="${Boolean(this.focus && this.focus.key === route.key)}"><span class="flow-route-head"><span class="flow-route-address">${escape(route.sourceIp)}<br>→ ${escape(route.targetIp)}</span><strong style="color:${this.color(route[this.metric()])}">${number(route[this.metric()])} µs</strong></span><span class="flow-route-meta">${route.count} probes · max ${number(route.max)} µs</span><span class="flow-route-bar"><i style="width:${Math.max(1, route[this.metric()] / (max || 1) * 100)}%;background:${this.color(route[this.metric()])}"></i></span></button>`).join('') : '<p class="flow-note">No matching probes in this sample. Choose another time or clear the selection.</p>';
      this.renderNetwork(shown);
      this.renderMatrix(routes);
    }

    renderNetwork(routes) {
      const nodes = [...new Set(routes.flatMap(route => [route.sourceIp, route.targetIp]))].sort(compareIp);
      const sources = new Set(routes.map(route => route.sourceIp));
      const positions = new Map();
      const hub = sources.size === 1 && nodes.length > 2 ? [...sources][0] : null;
      const outer = nodes.filter(ip => ip !== hub);
      if (hub) positions.set(hub, [50, 50]);
      outer.forEach((ip, i) => {
        const angle = -Math.PI / 2 + 2 * Math.PI * i / outer.length;
        positions.set(ip, [50 + 32 * Math.cos(angle), 50 + 36 * Math.sin(angle)]);
      });
      const maximum = Math.max(1, ...routes.map(route => route[this.metric()]));
      const base = this.base('network');
      const chart = this.chart('network', 'flowNetwork');
      const compact = chart.getWidth() < 500;
      this.compactNetwork = compact;
      chart.setOption({ ...base,
        grid: { left: 0, right: 0, top: 26, bottom: 25 },
        xAxis: { type: 'value', min: 0, max: 100, show: false }, yAxis: { type: 'value', min: 0, max: 100, show: false },
        tooltip: { ...base.tooltip, formatter: params => params.data.route ? this.routeTooltip(params.data.route) : `<strong>${escape(params.data.ip)}</strong><br>${routes.filter(route => route.sourceIp === params.data.ip).length} outgoing / ${routes.filter(route => route.targetIp === params.data.ip).length} incoming displayed routes` },
        graphic: nodes.length ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: 'No measured paths in this sample', fill: COLORS.muted, fontSize: 14 } }],
        series: [
          { type: 'lines', coordinateSystem: 'cartesian2d', z: 2, clip: true,
            symbol: ['none', 'arrow'], symbolSize: 7,
            effect: { show: !this.reducedMotion.matches, period: 4, trailLength: .12, symbol: 'arrow', symbolSize: 6 },
            lineStyle: { curveness: .12, opacity: .7 }, emphasis: { lineStyle: { opacity: 1, width: 5 } },
            data: routes.map(route => ({ coords: [positions.get(route.sourceIp), positions.get(route.targetIp)], route,
              lineStyle: { color: this.color(route[this.metric()]), width: 1.3 + 4 * Math.sqrt(route[this.metric()] / maximum) } })) },
          { type: 'scatter', coordinateSystem: 'cartesian2d', z: 3,
            data: nodes.map(ip => ({ value: positions.get(ip), ip, name: ip, symbolSize: ip === hub ? 36 : sources.has(ip) ? 25 : 18,
              itemStyle: { color: sources.has(ip) ? '#254b70' : '#1b514f', borderColor: sources.has(ip) ? COLORS.blue : COLORS.low, borderWidth: 2 },
              label: { show: nodes.length <= 28 || sources.has(ip), formatter: compact ? ip.split('.').slice(-2).join('.') : ip, position: ip === hub ? 'bottom' : positions.get(ip)[0] < 45 ? 'left' : positions.get(ip)[0] > 55 ? 'right' : positions.get(ip)[1] > 50 ? 'top' : 'bottom', fontSize: compact ? 9 : 10, color: '#d4e7f2', distance: 7 } })),
            emphasis: { scale: 1.25, label: { show: true } } }
        ]
      }, { notMerge: true });
    }

    renderTimeline() {
      const frames = this.filteredFrames;
      const spacings = frames.slice(1).map((frame, i) => frame.time - frames[i].time).filter(value => value > 0).sort((a, b) => a - b);
      const median = spacings.length ? spacings[Math.floor(spacings.length / 2)] : Infinity;
      const means = [], maxima = [];
      frames.forEach((frame, i) => {
        if (i && frame.time - frames[i - 1].time > median * 1.5) {
          const time = (frame.time + frames[i - 1].time) / 2;
          means.push({ value: [time, null] }); maxima.push({ value: [time, null] });
        }
        means.push({ value: [frame.time, frame.stats.mean], frameIndex: i });
        maxima.push({ value: [frame.time, frame.stats.max], frameIndex: i });
      });
      const base = this.base('latency-timeline');
      this.chart('timeline', 'flowTimeline').setOption({ ...base, useUTC: true,
        grid: { left: 60, right: 24, top: 42, bottom: 72 },
        legend: { data: ['Mean', 'Maximum'], top: 7, textStyle: { color: '#bacede' } },
        tooltip: { ...base.tooltip, trigger: 'axis', formatter: params => {
          const point = params.find(item => Number.isInteger(item.data.frameIndex));
          if (!point) return 'No collection in this gap';
          const frame = frames[point.data.frameIndex];
          return `${escape(frame.timestamp)}<br>Mean: ${number(frame.stats.mean)} µs<br>Maximum: ${number(frame.stats.max)} µs<br>${frame.rows.length} successful probes`;
        } },
        xAxis: { type: 'time', axisLabel: { color: COLORS.muted, hideOverlap: true }, axisLine: { lineStyle: { color: '#344a60' } } },
        yAxis: { type: 'value', name: 'µs', min: 0, axisLabel: { color: COLORS.muted }, splitLine: { lineStyle: { color: '#243649' } } },
        dataZoom: [{ type: 'inside', filterMode: 'none' }, { type: 'slider', bottom: 8, height: 24, borderColor: '#3a536a', textStyle: { color: COLORS.muted }, filterMode: 'none' }],
        series: [
          { id: 'mean', name: 'Mean', type: 'line', showSymbol: frames.length < 80, symbolSize: 7, connectNulls: false, data: means, lineStyle: { color: COLORS.low, width: 2 }, itemStyle: { color: COLORS.low }, areaStyle: { color: COLORS.low, opacity: .07 } },
          { id: 'maximum', name: 'Maximum', type: 'line', showSymbol: frames.length < 80, symbolSize: 7, connectNulls: false, data: maxima, lineStyle: { color: COLORS.high, width: 1.5 }, itemStyle: { color: COLORS.high } }
        ]
      }, { notMerge: true });
      this.markTime();
    }

    markTime() {
      if (!this.charts.timeline || !this.frames[this.index]) return;
      this.charts.timeline.setOption({ series: [{ id: 'mean', markLine: { silent: true, symbol: 'none', animation: false,
        data: [{ xAxis: this.frames[this.index].time, label: { show: false }, lineStyle: { color: '#c3dfef', type: 'dashed' } }] } }] });
    }

    renderMatrix(routes) {
      // Keep axes stable through playback, including absent measurements in a frame.
      const rows = this.filteredFrames.flatMap(frame => frame.routes);
      const sources = [...new Set(rows.map(route => route.sourceIp))].sort(compareIp);
      const targets = [...new Set(rows.map(route => route.targetIp))].sort(compareIp);
      const base = this.base('route-matrix');
      this.el('flowMatrix').style.height = `${Math.max(260, Math.min(550, sources.length * 36 + 190))}px`;
      const chart = this.chart('matrix', 'flowMatrix');
      const threshold = this.threshold();
      const axes = JSON.stringify([sources, targets]);
      const columns = this.matrixColumns();
      const zoom = this.matrixAxes === axes && this.matrixColumnCount === columns ? chart.getOption().dataZoom || [] : [];
      this.matrixAxes = axes;
      this.matrixColumnCount = columns;
      const range = (index, endValue) => zoom[index]
        ? { start: zoom[index].start, end: zoom[index].end }
        : { startValue: 0, endValue: Math.max(0, endValue) };
      chart.setOption({ ...base,
        grid: { left: 125, right: sources.length > 10 ? 48 : 20, top: 50, bottom: 115 },
        tooltip: { ...base.tooltip, formatter: params => this.routeTooltip(params.data.route) },
        xAxis: { type: 'category', data: targets, axisLabel: { color: COLORS.muted, rotate: 55, fontSize: 10, interval: 0 }, splitArea: { show: true, areaStyle: { color: ['#132638', '#152a3c'] } } },
        yAxis: { type: 'category', data: sources, inverse: true, axisLabel: { color: COLORS.muted, fontSize: 11 }, splitArea: { show: true } },
        visualMap: { show: false, type: 'piecewise', dimension: 2, pieces: [
          { lt: threshold / 2, color: COLORS.low }, { gte: threshold / 2, lt: threshold, color: COLORS.mid }, { gte: threshold, color: COLORS.high }
        ] },
        dataZoom: [
          { type: 'slider', xAxisIndex: 0, bottom: 8, height: 22, ...range(0, Math.min(targets.length - 1, columns - 1)), filterMode: 'filter', textStyle: { color: COLORS.muted } },
          ...(sources.length > 10 ? [{ type: 'slider', yAxisIndex: 0, right: 0, width: 18, ...range(1, 9), filterMode: 'filter' }] : [])
        ],
        graphic: routes.length ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: 'No matching route measurements', fill: COLORS.muted } }],
        series: [{ type: 'heatmap', data: routes.map(route => ({ value: [targets.indexOf(route.targetIp), sources.indexOf(route.sourceIp), route[this.metric()]], route })),
          itemStyle: { borderColor: '#101d2b', borderWidth: 3, borderRadius: 4 },
          label: { show: true, color: '#091c27', fontSize: 10, formatter: params => Math.round(params.value[2]).toLocaleString() },
          emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 2 } } }]
      }, { notMerge: true });
      chart.resize();
    }

    csv() {
      const header = ['collection_source_clock', 'source_ip', 'target_ip', 'queues', 'successful_probes', 'mean_us', 'p95_us', 'max_us', 'probes_at_or_above_threshold', 'threshold_us', 'files'];
      const rows = this.filteredFrames.flatMap(frame => frame.routes.map(route => [frame.timestamp, route.sourceIp, route.targetIp,
        [...new Set(route.rows.map(row => row.queue))].sort(compareIp).join(';'), route.count, route.mean, route.p95, route.max,
        route.rows.filter(row => row.usec >= this.threshold()).length, this.threshold(), [...new Set(route.rows.map(row => row.file))].join(';')]));
      const cell = value => `"${String(value).replace(/^[=+@\-]/, "'$&").replace(/"/g, '""')}"`;
      return [header, ...rows].map(row => row.map(cell).join(',')).join('\r\n');
    }

    download() {
      const url = URL.createObjectURL(new Blob([this.csv()], { type: 'text/csv;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'rds-network-latency.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      root.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }

  root.RdsNetworkView = RdsNetworkView;
  // Pure data helpers are also available to the dependency-free parser tests.
  if (typeof module !== 'undefined' && module.exports) module.exports = { clockTime, statistics, buildFrames, aggregateRoutes };
})(typeof window === 'undefined' ? globalThis : window);
