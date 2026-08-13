const $ = (sel) => document.querySelector(sel);
const cssVar = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

const FUEL_META = {
  electricity: { label: "Electricity", colorVar: "--series-1" },
  gas: { label: "Gas", colorVar: "--series-2" },
};

const state = { days: 30, unit: "kwh", forecastFuel: null, fuels: [] };
let historyChart = null;
let forecastChart = null;

const fmt = {
  kwh: (v) => (v == null ? "—" : `${v.toFixed(1)} kWh`),
  cost: (p) => (p == null ? "—" : `£${(p / 100).toFixed(2)}`),
};

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function shortDate(iso) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// Inclusive ISO-date range; missing dates become nulls so charts show gaps.
function dateRange(fromIso, toIso) {
  const out = [];
  const d = new Date(fromIso + "T12:00:00Z");
  for (;;) {
    const iso = d.toISOString().slice(0, 10);
    out.push(iso);
    if (iso === toIso) return out;
    d.setUTCDate(d.getUTCDate() + 1);
  }
}

function series(labels, rows, key) {
  const byDate = Object.fromEntries(rows.map((r) => [r.date, r[key]]));
  return labels.map((l) => byDate[l] ?? null);
}

function baseOptions(valueFmt) {
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: {
        grid: { display: false },
        border: { color: cssVar("--axis") },
        ticks: { color: cssVar("--muted"), maxTicksLimit: 12, maxRotation: 0 },
      },
      y: {
        beginAtZero: true,
        grid: { color: cssVar("--grid") },
        border: { display: false },
        ticks: { color: cssVar("--muted") },
      },
    },
    plugins: {
      legend: {
        labels: {
          color: cssVar("--text-secondary"),
          boxWidth: 12,
          boxHeight: 12,
          filter: (item) => !item.text.startsWith("_"),
        },
      },
      tooltip: {
        filter: (item) => !item.dataset.label.startsWith("_"),
        callbacks: { label: (ctx) => `${ctx.dataset.label}: ${valueFmt(ctx.parsed.y)}` },
      },
    },
  };
}

function showBanner(message) {
  const banner = $("#banner");
  banner.textContent = `⚠ ${message}`;
  banner.hidden = false;
}

function renderTiles(summary) {
  const tiles = [
    ["yesterday", "Yesterday"],
    ["last_7", "Last 7 days"],
    ["last_30", "Last 30 days"],
    ["next_30", "Next 30 days (projected)"],
  ];
  $("#tiles").innerHTML = state.fuels
    .map((fuel) => `
      <div class="fuel-row">
        <h2><span class="swatch" style="background:${cssVar(FUEL_META[fuel].colorVar)}"></span>${FUEL_META[fuel].label}</h2>
        <div class="tile-grid">${tiles
          .map(([key, label]) => {
            const t = summary.fuels[fuel][key];
            return `<div class="tile">
              <div class="tile-label">${label}</div>
              <div class="tile-value">${fmt.kwh(t.kwh)}</div>
              <div class="tile-sub">${fmt.cost(t.cost_pence)}</div>
            </div>`;
          })
          .join("")}</div>
      </div>`)
    .join("");
}

async function renderHistory() {
  const perFuel = await Promise.all(
    state.fuels.map((fuel) =>
      fetch(`/api/history?fuel=${fuel}&days=${state.days}`).then((r) => (r.ok ? r.json() : { days: [] })))
  );
  const allDates = perFuel.flatMap((p) => p.days.map((d) => d.date)).sort();
  if (!allDates.length) return;
  const labels = dateRange(allDates[0], allDates[allDates.length - 1]);
  const key = state.unit === "cost" ? "cost_pence" : "kwh";
  const datasets = state.fuels.map((fuel, i) => ({
    label: FUEL_META[fuel].label,
    data: series(labels, perFuel[i].days, key),
    backgroundColor: cssVar(FUEL_META[fuel].colorVar),
    borderRadius: 4,
    maxBarThickness: 24,
    barPercentage: 0.9,
    categoryPercentage: 0.8,
  }));
  historyChart?.destroy();
  historyChart = new Chart($("#history-chart"), {
    type: "bar",
    data: { labels: labels.map(shortDate), datasets },
    options: baseOptions(state.unit === "cost" ? fmt.cost : fmt.kwh),
  });
}

async function renderForecast() {
  const fuel = state.forecastFuel;
  if (!fuel) return;
  const [hist, fc] = await Promise.all([
    fetch(`/api/history?fuel=${fuel}&days=30`).then((r) => (r.ok ? r.json() : { days: [] })),
    fetch(`/api/forecast?fuel=${fuel}`).then((r) => (r.ok ? r.json() : { points: [] })),
  ]);
  if (!hist.days.length) return;
  const points = fc.points;
  const lastDate = (points.length ? points[points.length - 1] : hist.days[hist.days.length - 1]).date;
  const labels = dateRange(hist.days[0].date, lastDate);
  const color = cssVar(FUEL_META[fuel].colorVar);
  // Join the two lines: the forecast series starts from the last actual point.
  const lastActual = hist.days[hist.days.length - 1];
  const fcRows = points.length
    ? [{ date: lastActual.date, kwh: lastActual.kwh, lower: lastActual.kwh, upper: lastActual.kwh }, ...points]
    : [];
  const datasets = [
    {
      label: "Actual",
      data: series(labels, hist.days.filter((d) => d.complete), "kwh"),
      borderColor: color, borderWidth: 2, pointRadius: 0, spanGaps: false,
    },
    {
      label: "Forecast",
      data: series(labels, fcRows, "kwh"),
      borderColor: color, borderWidth: 2, borderDash: [5, 4], pointRadius: 0,
    },
    {
      label: "_upper",
      data: series(labels, fcRows, "upper"),
      borderWidth: 0, pointRadius: 0, fill: "+1", backgroundColor: hexToRgba(color, 0.18),
    },
    { label: "_lower", data: series(labels, fcRows, "lower"), borderWidth: 0, pointRadius: 0 },
  ];
  forecastChart?.destroy();
  forecastChart = new Chart($("#forecast-chart"), {
    type: "line",
    data: { labels: labels.map(shortDate), datasets },
    options: baseOptions(fmt.kwh),
  });
}

function renderFuelTabs() {
  const group = $("#forecast-fuel");
  group.innerHTML = state.fuels
    .map((fuel) =>
      `<button data-fuel="${fuel}" class="${fuel === state.forecastFuel ? "active" : ""}">${FUEL_META[fuel].label}</button>`)
    .join("");
  group.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.forecastFuel = btn.dataset.fuel;
      renderFuelTabs();
      renderForecast();
    })
  );
}

async function loadAll() {
  const res = await fetch("/api/summary");
  if (!res.ok) {
    showBanner("The app isn't configured yet — see the README.");
    return;
  }
  const summary = await res.json();
  state.fuels = Object.keys(summary.fuels);
  if (!state.fuels.length) {
    showBanner("No smart meter data yet — press Refresh to sync.");
    return;
  }
  if (!state.forecastFuel || !state.fuels.includes(state.forecastFuel)) {
    state.forecastFuel = state.fuels[0];
  }
  $("#last-sync").textContent = summary.last_sync
    ? `Updated ${new Date(summary.last_sync).toLocaleString("en-GB")} · data lags ~1 day`
    : "";
  if (summary.sync_error) showBanner("Couldn't reach Octopus — showing cached data.");
  renderTiles(summary);
  renderFuelTabs();
  await Promise.all([renderHistory(), renderForecast()]);
}

function wireControls() {
  $("#period").querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.days = Number(btn.dataset.days);
      $("#period").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderHistory();
    })
  );
  $("#unit").querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.unit = btn.dataset.unit;
      $("#unit").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderHistory();
    })
  );
  $("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh");
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      if (!res.ok) showBanner("Sync failed — showing cached data.");
      else $("#banner").hidden = true;
    } finally {
      btn.disabled = false;
      btn.textContent = "Refresh";
      loadAll();
    }
  });
  // Chart colors are read from CSS at render time; re-render on theme change.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
}

wireControls();
loadAll();
