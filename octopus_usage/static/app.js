'use strict';

const FUEL_META = {
  electricity: { name: 'Electricity', unit: 'kWh', color: 'oklch(0.70 0.16 230)', soft: 'oklch(0.70 0.16 230 / 0.16)' },
  gas: { name: 'Gas', unit: 'm³', color: 'oklch(0.78 0.14 75)', soft: 'oklch(0.78 0.14 75 / 0.16)' },
};
const FUEL_KEYS = ['electricity', 'gas'];
const TEMP_COLOR = 'oklch(0.78 0.09 25)';
const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const PRESETS = [
  ['yesterday', 'Yesterday'], ['7d', 'Last 7 days'], ['month', 'This month'], ['year', 'Calendar year'],
];
const NAV = [
  ['usage', 'Usage'], ['forecast', 'Forecast'], ['meters', 'Meters'], ['tariff', 'Tariff'], ['settings', 'Settings'],
];

const state = {
  screen: 'usage',
  preset: 'month',
  year: new Date().getFullYear(),
  on: { electricity: true, gas: true },
  heatFuel: 'electricity',
};
const cache = new Map();

function api(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then(r => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(url);
}

/* ---------- formatting ---------- */

function money(pence) {
  if (pence == null) return '—';
  const v = pence / 100;
  const dp = Math.abs(v) < 100 ? 2 : 0;
  return '£' + v.toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function money0(pence) {
  if (pence == null) return '—';
  return '£' + Math.round(pence / 100).toLocaleString('en-GB');
}

function fmtNum(v) {
  return v.toLocaleString('en-GB', { maximumFractionDigits: v < 20 ? 1 : 0 });
}

function fmtUnits(v, unit) {
  return fmtNum(v) + ' ' + unit;
}

function sumOrNull(vals) {
  if (!vals.length) return null;
  let s = 0;
  for (const v of vals) {
    if (v == null) return null;
    s += v;
  }
  return s;
}

function londonToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(new Date());
}

function londonHour(iso) {
  const h = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', hour12: false })
    .format(new Date(iso));
  return parseInt(h, 10) % 24;
}

function prettyDate(dateStr) {
  return new Date(dateStr + 'T12:00:00Z')
    .toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

function weekdayAbbr(dateStr) {
  return new Date(dateStr + 'T12:00:00Z').toLocaleDateString('en-GB', { weekday: 'short' });
}

function monthShort(ym) {
  return MONTH_ABBR[parseInt(ym.slice(5), 10) - 1] + ' ' + ym.slice(2, 4);
}

function monthLong(ym) {
  return MONTH_ABBR[parseInt(ym.slice(5), 10) - 1] + ' ' + ym.slice(0, 4);
}

function monthFull(ym) {
  return new Date(ym + '-15T12:00:00Z').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
}

function daysInMonth(ym) {
  return new Date(parseInt(ym.slice(0, 4), 10), parseInt(ym.slice(5), 10), 0).getDate();
}

function prevYm(ym) {
  let y = parseInt(ym.slice(0, 4), 10);
  let m = parseInt(ym.slice(5), 10) - 1;
  if (m === 0) { m = 12; y -= 1; }
  return `${y}-${String(m).padStart(2, '0')}`;
}

/* ---------- period assembly (usage screen) ---------- */

async function buildPeriod(fuel) {
  const gas = fuel === 'gas';
  const val = d => (gas ? d.units : d.kwh);
  const hist = await api(`/api/history?fuel=${fuel}&days=70`);
  const days = hist ? hist.days : [];

  if (state.preset === 'yesterday') {
    // Smart-meter data lags and the newest day is usually partial: show the
    // latest complete day instead, labelled with its actual date.
    const target = [...days].reverse().find(d => d.complete) || days[days.length - 1];
    if (!target) return null;
    const hh = await api(`/api/halfhourly?fuel=${fuel}&date=${target.date}`);
    if (!hh || !hh.intervals.length) return null;
    const idx = days.findIndex(d => d.date === hh.date);
    const dayRow = idx >= 0 ? days[idx] : null;
    const bars = hh.intervals.map((iv, i) => ({
      v: gas ? iv.units : iv.kwh,
      ghost: 0,
      label: i % 8 === 0 ? String(londonHour(iv.start)).padStart(2, '0') + ':00' : '',
    }));
    return {
      bars, gap: 2,
      label: prettyDate(hh.date),
      units: bars.reduce((a, b) => a + b.v, 0),
      cost: dayRow ? dayRow.cost_pence : sumOrNull(hh.intervals.map(iv => iv.cost_pence)),
      prevCost: idx > 0 ? days[idx - 1].cost_pence : null,
      days: dayRow ? [dayRow] : [],
      scDays: 1,
    };
  }

  if (state.preset === '7d') {
    const whole = days.filter(d => d.complete);
    if (!whole.length) return null;
    const sel = whole.slice(-7);
    const prev = whole.slice(-14, -7);
    return {
      bars: sel.map(d => ({ v: val(d), ghost: 0, label: weekdayAbbr(d.date) })),
      gap: 8,
      label: 'last 7 days',
      units: sel.reduce((a, d) => a + val(d), 0),
      cost: sumOrNull(sel.map(d => d.cost_pence)),
      prevCost: prev.length === 7 ? sumOrNull(prev.map(d => d.cost_pence)) : null,
      days: sel,
      scDays: sel.length,
    };
  }

  if (state.preset === 'month') {
    const ym = londonToday().slice(0, 7);
    const sel = days.filter(d => d.date.startsWith(ym));
    const prevRows = days.filter(d => d.date.startsWith(prevYm(ym)));
    const prevByDom = new Map(prevRows.map(d => [parseInt(d.date.slice(8), 10), d]));
    const bars = sel.map(d => {
      const dom = parseInt(d.date.slice(8), 10);
      const ghost = prevByDom.get(dom);
      return {
        v: val(d),
        ghost: ghost ? val(ghost) : 0,
        label: dom === 1 || dom % 5 === 0 ? String(dom) : '',
      };
    });
    const prevSel = prevRows.filter(d => parseInt(d.date.slice(8), 10) <= sel.length);
    return {
      bars, gap: 4,
      label: monthFull(ym),
      units: sel.reduce((a, d) => a + val(d), 0),
      cost: sumOrNull(sel.map(d => d.cost_pence)),
      prevCost: prevSel.length === sel.length ? sumOrNull(prevSel.map(d => d.cost_pence)) : null,
      days: sel,
      scDays: sel.length,
    };
  }

  // calendar year
  const cur = await api(`/api/monthly?fuel=${fuel}&year=${state.year}`);
  const prev = await api(`/api/monthly?fuel=${fuel}&year=${state.year - 1}`);
  if (!cur || !cur.months.length) return null;
  const prevByM = new Map((prev ? prev.months : []).map(m => [m.month.slice(5), m]));
  const nowYm = londonToday().slice(0, 7);
  // Keep one slot per month across the data range so gaps (e.g. a meter
  // comms outage) show as empty months rather than silently closing up.
  const nums = cur.months.map(m => parseInt(m.month.slice(5), 10));
  const byNum = new Map(cur.months.map(m => [parseInt(m.month.slice(5), 10), m]));
  const bars = [];
  for (let n = Math.min(...nums); n <= Math.max(...nums); n++) {
    const m = byNum.get(n);
    const ghost = prevByM.get(String(n).padStart(2, '0'));
    bars.push({
      v: m ? (gas ? m.units : m.kwh) : 0,
      ghost: ghost ? (gas ? ghost.units : ghost.kwh) : 0,
      label: MONTH_ABBR[n - 1],
    });
  }
  const prevAligned = cur.months.map(m => prevByM.get(m.month.slice(5))).filter(Boolean);
  return {
    bars, gap: 10,
    label: String(state.year),
    units: cur.months.reduce((a, m) => a + (gas ? m.units : m.kwh), 0),
    cost: sumOrNull(cur.months.map(m => m.cost_pence)),
    prevCost: prevAligned.length === cur.months.length
      ? sumOrNull(prevAligned.map(m => m.cost_pence)) : null,
    months: cur.months,
    days: [],
    scDays: cur.months.reduce(
      (a, m) => a + (m.month === nowYm ? parseInt(londonToday().slice(8), 10) : daysInMonth(m.month)), 0),
  };
}

/* ---------- shared chart pieces ---------- */

function barsHTML(bars, color, gapPx, overlay) {
  const max = Math.max(...bars.map(b => Math.max(b.v, b.ghost || 0)), 0) * 1.08 || 1;
  const slots = bars.map(b => {
    const h = (Math.max(0, b.v) / max * 100).toFixed(1);
    const gh = (Math.max(0, b.ghost || 0) / max * 100).toFixed(1);
    return '<div class="bar-slot">'
      + (b.ghost ? `<div class="bar-ghost" style="height:${gh}%"></div>` : '')
      + `<div class="bar-fill" style="height:${h}%;background:${color}"></div></div>`;
  }).join('');
  const labels = bars.map(b => `<span>${b.label || ''}</span>`).join('');
  return `<div class="bars" style="gap:${gapPx}px">${slots}${overlay || ''}</div>`
    + `<div class="bar-labels" style="gap:${gapPx}px">${labels}</div>`;
}

/* ---------- weather ---------- */

async function fetchWeather(per, active) {
  if (!active.length) return null;
  const p = per[active[0]];
  if (state.preset === 'yesterday') {
    const d = p.days[0] && p.days[0].date;
    if (!d) return null;
    const w = await api(`/api/weather?date=${d}`);
    return w && w.available ? { hours: w.hours } : null;
  }
  let start, end;
  if (state.preset === 'year') {
    start = `${state.year}-01-01`;
    end = `${state.year}-12-31`;
  } else {
    if (!p.days.length) return null;
    start = p.days[0].date;
    end = p.days[p.days.length - 1].date;
  }
  const w = await api(`/api/weather?start=${start}&end=${end}`);
  return w && w.available && w.days.length ? { days: w.days } : null;
}

function weatherSeries(w) {
  if (!w) return null;
  if (w.hours) {
    const vals = w.hours.filter(v => v != null);
    if (!vals.length) return null;
    return {
      pts: w.hours,
      lo: Math.min(...vals), hi: Math.max(...vals),
      mean: vals.reduce((a, b) => a + b, 0) / vals.length,
    };
  }
  let pts;
  if (state.preset === 'year') {
    const byM = new Map();
    for (const d of w.days) {
      const e = byM.get(d.date.slice(0, 7)) || { s: 0, n: 0 };
      e.s += d.tmean;
      e.n += 1;
      byM.set(d.date.slice(0, 7), e);
    }
    pts = [...byM.keys()].sort().map(k => byM.get(k).s / byM.get(k).n);
  } else {
    pts = w.days.map(d => d.tmean);
  }
  const means = w.days.map(d => d.tmean);
  return {
    pts,
    lo: Math.min(...w.days.map(d => d.tmin)), hi: Math.max(...w.days.map(d => d.tmax)),
    mean: means.reduce((a, b) => a + b, 0) / means.length,
  };
}

function tempPolyline(pts) {
  const vals = pts.filter(v => v != null);
  if (vals.length < 2) return '';
  const lo = Math.min(...vals);
  const span = Math.max(1e-6, Math.max(...vals) - lo);
  return pts.map((v, i) => (v == null ? null
    : `${(i / (pts.length - 1) * 1000).toFixed(1)},${(176 - (v - lo) / span * 128).toFixed(1)}`))
    .filter(Boolean).join(' ');
}

function tempOverlaySVG(ws) {
  const points = ws && tempPolyline(ws.pts);
  if (!points) return '';
  return `<svg viewBox="0 0 1000 200" preserveAspectRatio="none"
      style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">
    <polyline points="${points}" fill="none" stroke="${TEMP_COLOR}" stroke-width="1.75"
      vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline></svg>`;
}

function weatherCardHTML(ws, label) {
  const points = ws && tempPolyline(ws.pts);
  if (!points) return '';
  return `<div class="rail-card"><span class="mono-label">Weather</span>
    <div class="weather-mean">
      <span class="big">${ws.mean.toFixed(1)}°C</span>
      <span class="sub">${ws.lo.toFixed(1)}° – ${ws.hi.toFixed(1)}° over ${label}</span>
    </div>
    <svg viewBox="0 0 1000 200" preserveAspectRatio="none" style="width:100%;height:52px;display:block">
      <polyline points="${points}" fill="none" stroke="${TEMP_COLOR}" stroke-width="1.75"
        vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline>
    </svg>
    <span class="weather-note">Gas tracks temperature closely; electricity barely moves with it.</span>
  </div>`;
}

function usageCardHTML(fuel, p, overlay) {
  const f = FUEL_META[fuel];
  const totals = p && p.bars.length
    ? `<div class="chart-totals"><span class="units" style="color:${f.color}">${fmtUnits(p.units, f.unit)}</span>`
      + `<span class="cost">${money(p.cost)}</span></div>`
    : '';
  const body = p && p.bars.length
    ? barsHTML(p.bars, f.color, p.gap, overlay)
    : '<div class="empty-note">No data yet</div>';
  return `<div class="chart-card">
    <div class="chart-head">
      <div class="chart-title"><span class="dot" style="background:${f.color}"></span>
        <span class="name">${f.name}</span></div>
      ${totals}
    </div>
    ${body}
  </div>`;
}

function heatmapHTML(heat) {
  const f = FUEL_META[state.heatFuel];
  const tabs = FUEL_KEYS.map(k =>
    `<button type="button" data-heat="${k}" class="${state.heatFuel === k ? 'active' : ''}">`
    + `${k === 'gas' ? 'Gas' : 'Electric'}</button>`).join('');
  let body = '<div class="empty-note">No data yet</div>';
  if (heat && heat.rows.length) {
    const max = Math.max(...heat.rows.flatMap(r => r.cells)) || 1;
    const rows = heat.rows.map(r => {
      const cells = r.cells.map(v => {
        const op = (0.07 + 0.93 * Math.pow(v / max, 1.25)).toFixed(3);
        return `<div class="heat-cell" style="background:${f.color};opacity:${op}"></div>`;
      }).join('');
      return `<div class="heat-row"><span class="heat-day">${r.day}</span>`
        + `<div class="heat-cells">${cells}</div></div>`;
    }).join('');
    const hours = Array.from({ length: 24 }, (_, h) =>
      `<span>${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>`).join('');
    body = `<div class="heat-body">${rows}
      <div class="heat-row"><span class="heat-day"></span><div class="heat-hours">${hours}</div></div>
    </div>`;
  }
  return `<div class="chart-card">
    <div class="chart-head" style="align-items:center">
      <span class="name" style="font-size:14px;font-weight:600">Peak hours — ${f.name.toLowerCase()}, last 12 weeks</span>
      <div class="heat-tabs">${tabs}</div>
    </div>
    ${body}
  </div>`;
}

function peakSlot(heat) {
  if (!heat || !heat.rows.length) return null;
  let best = null;
  for (const r of heat.rows) {
    r.cells.forEach((v, h) => {
      if (!best || v > best.v) best = { v, h, day: r.day };
    });
  }
  return best ? `${best.day} ${String(best.h).padStart(2, '0')}:00` : null;
}

/* ---------- usage screen ---------- */

async function renderUsage(summary) {
  const per = {};
  await Promise.all(FUEL_KEYS.map(async f => {
    if (state.on[f] && summary.fuels[f]) per[f] = await buildPeriod(f);
  }));
  const heat = summary.fuels[state.heatFuel] ? await api(`/api/heatmap?fuel=${state.heatFuel}`) : null;
  const active = FUEL_KEYS.filter(f => per[f]);
  const ws = weatherSeries(await fetchWeather(per, active));

  const charts = document.getElementById('usage-charts');
  charts.innerHTML = FUEL_KEYS.filter(f => state.on[f])
    .map(f => usageCardHTML(f, per[f], f === 'electricity' ? tempOverlaySVG(ws) : ''))
    .join('') + heatmapHTML(heat);

  const label = active.length ? per[active[0]].label
    : { yesterday: 'yesterday', '7d': 'last 7 days', month: monthFull(londonToday().slice(0, 7)), year: String(state.year) }[state.preset];

  const heroCost = sumOrNull(active.map(f => per[f].cost));
  const prevCost = sumOrNull(active.map(f => per[f].prevCost));
  let deltaHTML = '';
  if (heroCost != null && prevCost != null && prevCost > 0) {
    const pct = (heroCost - prevCost) / prevCost * 100;
    const up = pct >= 0;
    deltaHTML = `<span class="hero-line hero-delta ${up ? 'up' : 'down'}">`
      + `${up ? '▲' : '▼'} ${Math.abs(pct).toFixed(1)}% vs previous period</span>`;
  }

  const splitRows = FUEL_KEYS.map(f => {
    const meta = FUEL_META[f];
    let v = 'off';
    if (state.on[f]) v = per[f] ? `${fmtUnits(per[f].units, meta.unit)} · ${money(per[f].cost)}` : '—';
    return `<div class="split-row"><span class="k">${meta.name}</span>`
      + `<span class="v" style="color:${state.on[f] && per[f] ? meta.color : 'var(--muted)'}">${v}</span></div>`;
  }).join('');

  const notable = [];
  const peak = peakSlot(heat);
  if (peak) notable.push(['Peak slot', peak]);
  if (state.preset === 'year') {
    // A month only qualifies when every active fuel has priced data for it —
    // a fuel's outage month would otherwise look artificially cheap.
    const nowYm = londonToday().slice(0, 7);
    const costByFuel = active.map(f => new Map((per[f].months || []).map(b => [b.month, b.cost_pence])));
    const priced = [...new Set(costByFuel.flatMap(m => [...m.keys()]))]
      .filter(k => k !== nowYm)
      .map(k => [k, costByFuel.map(m => m.get(k))])
      .filter(([, vals]) => vals.every(v => v != null))
      .map(([k, vals]) => [k, vals.reduce((a, b) => a + b, 0)]);
    if (priced.length) {
      const min = priced.reduce((a, b) => (b[1] < a[1] ? b : a));
      notable.push(['Cheapest month', `${monthShort(min[0])} · ${money(min[1])}`]);
    }
  } else {
    const byDate = new Map();
    for (const f of active) {
      for (const d of per[f].days) {
        if (d.cost_pence == null || !d.complete) continue;
        byDate.set(d.date, (byDate.get(d.date) || 0) + d.cost_pence);
      }
    }
    if (byDate.size) {
      const min = [...byDate.entries()].reduce((a, b) => (b[1] < a[1] ? b : a));
      notable.push(['Cheapest day', `${weekdayAbbr(min[0])} · ${money(min[1])}`]);
    }
  }
  const scPerDay = sumOrNull(active.map(f => summary.fuels[f].standing_charge));
  if (scPerDay != null && active.length) {
    notable.push(['Standing charges', money(per[active[0]].scDays * scPerDay)]);
  }
  const notableHTML = notable.map(([k, v]) =>
    `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('');

  document.getElementById('usage-rail').innerHTML = `
    <div class="hero">
      <span class="mono-label">Spend so far — ${label}</span>
      <span class="hero-value">${money(heroCost)}</span>
      ${deltaHTML}
    </div>
    <div class="split-card">${splitRows}</div>
    ${weatherCardHTML(ws, label)}
    <div class="rail-card"><span class="mono-label">Notable</span>${notableHTML}</div>`;
}

/* ---------- forecast screen ---------- */

function buildFcSeries(months, todayYm) {
  const map = new Map();
  for (const m of months) {
    const e = map.get(m.month) || { actualKwh: 0, fcKwh: 0, fcCost: null, hasActual: false };
    if (m.forecast) { e.fcKwh += m.kwh; e.fcCost = m.cost_pence; }
    else { e.actualKwh += m.kwh; e.hasActual = true; }
    map.set(m.month, e);
  }
  const keys = [...map.keys()].sort();
  const actualKeys = keys.filter(k => map.get(k).hasActual && k <= todayYm).slice(-6);
  // The 365-day forecast horizon ends mid-month, so the last bucket is a
  // partial month — drop it rather than chart/tabulate a misleading dip.
  const futureKeys = keys.filter(k => k > todayYm).slice(0, -1).slice(0, 12);
  const pts = actualKeys.map(k => ({ k, v: map.get(k).actualKwh + map.get(k).fcKwh }))
    .concat(futureKeys.map(k => ({ k, v: map.get(k).fcKwh })));
  return { pts, splitIdx: actualKeys.length - 1, futureKeys, map };
}

function fcChartSVG(series, color, soft) {
  const pts = series.pts;
  const n = pts.length;
  if (n < 2) return '<div class="empty-note">Not enough history to forecast yet</div>';
  const max = Math.max(...pts.map(p => p.v)) * 1.24 || 1;
  const X = i => (i / (n - 1) * 1000).toFixed(1);
  const Y = v => (200 - v / max * 190).toFixed(1);
  const s = Math.max(0, series.splitIdx);
  let band = `M ${X(s)} ${Y(pts[s].v)}`;
  for (let i = s + 1; i < n; i++) band += ` L ${X(i)} ${Y(pts[i].v * 1.19)}`;
  for (let i = n - 1; i > s; i--) band += ` L ${X(i)} ${Y(pts[i].v * 0.86)}`;
  band += ' Z';
  let mid = `M ${X(s)} ${Y(pts[s].v)}`;
  for (let i = s + 1; i < n; i++) mid += ` L ${X(i)} ${Y(pts[i].v)}`;
  let actual = `M ${X(0)} ${Y(pts[0].v)}`;
  for (let i = 1; i <= s; i++) actual += ` L ${X(i)} ${Y(pts[i].v)}`;
  const axis = pts.map((p, i) => `<span>${i % 3 === 0 ? monthShort(p.k) : ''}</span>`).join('');
  return `<svg viewBox="0 0 1000 200" preserveAspectRatio="none" style="width:100%;height:150px;display:block">
      <path d="${band}" fill="${soft}"></path>
      <path d="${mid}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"></path>
      <path d="${actual}" fill="none" stroke="${color}" stroke-width="2.25" vector-effect="non-scaling-stroke"></path>
      <line x1="${X(s)}" y1="0" x2="${X(s)}" y2="200" stroke="oklch(0.42 0.01 250)" stroke-width="1" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"></line>
    </svg>
    <div class="fc-axis">${axis}</div>`;
}

async function renderForecast(summary) {
  const yearlyData = await api('/api/yearly');
  const factor = summary.gas_m3_to_kwh;
  const todayYm = londonToday().slice(0, 7);
  const series = {};
  const active = FUEL_KEYS.filter(f =>
    state.on[f] && yearlyData && yearlyData.fuels[f] && yearlyData.fuels[f].months.length);
  for (const f of active) series[f] = buildFcSeries(yearlyData.fuels[f].months, todayYm);

  const dispUnits = (f, kwh) => (f === 'gas' ? kwh / factor : kwh);

  const cards = active.map(f => {
    const meta = FUEL_META[f];
    const s = series[f];
    const futKwh = s.futureKeys.reduce((a, k) => a + s.map.get(k).fcKwh, 0);
    const futCost = sumOrNull(s.futureKeys.map(k => s.map.get(k).fcCost));
    return `<div class="chart-card">
      <div class="chart-head">
        <div class="chart-title"><span class="dot" style="background:${meta.color}"></span>
          <span class="name">${meta.name}</span>
          <span class="sub">monthly ${meta.unit}</span></div>
        <div class="chart-totals"><span class="units" style="color:${meta.color}">
          ${fmtUnits(dispUnits(f, futKwh), meta.unit)} · ${money(futCost)}</span></div>
      </div>
      ${fcChartSVG(s, meta.color, meta.soft)}
    </div>`;
  }).join('');
  document.getElementById('forecast-charts').innerHTML =
    cards || '<div class="chart-card"><div class="empty-note">Not enough history to forecast yet</div></div>';

  const rail = document.getElementById('forecast-rail');
  if (!active.length) { rail.innerHTML = ''; return; }

  const futureKeys = series[active[0]].futureKeys;
  const rows = futureKeys.map(k => {
    const elec = state.on.electricity && series.electricity ? series.electricity.map.get(k) : null;
    const gas = state.on.gas && series.gas ? series.gas.map.get(k) : null;
    const mid = sumOrNull(active.map(f => (series[f].map.get(k) || {}).fcCost));
    return `<div class="fc-row">
      <span class="fc-c-month">${monthShort(k)}</span>
      <span class="fc-c-kwh">${elec ? fmtNum(Math.round(elec.fcKwh)) : '—'}</span>
      <span class="fc-c-m3">${gas ? fmtNum(Math.round(gas.fcKwh / factor)) : '—'}</span>
      <span class="fc-c-low">${money0(mid == null ? null : mid * 0.86)}</span>
      <span class="fc-c-mid">${money0(mid)}</span>
      <span class="fc-c-high">${money0(mid == null ? null : mid * 1.19)}</span>
    </div>`;
  }).join('');

  const totalMid = sumOrNull(futureKeys.map(k => sumOrNull(active.map(f => (series[f].map.get(k) || {}).fcCost))));
  const unitTotals = active.map(f => {
    const s = series[f];
    const tot = s.futureKeys.reduce((a, k) => a + s.map.get(k).fcKwh, 0);
    return fmtUnits(Math.round(dispUnits(f, tot)), FUEL_META[f].unit);
  }).join(' · ');
  const windowLabel = futureKeys.length
    ? `${monthLong(futureKeys[0])} – ${monthLong(futureKeys[futureKeys.length - 1])}` : '';

  rail.innerHTML = `
    <div class="hero">
      <span class="mono-label">Projected — ${windowLabel}</span>
      <div class="hero-row">
        <span class="hero-value">${money(totalMid)}</span>
        <span class="hero-units">${unitTotals}</span>
      </div>
      <span class="hero-line">${totalMid == null ? '' : `${money(totalMid * 0.86)} – ${money(totalMid * 1.19)}`}</span>
    </div>
    <div class="fc-table">
      <div class="fc-head">
        <span class="fc-c-month">Month</span><span class="fc-c-kwh">kWh</span>
        <span class="fc-c-m3">m³</span><span class="fc-c-low">Low</span>
        <span class="fc-c-mid">Exp.</span><span class="fc-c-high">High</span>
      </div>
      ${rows}
    </div>
    <span class="fc-note">Low assumes a mild winter (−14% demand), high a cold one (+19%).
      Tariff held at current unit rates.</span>`;
}

/* ---------- chrome ---------- */

function renderChrome(summary) {
  document.getElementById('nav').innerHTML = NAV.map(([id, label]) => {
    const usable = id === 'usage' || id === 'forecast';
    return `<button type="button" data-nav="${id}" ${usable ? '' : 'disabled'}
      class="${state.screen === id ? 'active' : ''}"><span class="dot"></span>${label}</button>`;
  }).join('');

  document.getElementById('presets').innerHTML = PRESETS.map(([id, label]) =>
    `<button type="button" data-preset="${id}" class="${state.preset === id ? 'active' : ''}">${label}</button>`).join('');

  const minYear = summary && summary.first_data
    ? parseInt(summary.first_data.slice(0, 4), 10) : state.year;
  const maxYear = new Date().getFullYear();
  state.year = Math.min(maxYear, Math.max(minYear, state.year));
  const yearActive = state.screen === 'usage' && state.preset === 'year';
  document.getElementById('year-label').textContent = state.year;
  document.getElementById('prev-year').disabled = !yearActive || state.year <= minYear;
  document.getElementById('next-year').disabled = !yearActive || state.year >= maxYear;

  document.getElementById('fuel-chips').innerHTML = FUEL_KEYS.map(f => {
    const meta = FUEL_META[f];
    const on = state.on[f];
    return `<button type="button" class="fuel-chip" data-fuel="${f}">
      <span class="id"><span class="dot" style="background:${on ? meta.color : 'oklch(0.38 0.01 250)'}"></span>
        <span class="name" style="color:${on ? 'oklch(0.93 0.01 250)' : 'var(--faint)'}">${meta.name}</span></span>
      <span class="track" style="background:${on ? meta.color : 'oklch(0.24 0.012 250)'}">
        <span class="knob" style="left:${on ? 17 : 2}px;background:${on ? 'oklch(0.18 0.02 250)' : 'oklch(0.50 0.01 250)'}"></span>
      </span></button>`;
  }).join('');

  const lines = [];
  if (summary) {
    for (const f of FUEL_KEYS) {
      const serial = summary.meters && summary.meters[f];
      if (serial) lines.push(`${f === 'gas' ? 'GAS' : 'ELEC'} ···${serial.slice(-4)}`);
    }
    if (summary.last_sync) {
      const t = new Date(summary.last_sync).toLocaleTimeString('en-GB',
        { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' });
      lines.push(`Synced ${t}`);
    }
  }
  document.getElementById('meter-lines').innerHTML = lines.map(l => `<span>${l}</span>`).join('');

  const banner = document.getElementById('banner');
  const problem = !summary ? 'Could not load data — is the server running?'
    : (summary.sync_error ? `Sync failed: ${summary.sync_error}` :
      (Object.keys(summary.fuels).length ? null : 'No usage data yet — try Sync now.'));
  banner.hidden = !problem;
  banner.textContent = problem || '';

  document.getElementById('screen-usage').hidden = state.screen !== 'usage';
  document.getElementById('screen-forecast').hidden = state.screen !== 'forecast';
}

/* ---------- render + events ---------- */

let renderSeq = 0;

async function render() {
  const seq = ++renderSeq;
  const summary = await api('/api/summary');
  if (seq !== renderSeq) return;
  renderChrome(summary);
  if (!summary) return;
  if (state.screen === 'usage') await renderUsage(summary);
  else await renderForecast(summary);
}

document.addEventListener('click', async e => {
  const nav = e.target.closest('[data-nav]');
  if (nav && !nav.disabled) { state.screen = nav.dataset.nav; render(); return; }
  const preset = e.target.closest('[data-preset]');
  if (preset) { state.preset = preset.dataset.preset; render(); return; }
  const chip = e.target.closest('[data-fuel]');
  if (chip) { state.on[chip.dataset.fuel] = !state.on[chip.dataset.fuel]; render(); return; }
  const heat = e.target.closest('[data-heat]');
  if (heat) { state.heatFuel = heat.dataset.heat; render(); return; }
  if (e.target.id === 'prev-year') { state.year -= 1; render(); return; }
  if (e.target.id === 'next-year') { state.year += 1; render(); return; }
  if (e.target.id === 'sync-now') {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = 'Syncing…';
    try { await fetch('/api/sync', { method: 'POST' }); } catch { /* banner shows the error */ }
    cache.clear();
    btn.disabled = false;
    btn.textContent = 'Sync now';
    render();
  }
});

render();
