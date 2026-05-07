import * as d3 from 'd3';
import { parseExpenses } from './api.js';

function _timelineEventsFromSpending(intake) {
  const items = parseExpenses(String(intake?.spending || ''));
  return items.map((e) => ({
    year: e.years,
    label: e.label || `Spending ($${Number(e.amount).toLocaleString()})`,
    amount: e.amount,
  }));
}

const PLOTLY_COLORS = [
  "#2563eb", "#7c3aed", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#64748b", "#ec4899", "#14b8a6", "#f97316",
];

function _fmtVal(v) {
  if (v == null) return "N/A";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return "$" + v.toFixed(2);
}


// ---- D3-based inline chart rendering (with hover tooltips) ----

function _chartTooltip() {
  let tip = document.getElementById("chart-tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chart-tooltip";
    tip.style.cssText = "position:fixed;z-index:9999;padding:8px 12px;background:#1f2937;color:#fff;font-size:12px;border-radius:8px;pointer-events:none;opacity:0;transition:opacity 0.15s;max-width:280px;box-shadow:0 4px 12px rgba(0,0,0,0.3);";
    document.body.appendChild(tip);
  }
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.style.opacity = "1";
      this.move(x, y);
    },
    move(x, y) {
      const left = Math.min(x + 12, window.innerWidth - (tip.offsetWidth || 150) - 8);
      const top = Math.max(8, y - (tip.offsetHeight || 40) - 8);
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    },
    hide() {
      tip.style.opacity = "0";
    },
  };
}

function renderInlineCharts(artifacts, parentEl) {
  if (!artifacts) return;

  const allPortfolios = artifacts.all_portfolios;
  const composition = artifacts.portfolio_composition;
  const scenarios = artifacts.scenarios;
  if (!allPortfolios && !composition && !scenarios) return;

  const container = document.createElement("div");
  container.className = "inline-charts";

  // --- Quala portfolio proposals: 3 scenarios in one row (Conservative | Moderate | Aggressive) ---
  if (allPortfolios && Object.keys(allPortfolios).length) {
    const PIE_DEFS = [
      { key: "tickers", suffix: "Tickers" },
      { key: "sectors", suffix: "Asset class" },
      { key: "industries", suffix: "Sector" },
    ];
    const RET_PIE_DEFS = [
      { key: "tickers", suffix: "Retirement Tickers" },
      { key: "sectors", suffix: "Retirement asset class" },
      { key: "industries", suffix: "Retirement sector" },
    ];
    const scenariosRow = document.createElement("div");
    scenariosRow.className = "scenarios-row";
    for (const [name, data] of Object.entries(allPortfolios)) {
      const label = name.charAt(0).toUpperCase() + name.slice(1);
      const tickers = data.tickers || (data && typeof data === "object" && !Array.isArray(data) ? data : {});
      if (!tickers || typeof tickers !== "object" || !Object.keys(tickers).length) continue;
      const col = document.createElement("div");
      col.className = "scenario-column";
      // Tickers pie
      const pieCard = _makeChartCard(`${label} — Tickers`);
      const pieEl = document.createElement("div");
      pieEl.style.minHeight = "130px";
      pieEl.style.width = "100%";
      pieEl.style.minWidth = "140px";
      pieCard.querySelector(".chart-div").appendChild(pieEl);
      _renderPie(pieEl, tickers, { height: 130 });
      col.appendChild(pieCard);
      // Assets & weights table
      const tableCard = _makeChartCard(`${label} — Assets & Weights`);
      const table = document.createElement("table");
      table.className = "metrics-table";
      table.style.minWidth = "max-content";
      table.innerHTML = "<thead><tr><th>Ticker</th><th>Weight</th></tr></thead><tbody></tbody>";
      const tbody = table.querySelector("tbody");
      const sorted = Object.entries(tickers).sort((a, b) => b[1] - a[1]);
      for (const [t, w] of sorted) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${t}</td><td>${(w * 100).toFixed(1)}%</td>`;
        tbody.appendChild(tr);
      }
      tableCard.querySelector(".chart-div").appendChild(table);
      const est = data.estimates;
      if (est && typeof est === "object" && Object.keys(est).length) {
        const estDiv = document.createElement("div");
        estDiv.className = "estimates-block";
        const note = document.createElement("div");
        note.className = "estimates-note";
        note.textContent = artifacts.llm_estimates_date_range
          ? `Approximate based on data for date range: ${artifacts.llm_estimates_date_range}`
          : "Approximate based on LLM data";
        estDiv.appendChild(note);
        const labels = {
          cagr_range: "Time Weighted Return (TWR)",
          yield: "Yield",
          yield_range: "Yield",
          beta_range: "Beta",
          max_drawdown_range: "Max Drawdown",
          crash_2008: "2008 Crash",
          crash_2022: "2022 Crash",
          projected_income_at_retirement: "Projected income<br>at retirement",
        };
        for (const [k, v] of Object.entries(est)) {
          if (!v) continue;
          const row = document.createElement("div");
          row.className = "estimates-row";
          row.innerHTML = `<span class="estimates-label">${labels[k] || k}:</span><span class="estimates-value">${String(v)}</span>`;
          estDiv.appendChild(row);
        }
        tableCard.querySelector(".chart-div").appendChild(estDiv);
      }
      col.appendChild(tableCard);
      // Sectors, industries
      for (const def of PIE_DEFS) {
        if (def.key === "tickers") continue;
        const weights = data[def.key] || {};
        if (weights && Object.keys(weights).length) {
          const card = _makeChartCard(`${label} — ${def.suffix}`);
          const pieEl = document.createElement("div");
          pieEl.style.minHeight = "130px";
          pieEl.style.width = "100%";
          pieEl.style.minWidth = "140px";
          card.querySelector(".chart-div").appendChild(pieEl);
          _renderPie(pieEl, weights, { height: 130 });
          col.appendChild(card);
        }
      }
      // Retirement
      if (data.retirement && Object.keys(data.retirement).length) {
        for (const def of RET_PIE_DEFS) {
          const weights = data.retirement[def.key] || {};
          if (weights && Object.keys(weights).length) {
            const card = _makeChartCard(`${label} — ${def.suffix}`);
            const pieEl = document.createElement("div");
            pieEl.style.minHeight = "130px";
            pieEl.style.width = "100%";
            pieEl.style.minWidth = "140px";
            card.querySelector(".chart-div").appendChild(pieEl);
            _renderPie(pieEl, weights, { height: 130 });
            col.appendChild(card);
          }
        }
      }
      scenariosRow.appendChild(col);
    }
    if (scenariosRow.children.length) container.appendChild(scenariosRow);
  } else if (composition && Object.keys(composition).length && !scenarios?.length) {
    // Chosen portfolio pie (standalone) — only when no backtest results yet
    const pieCard = _makeChartCard("Chosen Portfolio");
    const pieEl = document.createElement("div");
    pieEl.style.minHeight = "170px";
    pieEl.style.width = "100%";
    pieCard.querySelector(".chart-div").appendChild(pieEl);
    _renderPie(pieEl, composition);
    container.appendChild(pieCard);
  }

  // --- Post-backtest artifacts (chosen portfolio): left panel (plots) | right panel (performance table + pie) ---
  if (scenarios && scenarios.length) {
    const assetCorr = artifacts.asset_correlations;
    const barChartCard = _renderPortfolioValueBarChart(scenarios, { defer: true });
    const tableCard = _makeChartCard("Performance Metrics");

    let dataDateRange = artifacts.data_date_range;
    if (!dataDateRange && scenarios[0]?.timeseries?.length >= 2) {
      const ts = scenarios[0].timeseries;
      const d0 = ts[0].date, d1 = ts[ts.length - 1].date;
      if (d0 && d1) {
        const fmt = (d) => {
          const x = new Date(d);
          const m = String(x.getMonth() + 1).padStart(2, "0");
          return `${m}/${x.getFullYear()}`;
        };
        dataDateRange = `${fmt(d0)} – ${fmt(d1)}`;
      }
    }
    if (dataDateRange) {
      const rangeEl = document.createElement("p");
      rangeEl.className = "data-date-range";
      rangeEl.style.cssText = "margin:0 0 6px 0;font-size:10px;color:#64748b;";
      rangeEl.textContent = `Historical data: ${dataDateRange}`;
      tableCard.querySelector(".chart-div").appendChild(rangeEl);
    }
    const horizonForLabels = artifacts.intake?.horizon_years ?? artifacts.mc_years ?? artifacts.years ?? 25;
    _renderMetricsTable(tableCard, scenarios, artifacts.years, artifacts.mc_years, horizonForLabels);

    const corrCard = assetCorr && assetCorr.rows && assetCorr.rows.length
      ? (() => {
          const c = _makeChartCard("Asset Correlations and Returns");
          _renderAssetCorrelationTable(c.querySelector(".chart-div"), assetCorr);
          return c;
        })()
      : null;

    const chartScenarios = scenarios;
    const bestScenario = chartScenarios[0];
    const ts = bestScenario?.timeseries;
    const sp = bestScenario?.summary_paths;
    const spaghettiPaths = bestScenario?.paths_sample;
    const spaghettiYears = bestScenario?.paths_sample_years;
    const hasSpaghetti = spaghettiPaths && spaghettiPaths.length && spaghettiYears && spaghettiYears.length;
    const hasGrowth = (ts && ts.length) || (sp && sp.mean && sp.mean.length);

    const panelWrapper = document.createElement("div");
    panelWrapper.className = "chosen-portfolio-panels";

    // Left panel: plots stacked vertically (defer render until after append so flex layout gives height)
    const leftPanel = document.createElement("div");
    leftPanel.className = "chart-panel-left";
    const deferredCharts = [];
    if (barChartCard) {
      const firstScenario = scenarios[0];
      const ts = firstScenario?.timeseries || [];
      const raw = _yearEndValues(ts);
      const barData = raw.years?.length ? raw.years.map((y, i) => ({ year: Number(y), value: raw.values[i] })) : [];
      const chartEl = barChartCard.querySelector(".chart-div").querySelector("div");
      if (chartEl && barData.length) {
        deferredCharts.push(() => _renderPortfolioValueBarChartInto(chartEl, barData));
      }
      leftPanel.appendChild(barChartCard);
    }
    if (hasGrowth) {
      const growthTitle = "Growth Portfolio- future projections";
      const mcSimsSub = artifacts.mc_sims != null ? `${Number(artifacts.mc_sims)} scenarios` : null;
      const comboCard = _makeChartCard(growthTitle, false, mcSimsSub);
      const chartEl = comboCard.querySelector(".chart-div");
      const hasIntake = artifacts.intake || (artifacts.mc_years && artifacts.portfolio_composition);
      deferredCharts.push(() => {
        if (hasIntake) {
          const intake = artifacts.intake || {
            horizon_years: artifacts.mc_years,
            longevity_years: (artifacts.mc_years || 25) + 30,
            initial_value: 1,
            retirement_monthly_target: 0,
            spending: '',
          };
          _renderD3TimelineChart(
            chartEl,
            chartScenarios,
            { ...artifacts, intake },
            artifacts.portfolio_composition,
            null,
          );
        } else {
          const horizon = artifacts.intake?.horizon_years ?? artifacts.mc_years ?? artifacts.years ?? 25;
          _renderCombinedChart(
            chartEl,
            chartScenarios,
            horizon,
            artifacts.frequency || "daily",
          );
        }
      });
      leftPanel.appendChild(comboCard);
    }
    if (hasSpaghetti) {
      const mcSimsSub = artifacts.mc_sims != null ? `${Number(artifacts.mc_sims)} scenarios` : null;
      const spaghettiCard = _makeChartCard("Monte Carlo scenarios", false, mcSimsSub);
      const chartEl = spaghettiCard.querySelector(".chart-div");
      deferredCharts.push(() => _renderSpaghettiPlot(chartEl, spaghettiPaths, spaghettiYears));
      leftPanel.appendChild(spaghettiCard);
    }

    // Right panel: chosen portfolio pie (top) | performance table | asset correlation
    const rightPanel = document.createElement("div");
    rightPanel.className = "chart-panel-right";
    const composition = artifacts.portfolio_composition;
    if (composition && Object.keys(composition).length) {
      const pieCard = _makeChartCard("Chosen Portfolio");
      const pieEl = document.createElement("div");
      pieEl.style.minHeight = "84px";
      pieEl.style.width = "100%";
      pieCard.querySelector(".chart-div").appendChild(pieEl);
      _renderPie(pieEl, composition, { height: 84 });
      rightPanel.appendChild(pieCard);
    }
    rightPanel.appendChild(tableCard);
    if (corrCard) rightPanel.appendChild(corrCard);

    panelWrapper.appendChild(leftPanel);
    panelWrapper.appendChild(rightPanel);
    container.appendChild(panelWrapper);

    // Defer left-panel chart render until after layout so flex gives correct height
    if (deferredCharts.length) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          deferredCharts.forEach((fn) => fn());
        });
      });
    }
  }

  if (!parentEl) return;
  parentEl.appendChild(container);
  if (parentEl.scrollTop !== undefined) parentEl.scrollTop = parentEl.scrollHeight;
}

function _makeChartCard(title, fullWidth, subtitle) {
  const card = document.createElement("div");
  card.className = "chart-card" + (fullWidth ? " full-width" : "");
  const h3 = document.createElement("h3");
  h3.textContent = title;
  card.appendChild(h3);
  if (subtitle) {
    const p = document.createElement("p");
    p.className = "chart-subtitle";
    p.style.cssText = "margin:0 0 8px 0;font-size:11px;color:#6b7280;";
    p.textContent = subtitle;
    card.appendChild(p);
  }
  const div = document.createElement("div");
  div.className = "chart-div";
  card.appendChild(div);
  return card;
}

function _renderPie(el, weightMap, opts) {
  if (typeof d3 === "undefined") return;
  const height = (opts && opts.height) ?? 170;
  const sorted = Object.entries(weightMap).sort((a, b) => Number(b[1]) - Number(a[1]));
  const total = sorted.reduce((s, [, w]) => s + Number(w), 0) || 1;

  if (!sorted.length || total <= 0) return;

  d3.select(el).selectAll("*").remove();
  const w = el.clientWidth || el.offsetWidth || 200;
  const size = Math.min(w, height) - 24;
  const radius = Math.min(size / 2, Math.min(80, height / 2 - 16));
  const innerRadius = radius * 0.4;
  const cx = w / 2;
  const cy = height / 2;

  const pie = d3.pie().value((d) => d[1] / total)(sorted);
  const arc = d3.arc().innerRadius(innerRadius).outerRadius(radius);
  const color = d3.scaleOrdinal(PLOTLY_COLORS).domain(sorted.map(([t]) => t));
  const tooltip = _chartTooltip();

  const svg = d3.select(el)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g").attr("transform", `translate(${cx},${cy})`);

  g.selectAll("path")
    .data(pie)
    .join("path")
    .attr("d", arc)
    .attr("fill", (d) => color(d.data[0]))
    .attr("stroke", "#fff")
    .attr("stroke-width", 1)
    .on("mouseenter", (event, d) => {
      const pct = ((d.data[1] / total) * 100).toFixed(1);
      tooltip.show(`${d.data[0]}: ${pct}%`, event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => tooltip.hide());

  // Arc labels: label + weight outside each slice (2 mm from pie edge)
  const padPx = 2 * 96 / 25.4;
  const labelArc = d3.arc().innerRadius(radius).outerRadius(radius);
  const labelGroup = g.append("g").attr("class", "arc-labels");
  labelGroup.selectAll("text")
    .data(pie)
    .join("text")
    .attr("transform", (d) => {
      const c = labelArc.centroid(d);
      const dist = Math.sqrt(c[0] * c[0] + c[1] * c[1]) || 1;
      const scale = (radius + padPx) / dist;
      return `translate(${c[0] * scale},${c[1] * scale})`;
    })
    .attr("text-anchor", (d) => {
      const c = labelArc.centroid(d);
      return c[0] >= 0 ? "start" : "end";
    })
    .attr("dx", (d) => {
      const c = labelArc.centroid(d);
      return c[0] >= 0 ? 2 : -2;
    })
    .attr("font-size", 12)
    .attr("fill", "#1f2937")
    .each(function (d) {
      const pct = ((d.data[1] / total) * 100).toFixed(1);
      d3.select(this).append("tspan").attr("x", 0).attr("dy", "-0.3em").text(d.data[0]);
      d3.select(this).append("tspan").attr("x", 0).attr("dy", "1.1em").text(pct + "%");
    });

  // Legend with labels and weights
  const legendY = cy + radius + 20;
  const legendData = sorted.slice(0, 8);
  legendData.forEach(([label, weight], i) => {
    const pct = ((weight / total) * 100).toFixed(1);
    const x = -w / 2 + 8 + (i % 4) * (w / 4);
    const row = Math.floor(i / 4);
    g.append("circle").attr("cx", x).attr("cy", legendY + row * 14).attr("r", 4).attr("fill", color(label));
    g.append("text").attr("x", x + 8).attr("y", legendY + row * 14).attr("dy", "0.35em").attr("font-size", 12).attr("fill", "#374151").text(`${label} ${pct}%`);
  });
}

function _renderMetricsTable(card, scenarios, years, mcYears, horizonOverride) {
  // horizonOverride = years projected to retirement (from intake when available)
  const horizon = horizonOverride ?? mcYears ?? years ?? 25;
  const terminalFormatter = (v) => {
    if (v == null) return "N/A";
    if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (Math.abs(v) >= 1e3) return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return "$" + v.toFixed(2);
  };
  const METRIC_LABELS = {
    cagr: "TWR",
    benchmark_twr: "Benchmark TWR (60/40)",
    alpha_twr: "Alpha (excess TWR)",
    annualized_volatility: "Volatility",
    sharpe_ratio: "Sharpe (annualized)",
    sortino_ratio: "Sortino (annualized)",
    max_drawdown: "Max Drawdown",
    cumulative_return: "Cumulative Return",
    beta: "Beta",
    information_ratio: "Info. Ratio",
  };
  const METRIC_DESCRIPTIONS = {
    cagr: "Time-weighted return; average yearly return over the period.",
    benchmark_twr: "Annualized time-weighted return of the 60% stocks / 40% bonds benchmark over the same span as Alpha.",
    alpha_twr: "Portfolio TWR minus benchmark TWR.",
    annualized_volatility: "Standard deviation of returns, annualized; measures fluctuation.",
    sharpe_ratio:
      "Annualized Sharpe: excess uses a 3.6% p.a. risk-free rate.",
    sortino_ratio:
      "Annualized Sortino: like Sharpe, but the denominator includes only downside volatility.",
    max_drawdown: "Largest peak-to-trough decline; worst historical loss.",
    cumulative_return: "Total return over the period (e.g. 1.5 = 150% gain).",
    beta: "Sensitivity to the benchmark; 1 = moves with a 60% stocks / 40% bonds portfolio.",
    information_ratio: "Excess return per unit of tracking error vs benchmark.",
  };
  const MC_LABELS = {
    cagr_p50: "TWR (MC P50)",
    cagr_p10: "TWR (MC P10)",
    cagr_p90: "TWR (MC P90)",
    terminal_value_p50: `Portfolio Value at Retirement (MC P50, ${horizon}yr)`,
    terminal_value_p10: `Portfolio Value at Retirement (MC P10, ${horizon}yr)`,
    terminal_value_p90: `Portfolio Value at Retirement (MC P90, ${horizon}yr)`,
    prob_loss: "Prob. of Loss",
    prob_outperform_benchmark: "Prob. Outperform Benchmark",
  };
  const MC_DESCRIPTIONS = {
    cagr_p50: "Median simulated TWR; 50% of paths had at least this return.",
    cagr_p10: "10th percentile TWR; 10% of paths had this or lower return.",
    cagr_p90: "90th percentile TWR; 90% of paths had at most this return.",
    terminal_value_p50: "Median portfolio value at retirement across simulations.",
    terminal_value_p10: "10th percentile; 10% chance of ending below this value.",
    terminal_value_p90: "90th percentile; 90% chance of ending below this value.",
    prob_loss: "Chance of ending with less than your initial investment.",
    prob_outperform_benchmark: "Chance of beating the benchmark over the horizon.",
  };

  const table = document.createElement("table");
  table.className = "metrics-table";
  table.style.minWidth = "max-content";

  const scenarioNames = scenarios.map((s) => {
    const n = s.scenario;
    const name = n.startsWith("growth_") ? n.replace("growth_", "Growth — ")
      : n.startsWith("retirement_") ? n.replace("retirement_", "Retirement — ")
      : n;
    if (name === "No Rebalancing" || name === "none") return "Metrics";
    return name;
  });
  const headerStyle = "background:#e2e8f0;font-weight:600;padding:6px 10px";
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headerRow.innerHTML =
    `<th style="${headerStyle}">Backtesting</th>` +
    scenarioNames.map((n) => `<th style="${headerStyle}">${n}</th>`).join("") +
    `<th style="${headerStyle}">What it means</th>`;
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");

  function addRow(label, getter, formatter, description) {
    const fmt = formatter || _fmtNum;
    const tr = document.createElement("tr");
    let html = `<td>${label}</td>`;
    for (const s of scenarios) {
      const v = getter(s);
      html += `<td>${fmt(v)}</td>`;
    }
    html += `<td style="font-size:12px;color:#64748b;max-width:220px">${description || ""}</td>`;
    tr.innerHTML = html;
    tbody.appendChild(tr);
  }

  const pctFormatter = (v) => (v == null ? "N/A" : (v * 100).toFixed(2) + "%");
  for (const [key, label] of Object.entries(METRIC_LABELS)) {
    const fmt =
      key === "cagr" || key === "benchmark_twr" || key === "alpha_twr" ? pctFormatter : undefined;
    addRow(label, (s) => s.metrics[key], fmt, METRIC_DESCRIPTIONS[key]);
  }

  const mcSep = document.createElement("tr");
  const mcSepStyle = "background:#e2e8f0;font-weight:600;padding:6px 10px";
  mcSep.innerHTML =
    `<td style="${mcSepStyle}">Monte Carlo</td>` +
    `<td colspan="${scenarioNames.length}" style="${mcSepStyle}">Metrics</td>` +
    `<td style="${mcSepStyle}">What it means</td>`;
  tbody.appendChild(mcSep);

  for (const [key, label] of Object.entries(MC_LABELS)) {
    const fmt = key.startsWith("cagr_p") ? pctFormatter
      : key.startsWith("terminal_value") ? terminalFormatter
      : undefined;
    addRow(label, (s) => s.monte_carlo[key], fmt, MC_DESCRIPTIONS[key]);
  }

  table.appendChild(tbody);
  card.querySelector(".chart-div").appendChild(table);
}

function _renderPortfolioValueBarChart(scenarios, opts) {
  if (typeof d3 === "undefined") return null;
  const firstScenario = scenarios[0];
  const ts = firstScenario?.timeseries || [];
  const raw = _yearEndValues(ts);
  if (!raw.years.length || !raw.values.length) return null;
  let dateRangeSub = null;
  if (ts.length >= 2 && ts[0].date != null && ts[ts.length - 1].date != null) {
    const fmt = (d) => { const x = new Date(d); return `${String(x.getMonth() + 1).padStart(2, "0")}/${x.getFullYear()}`; };
    dateRangeSub = `${fmt(ts[0].date)} – ${fmt(ts[ts.length - 1].date)}`;
  }
  const card = _makeChartCard("Growth Portfolio- historical performance", false, dateRangeSub);
  const chartEl = document.createElement("div");
  chartEl.style.height = "100%";
  chartEl.style.minHeight = "168px";
  chartEl.style.minWidth = "480px";
  card.querySelector(".chart-div").appendChild(chartEl);
  if (opts?.defer) return card;
  _renderPortfolioValueBarChartInto(chartEl, raw.years.map((y, i) => ({ year: Number(y), value: raw.values[i] })));
  return card;
}

function _renderPortfolioValueBarChartInto(chartEl, data) {
  if (typeof d3 === "undefined" || !chartEl || !data?.length) return;
  d3.select(chartEl).selectAll("*").remove();
  const w = Math.max(chartEl.clientWidth || chartEl.offsetWidth || 0, 400);
  const height = Math.max(168, chartEl.clientHeight || 168);
  const margin = { top: 28, right: 28, bottom: 48, left: 55 };
  const innerWidth = w - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const xScale = d3.scaleBand().domain(data.map((d) => d.year)).range([0, innerWidth]).padding(0.2);
  const maxVal = d3.max(data, (d) => d.value) || 1;
  const yScale = d3.scaleLinear().domain([0, maxVal * 1.05]).range([innerHeight, 0]);
  const tooltip = _chartTooltip();

  const svg = d3.select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  g.selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", (d) => xScale(d.year))
    .attr("y", (d) => yScale(d.value))
    .attr("width", xScale.bandwidth())
    .attr("height", (d) => innerHeight - yScale(d.value))
    .attr("fill", "rgba(37,99,235,0.6)")
    .on("mouseenter", (event, d) => {
      tooltip.show(`Year ${d.year}<br>Value: ${_fmtVal(d.value)}`, event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => tooltip.hide());

  g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).tickValues(xScale.domain().filter((_, i) => i % 2 === 0)));
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat((v) => (v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v)))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end");
}

function _renderAssetCorrelationTable(container, assetCorr) {
  const { tickers, rows } = assetCorr;
  if (!rows || !rows.length) return;
  const wrapper = document.createElement("div");
  wrapper.style.overflowX = "auto";
  const table = document.createElement("table");
  table.className = "metrics-table asset-correlation-table";
  table.style.fontSize = "10px";
  table.style.minWidth = "max-content";
  const thead = document.createElement("thead");
  const headerCells = ["Name", "Ticker", "Weight", ...tickers, "Expected Annual Return", "Annualized Volatility"];
  thead.innerHTML = `<tr>${headerCells.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const pctFmt = (v) => (v == null || v === undefined ? "N/A" : (v * 100).toFixed(2) + "%");
  const corrFmt = (v) => (v == null || v === undefined ? "—" : v.toFixed(2));
  const corrClass = (v) => {
    if (v == null || v === undefined) return "corr-neutral";
    if (v >= 0.6) return "corr-positive-high";
    if (v >= 0.3) return "corr-positive-medium";
    if (v > 0) return "corr-positive-low";
    if (v === 0) return "corr-neutral";
    if (v >= -0.3) return "corr-negative-low";
    if (v >= -0.6) return "corr-negative-medium";
    return "corr-negative-high";
  };
  for (const row of rows) {
    const corrCells = tickers.map((t) => {
      const v = row.correlations?.[t];
      const cls = corrClass(v);
      return `<td class="${cls}">${corrFmt(v)}</td>`;
    });
    const nameCell = `<td>${row.name || row.ticker}</td>`;
    const tickerCell = `<td>${row.ticker}</td>`;
    const weightCell = `<td>${row.weight != null ? pctFmt(row.weight) : "—"}</td>`;
    const pctCells = [
      pctFmt(row.expected_return),
      pctFmt(row.volatility),
    ].map((c) => `<td>${c}</td>`);
    tbody.appendChild(document.createElement("tr")).innerHTML =
      nameCell + tickerCell + weightCell + corrCells.join("") + pctCells.join("");
  }
  table.appendChild(tbody);
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

function _fmtNum(v) {
  if (v == null || v === undefined) return "N/A";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toFixed(4);
}

// Aggregate timeseries to year-end values (raw years from data)
function _yearEndValues(timeseries) {
  const byYear = {};
  for (const row of timeseries) {
    const d = new Date(row.date);
    const yr = d.getFullYear();
    byYear[yr] = row.portfolio_value;
  }
  const years = Object.keys(byYear).sort();
  return { years, values: years.map((y) => byYear[y]) };
}

// Map backtest to projection: current year through retirement (horizon years)
// Cap to data length to avoid jump: use value at horizon only when it exists in raw data
function _backtestToProjection(timeseries, horizon, portfolioValueAtRetirement) {
  const raw = _yearEndValues(timeseries || []);
  const startYear = new Date().getFullYear();
  if (horizon <= 0) return raw;
  const dataYears = raw.values.length;
  const effectiveHorizon = Math.min(horizon, Math.max(0, dataYears - 1));
  const years = [];
  const values = [];
  for (let i = 0; i <= effectiveHorizon; i++) {
    years.push(startYear + i);
    const rawVal = raw.values[i];
    values.push(rawVal ?? (i === 0 && raw.values.length ? raw.values[0] : null));
  }
  return { years, values };
}

// Sample Monte Carlo summary_paths at annual intervals
function _annualMC(summaryPaths, totalYears, frequency) {
  const mean = summaryPaths.mean || [];
  if (!mean.length || totalYears <= 0) return null;
  const periodsPerYear = frequency === "monthly" ? 12 : 252;
  const n = mean.length;
  const indices = [];
  for (let yr = 0; yr <= totalYears; yr++) {
    const idx = Math.min(Math.round(yr * periodsPerYear), n - 1);
    indices.push(idx);
  }
  const sample = (arr) => indices.map((i) => (arr && arr[i] != null ? arr[i] : null));
  return {
    years: indices.map((_, i) => i),
    p10: sample(summaryPaths.p10),
    p50: sample(summaryPaths.p50),
    p90: sample(summaryPaths.p90),
    mean: sample(mean),
  };
}

// Build timeline data for D3 chart: years from portfolio creation to longevity
// Retirement phase: income = yield from portfolio + principal draw to sustain target
function _buildTimelineData(scenarios, artifacts) {
  const best = scenarios[0];
  const sp = best?.summary_paths || {};
  const intake = artifacts?.intake || {};
  const horizon = intake.horizon_years ?? artifacts.mc_years ?? 25;
  const longevity = intake.longevity_years ?? horizon + 30;
  const frequency = artifacts.frequency || "monthly";
  const initialValue = intake.initial_value ?? 1;

  // Use data length to cap horizon — avoid mismatch when mc_years != intake horizon — avoid mismatch when mc_years != intake horizon
  const dataYears = sp.mean?.length ? Math.floor(sp.mean.length / (frequency === "monthly" ? 12 : 252)) : horizon;
  const effectiveHorizon = Math.min(horizon, dataYears);
  const mc = _annualMC(sp, effectiveHorizon, frequency);
  if (!mc) return null;

  const startYear = new Date().getFullYear();
  const data = [];
  let lastValue = mc.p50[0] ?? initialValue;

  // Only show accumulation phase (current year through retirement) — no synthetic retirement projection
  for (let yr = 0; yr <= effectiveHorizon; yr++) {
    const calendarYear = startYear + yr;
    let p50, p10, p90;
    if (mc.p50[yr] != null) {
      p50 = mc.p50[yr];
      p10 = mc.p10[yr];
      p90 = mc.p90[yr];
      lastValue = p50;
    } else {
      p50 = lastValue;
      p10 = lastValue * 0.9;
      p90 = lastValue * 1.1;
    }
    data.push({
      year: yr,
      calendarYear,
      p50,
      p10,
      p90,
      isRetirement: false,
      incomeDrawn: 0,
    });
  }

  return {
    data,
    startYear,
    horizon: effectiveHorizon,
    longevity: effectiveHorizon,
    events: _timelineEventsFromSpending(intake),
  };
}

function _renderD3TimelineChart(el, scenarios, artifacts, growthComposition, retirementComposition) {
  const fallbackHorizon = artifacts.intake?.horizon_years ?? artifacts.mc_years ?? 25;
  if (typeof d3 === "undefined") {
    _renderCombinedChart(el, scenarios, fallbackHorizon, artifacts.frequency || "monthly");
    return;
  }

  const timeline = _buildTimelineData(scenarios, artifacts);
  if (!timeline) {
    _renderCombinedChart(el, scenarios, fallbackHorizon, artifacts.frequency || "monthly");
    return;
  }

  const { data, startYear, horizon, longevity, events } = timeline;
  const containerWidth = el.clientWidth || el.offsetWidth || 600;
  const width = Math.min(Math.max(360, containerWidth), 540);
  const height = Math.max(252, el.clientHeight || 252);
  const margin = { top: 30, right: 40, bottom: 50, left: 55 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  d3.select(el).selectAll("*").remove();

  const svg = d3.select(el)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet")
    .style("max-width", "100%");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const xScale = d3.scaleBand()
    .domain(data.map((d) => d.calendarYear))
    .range([0, innerWidth])
    .padding(0.2);

  const maxVal = d3.max(data, (d) => d.p90) || 1;
  const yScale = d3.scaleLinear()
    .domain([0, maxVal * 1.05])
    .range([innerHeight, 0]);

  // P10-P90 confidence band
  const area = d3.area()
    .x((d) => xScale(d.calendarYear) + xScale.bandwidth() / 2)
    .y0((d) => yScale(d.p90))
    .y1((d) => yScale(d.p10))
    .curve(d3.curveMonotoneX);

  g.append("path")
    .datum(data)
    .attr("fill", "rgba(134,239,172,0.35)")
    .attr("stroke", "none")
    .attr("d", area);

  const tooltip = _chartTooltip();

  // P50 bars
  g.selectAll(".bar")
    .data(data)
    .join("rect")
    .attr("class", "bar")
    .attr("x", (d) => xScale(d.calendarYear))
    .attr("y", (d) => yScale(d.p50))
    .attr("width", xScale.bandwidth())
    .attr("height", (d) => Math.max(0, innerHeight - yScale(d.p50)))
    .attr("fill", (d) => (d.isRetirement ? "#64748b" : "#2563eb"))
    .attr("opacity", 0.8)
    .on("mouseenter", (event, d) => {
      tooltip.show(`Year ${d.calendarYear}<br>P50: ${_fmtVal(d.p50)}<br>P10-P90: ${_fmtVal(d.p10)} - ${_fmtVal(d.p90)}`, event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => tooltip.hide());

  // Retirement line
  const retX = xScale(startYear + horizon) + xScale.bandwidth() / 2;
  g.append("line")
    .attr("x1", retX)
    .attr("x2", retX)
    .attr("y1", 0)
    .attr("y2", innerHeight)
    .attr("stroke", "#dc2626")
    .attr("stroke-width", 2)
    .attr("stroke-dasharray", "4,4");

  // Portfolio value at retirement: Monte Carlo (P50) only (backtest value excluded)
  const best = scenarios[0];
  const retDataPoint = data.find((d) => d.year === horizon);
  const mcP50AtRet = retDataPoint?.p50;
  const labels = [];
  if (mcP50AtRet != null) labels.push(`MC P50: ${_fmtVal(mcP50AtRet)}`);
  if (labels.length) {
    g.append("text")
      .attr("x", retX - 6)
      .attr("y", 6)
      .attr("text-anchor", "end")
      .attr("font-size", 8)
      .attr("fill", "#374151")
      .attr("font-weight", "600")
      .text(`At retirement (${horizon}yr):`);
    labels.forEach((txt, i) => {
      g.append("text")
        .attr("x", retX - 6)
        .attr("y", 14 + i * 10)
        .attr("text-anchor", "end")
        .attr("font-size", 9)
        .attr("fill", "#64748b")
        .text(txt);
    });
  }

  // Big expenses (house, college, etc.): red dotted line + label at top (like retirement)
  events.forEach((ev) => {
    const evX = xScale(startYear + ev.year) + xScale.bandwidth() / 2;
    if (evX >= 0 && evX <= innerWidth) {
      g.append("line")
        .attr("x1", evX)
        .attr("x2", evX)
        .attr("y1", 0)
        .attr("y2", innerHeight)
        .attr("stroke", "#dc2626")
        .attr("stroke-width", 2)
        .attr("stroke-dasharray", "4,4");
      g.append("text")
        .attr("x", evX - 6)
        .attr("y", 6)
        .attr("text-anchor", "end")
        .attr("font-size", 8)
        .attr("fill", "#374151")
        .attr("font-weight", "600")
        .text(ev.label);
    }
  });

  // Income drawn labels (post-retirement)
  const incomeYears = data.filter((d) => d.incomeDrawn > 0);
  incomeYears.forEach((d, i) => {
    if (i % 3 === 0) {
      const x = xScale(d.calendarYear) + xScale.bandwidth() / 2;
      g.append("text")
        .attr("x", x)
        .attr("y", yScale(d.p50) - 6)
        .attr("text-anchor", "middle")
        .attr("font-size", 9)
        .attr("fill", "#059669")
        .text(`Income: $${(d.incomeDrawn / 1000).toFixed(0)}K`);
    }
  });

  // X axis
  g.append("g")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(xScale).tickValues(xScale.domain().filter((_, i) => i % 2 === 0)))
    .selectAll("text")
    .attr("transform", "rotate(-45)")
    .style("text-anchor", "end");

  // Y axis
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat((v) => "$" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : v.toFixed(0))))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end");

  // Titles
  g.append("text")
    .attr("x", innerWidth / 2)
    .attr("y", -12)
    .attr("text-anchor", "middle")
    .attr("font-size", 10)
    .attr("font-weight", "600")
    .text(`Portfolio Value by Year (${startYear} – ${startYear + longevity})`);

  // Retirement pie (at retirement year)
  if (retirementComposition && Object.keys(retirementComposition).length) {
    const pieSize = 35;
    const retX = xScale(startYear + horizon) + xScale.bandwidth() / 2;
    const retY = 24;
    g.append("text").attr("x", retX).attr("y", retY - 2).attr("text-anchor", "middle").attr("font-size", 8).attr("fill", "#374151").text("Retirement");
    _renderD3Pie(g, retirementComposition, retX, retY, pieSize);
  }
}

function _renderD3Pie(g, weightMap, cx, cy, size) {
  const sorted = Object.entries(weightMap).sort((a, b) => b[1] - a[1]);
  const total = sorted.reduce((s, [, w]) => s + w, 0) || 1;
  const pie = d3.pie().value((d) => d[1] / total)(sorted);
  const arc = d3.arc().innerRadius(size * 0.35).outerRadius(size * 0.5);
  const labelArc = d3.arc().innerRadius(size * 0.5).outerRadius(size * 0.5);
  const color = d3.scaleOrdinal(PLOTLY_COLORS).domain(sorted.map(([t]) => t));

  const pieG = g.append("g").attr("transform", `translate(${cx},${cy})`);
  pieG.selectAll("path")
    .data(pie)
    .join("path")
    .attr("d", arc)
    .attr("fill", (d) => color(d.data[0]))
    .attr("stroke", "#fff")
    .attr("stroke-width", 1);

  const padPx = 2 * 96 / 25.4;
  pieG.selectAll("text")
    .data(pie.filter((d) => (d.data[1] / total) >= 0.08))
    .join("text")
    .attr("transform", (d) => {
      const c = labelArc.centroid(d);
      const dist = Math.sqrt(c[0] * c[0] + c[1] * c[1]) || 1;
      const scale = (size * 0.5 + padPx) / dist;
      return `translate(${c[0] * scale},${c[1] * scale})`;
    })
    .attr("text-anchor", (d) => {
      const c = labelArc.centroid(d);
      return c[0] >= 0 ? "start" : "end";
    })
    .attr("dx", (d) => {
      const c = labelArc.centroid(d);
      return c[0] >= 0 ? 2 : -2;
    })
    .attr("font-size", 12)
    .attr("fill", "#1f2937")
    .text((d) => d.data[0] + " " + ((d.data[1] / total) * 100).toFixed(0) + "%");
}

function _renderSpaghettiPlot(el, pathsSample, yearsAxis) {
  if (typeof d3 === "undefined") return;
  d3.select(el).selectAll("*").remove();

  const w = el.clientWidth || el.offsetWidth || 400;
  const height = Math.max(252, el.clientHeight || 252);
  const margin = { top: 28, right: 28, bottom: 44, left: 55 };
  const innerWidth = w - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const xScale = d3.scaleLinear().domain([d3.min(yearsAxis), d3.max(yearsAxis)]).range([0, innerWidth]);
  const allValues = pathsSample.flat().filter((v) => v != null && v > 0);
  const maxVal = d3.max(allValues) || 1;
  const minVal = Math.max(0, d3.min(allValues) || 0);
  const yScale = d3.scaleLinear().domain([minVal, maxVal * 1.05]).range([innerHeight, 0]);
  const tooltip = _chartTooltip();

  const line = d3.line()
    .x((_, i) => xScale(yearsAxis[i]))
    .y((d) => yScale(d != null ? d : minVal))
    .defined((d) => d != null)
    .curve(d3.curveMonotoneX);

  const svg = d3.select(el)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const pathGroup = g.append("g");
  pathsSample.forEach((pathValues) => {
    pathGroup.append("path")
      .datum(pathValues)
      .attr("d", line)
      .attr("fill", "none")
      .attr("stroke", "rgba(37,99,235,0.25)")
      .attr("stroke-width", 1);
  });

  const overlay = g.append("rect").attr("width", innerWidth).attr("height", innerHeight).attr("fill", "none").attr("pointer-events", "all");
  overlay.on("mousemove", (event) => {
    const mx = d3.pointer(event, g.node())[0];
    const xVal = xScale.invert(mx);
    let idx = 0;
    let best = Infinity;
    yearsAxis.forEach((y, i) => {
      const d = Math.abs(y - xVal);
      if (d < best) { best = d; idx = i; }
    });
    const year = yearsAxis[idx];
    const vals = pathsSample.map((p) => p[idx]).filter((v) => v != null && v > 0);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const html = `Year ${year}<br>Paths: ${pathsSample.length}<br>${avg != null ? "Avg: " + _fmtVal(avg) : ""}`;
    tooltip.show(html, event.pageX, event.pageY);
  }).on("mouseleave", () => tooltip.hide());

  g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale));
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat((v) => (v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v.toFixed(0))))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end");
  g.append("text").attr("x", innerWidth / 2).attr("y", innerHeight + 36).attr("text-anchor", "middle").attr("font-size", 11).attr("fill", "#374151").text("Years");
}

function _renderCombinedChart(el, scenarios, years, frequency) {
  if (typeof d3 === "undefined") return;
  d3.select(el).selectAll("*").remove();

  const best = scenarios[0];
  const ts = best.timeseries || [];
  const sp = best.summary_paths || {};
  const startYear = new Date().getFullYear();
  const retirementYear = startYear + years;

  let agg = null;
  if (ts.length && years > 0) agg = _backtestToProjection(ts, years);
  const mc = sp.mean?.length && years > 0 ? _annualMC(sp, years, frequency) : null;

  const barData = agg ? agg.years.map((y, i) => ({ year: y, value: agg.values[i] })) : [];
  const mcData = mc ? mc.years.map((yr, i) => ({
    year: startYear + yr,
    p10: mc.p10[i],
    p50: mc.p50[i],
    p90: mc.p90[i],
    mean: mc.mean[i],
  })) : [];

  const allYears = [...new Set([...barData.map((d) => d.year), ...mcData.map((d) => d.year)])].sort((a, b) => a - b);
  if (!allYears.length) return;

  const w = el.clientWidth || el.offsetWidth || 500;
  const height = Math.max(252, el.clientHeight || 252);
  const margin = { top: 40, right: 40, bottom: 50, left: 55 };
  const innerWidth = w - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const xScale = d3.scaleBand().domain(allYears).range([0, innerWidth]).padding(0.2);
  const maxVal = d3.max([...barData.map((d) => d.value), ...mcData.flatMap((d) => [d.p90, d.p50])].filter((v) => v != null)) || 1;
  const yScale = d3.scaleLinear().domain([0, maxVal * 1.05]).range([innerHeight, 0]);
  const tooltip = _chartTooltip();

  const svg = d3.select(el)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  if (mcData.length) {
    const area = d3.area()
      .x((d) => xScale(d.year) + xScale.bandwidth() / 2)
      .y0((d) => yScale(d.p90))
      .y1((d) => yScale(d.p10))
      .curve(d3.curveMonotoneX);
    g.append("path").datum(mcData).attr("d", area).attr("fill", "rgba(134,239,172,0.35)").attr("stroke", "none");

    const line = d3.line().x((d) => xScale(d.year) + xScale.bandwidth() / 2).y((d) => yScale(d.p50)).curve(d3.curveMonotoneX);
    g.append("path").datum(mcData).attr("d", line).attr("fill", "none").attr("stroke", "#10b981").attr("stroke-width", 2.5);
    const meanLine = d3.line().x((d) => xScale(d.year) + xScale.bandwidth() / 2).y((d) => yScale(d.mean)).curve(d3.curveMonotoneX);
    g.append("path").datum(mcData).attr("d", meanLine).attr("fill", "none").attr("stroke", "#64748b").attr("stroke-width", 2).attr("stroke-dasharray", "4,4");
  }

  barData.forEach((d) => {
    g.append("rect")
      .attr("x", xScale(d.year))
      .attr("y", yScale(d.value))
      .attr("width", xScale.bandwidth())
      .attr("height", innerHeight - yScale(d.value))
      .attr("fill", "rgba(37,99,235,0.6)")
      .attr("opacity", 0.8)
      .on("mouseenter", (event) => tooltip.show(`Year ${d.year}<br>Backtest: ${_fmtVal(d.value)}`, event.pageX, event.pageY))
      .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
      .on("mouseleave", () => tooltip.hide());
  });

  if (allYears.includes(retirementYear)) {
    const retX = xScale(retirementYear) + xScale.bandwidth() / 2;
    g.append("line").attr("x1", retX).attr("x2", retX).attr("y1", 0).attr("y2", innerHeight).attr("stroke", "#dc2626").attr("stroke-width", 2).attr("stroke-dasharray", "4,4");
    const mcPoint = mcData.find((d) => d.year === retirementYear);
    if (mcPoint?.p50 != null) {
      g.append("text").attr("x", retX + 6).attr("y", 8).attr("font-size", 10).attr("fill", "#374151").text(`At retirement (${years}yr): MC P50 ${_fmtVal(mcPoint.p50)}`);
    }
  }

  const overlay = g.append("rect").attr("width", innerWidth).attr("height", innerHeight).attr("fill", "none").attr("pointer-events", "all");
  overlay.on("mousemove", (event) => {
    const mx = d3.pointer(event, g.node())[0];
    const year = allYears.find((y) => mx >= xScale(y) && mx <= xScale(y) + xScale.bandwidth());
    if (!year) return;
    const bar = barData.find((d) => d.year === year);
    const mcPt = mcData.find((d) => d.year === year);
    let html = `Year ${year}`;
    if (bar) html += `<br>Backtest: ${_fmtVal(bar.value)}`;
    if (mcPt) html += `<br>MC P50: ${_fmtVal(mcPt.p50)}<br>MC P10-P90: ${_fmtVal(mcPt.p10)} - ${_fmtVal(mcPt.p90)}`;
    tooltip.show(html, event.pageX, event.pageY);
  }).on("mouseleave", () => tooltip.hide());

  g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).tickValues(allYears.filter((_, i) => i % 2 === 0)));
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat((v) => (v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v)))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end");
  g.append("text").attr("x", innerWidth / 2).attr("y", innerHeight + 36).attr("text-anchor", "middle").attr("font-size", 11).attr("fill", "#374151").text("Year");
}

export { renderInlineCharts };
