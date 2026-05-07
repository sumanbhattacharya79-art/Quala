import * as d3 from 'd3';
import { parseExpenses, spendingFieldDeclaresOneTimeOutflows, expensesFromBigSpendingRows } from './api.js';

function _timelineEventsFromSpending(intake) {
  const raw = String(intake?.spending || '').trim();
  const fromRows = expensesFromBigSpendingRows(intake?.big_spending_rows);
  const key = (e) => `${Number(e.years)}|${Math.round(Number(e.amount))}`;
  let items = [];
  if (fromRows.length) {
    items = [...fromRows];
  }
  if (raw && spendingFieldDeclaresOneTimeOutflows(raw)) {
    const seen = new Set(items.map(key));
    for (const p of parseExpenses(raw)) {
      const k = key(p);
      if (!seen.has(k)) {
        items.push(p);
        seen.add(k);
      }
    }
  }
  if (!items.length) return [];
  const sy = Number(intake?.start_year);
  const hasStartYear = Number.isFinite(sy) && sy >= 1990 && sy <= 2100;
  return items
    // Guard against accidental matches from unrelated numbers in free-form text.
    .filter((e) => Number(e?.amount) >= 1000)
    .map((e) => {
      let y = Number(e.years);
      // parseExpenses uses calendar years (>=1000) for "100K in 2028" patterns; chart x-axis is horizon offset.
      if (hasStartYear && y >= 1000 && y <= 2100) {
        y = y - sy;
      }
      return { ...e, years: y };
    })
    .filter((e) => Number.isFinite(e.years) && e.years >= 0 && e.years <= 200)
    .map((e) => {
      const amt = Number(e.amount);
      const amtStr = amt >= 1e6 ? (amt / 1e6).toFixed(1) + ' M' : (amt / 1e3).toFixed(1) + ' K';
      const purpose = (e.label && String(e.label).trim()) || 'Spending';
      const label = `${purpose} ${amtStr}`;
      return { year: e.years, label, amount: e.amount };
    });
}

const PLOTLY_COLORS = [
  "#2563eb", "#7c3aed", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#64748b", "#ec4899", "#14b8a6", "#f97316",
];

function _chartThemeIsLight() {
  return typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light";
}

/** Axis / caption text in SVG — follows html data-theme. */
function chartTextFill() {
  return _chartThemeIsLight() ? "#1c1917" : "#ffffff";
}

/** Legend lines and heading-style SVG captions — black in light mode, white in dark. */
function chartHeadingFill() {
  return _chartThemeIsLight() ? "#000000" : "#ffffff";
}

/** Growth + retirement portfolio: one body size for SVG axes/legends and table helper cells (matches .metrics-table). */
const CHART_BODY_FONT_PX = 11;

function _chartTableHeaderCellStyle() {
  return _chartThemeIsLight()
    ? "background:#ebe6dd;color:#292524;font-weight:600;padding:6px 10px;border:1px solid #c4bdb0"
    : "background:#2a2a2a;color:#ffffff;font-weight:600;padding:6px 10px";
}

function _chartTableDescCellStyle() {
  return _chartThemeIsLight()
    ? `font-size:${CHART_BODY_FONT_PX}px;color:#44403c;max-width:220px`
    : `font-size:${CHART_BODY_FONT_PX}px;color:#ffffff;max-width:220px`;
}

function _chartTableMutedCellStyle() {
  return _chartThemeIsLight()
    ? `font-size:${CHART_BODY_FONT_PX}px;color:#57534e;max-width:220px`
    : `font-size:${CHART_BODY_FONT_PX}px;color:#ffffff;max-width:220px`;
}

function _chartRangeLineStyle() {
  return _chartThemeIsLight()
    ? `margin:0 0 6px 0;font-size:${CHART_BODY_FONT_PX}px;color:#57534e;`
    : `margin:0 0 6px 0;font-size:${CHART_BODY_FONT_PX}px;color:#ffffff;`;
}

function _chartNotePStyle() {
  return _chartThemeIsLight()
    ? `margin:0 0 8px 0;font-size:${CHART_BODY_FONT_PX}px;color:#78716c;`
    : `margin:0 0 8px 0;font-size:${CHART_BODY_FONT_PX}px;color:#ffffff;`;
}

/**
 * Pick tick values for band / ordinal x-axis so labels fit `innerWidth`.
 * @param {unknown[]} domain - scale domain (years, ages, or other labels)
 * @param {number|{innerWidth?: number, maxTicks?: number, minPxPerTick?: number}} [opts] - maxTicks if number, else options
 */
function _adaptiveXAxisTicks(domain, opts) {
  if (!domain?.length) return [];
  const o = typeof opts === "number" ? { maxTicks: opts } : (opts || {});
  const maxTicksCap = o.maxTicks ?? 16;
  const minPxPerTick = o.minPxPerTick ?? 40;
  const innerWidth = o.innerWidth;
  let maxTicks = maxTicksCap;
  if (innerWidth != null && innerWidth > 0 && Number.isFinite(innerWidth)) {
    maxTicks = Math.max(2, Math.min(maxTicksCap, Math.floor(innerWidth / minPxPerTick)));
  }

  const vals = domain.map((d) => Number(d)).filter((n) => Number.isFinite(n));
  if (!vals.length) {
    if (domain.length <= maxTicks) return [...domain];
    const step = Math.ceil(domain.length / maxTicks);
    const out = [];
    for (let i = 0; i < domain.length; i += step) out.push(domain[i]);
    if (out[out.length - 1] !== domain[domain.length - 1]) out.push(domain[domain.length - 1]);
    return out;
  }

  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min;
  if (range <= 0) return [domain.find((d) => Number(d) === min) ?? domain[0]];

  const denom = Math.max(1, maxTicks - 1);
  let step = range / denom;
  const magnitude = Math.pow(10, Math.floor(Math.log10(step))) || 1;
  const norm = step / magnitude;
  let niceStep = magnitude;
  if (norm <= 1.5) niceStep = magnitude;
  else if (norm <= 3) niceStep = 2 * magnitude;
  else if (norm <= 7) niceStep = 5 * magnitude;
  else niceStep = 10 * magnitude;

  const tickNums = [];
  for (let v = min; v < max - 1e-9; v += niceStep) tickNums.push(Math.round(v));
  tickNums.push(max);
  const seen = new Set();
  const inDomain = (n) => domain.some((d) => Number(d) === n);
  let picked = tickNums.filter((n) => {
    if (seen.has(n)) return false;
    if (!inDomain(n)) return false;
    seen.add(n);
    return true;
  }).map((n) => domain.find((d) => Number(d) === n) ?? String(n));

  while (picked.length > maxTicks && picked.length > 2) {
    const thinStep = Math.ceil(picked.length / maxTicks);
    const thin = [];
    for (let i = 0; i < picked.length; i += thinStep) thin.push(picked[i]);
    if (thin[thin.length - 1] !== picked[picked.length - 1]) thin.push(picked[picked.length - 1]);
    picked = thin;
  }
  if (picked.length) return picked;
  return [domain[0], domain[domain.length - 1]].filter((x, i, a) => a.indexOf(x) === i);
}

/** Style bottom band-axis labels: rotate when ticks would crowd horizontally. */
function _styleBandAxisXLabels(tickSelection, innerWidth, tickCount) {
  const n = Math.max(1, tickCount);
  const estW = innerWidth / n;
  const minPx = 38;
  const crowded = estW < minPx || innerWidth < 280;
  tickSelection
    .attr("transform", crowded ? "rotate(-42)" : null)
    .attr("dx", crowded ? "-0.15em" : -9)
    .attr("dy", crowded ? "0.55em" : null)
    .attr("text-anchor", "end");
}

/**
 * Pick the first usable planning horizon in years. `??` is not enough: `0` is
 * a valid JS value but unusable for MC/timeline (effectiveHorizon 0 → blank chart).
 */
function _firstPositiveYears(...candidates) {
  const fallback = 25;
  for (const c of candidates) {
    const n = Number(c);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return fallback;
}

// Portfolio values: >= 1e6 use M, < 1e6 use K (everywhere in charts/tables)
function _fmtVal(v, _displayUnit) {
  if (v == null) return "N/A";
  if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (Math.abs(v) >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
  return "$" + v.toFixed(2);
}

/** One-time expense labels above bars: compact $ / K / M (avoids $0.0M for smaller amounts). */
function _fmtOneTimeExpenseLabel(amount) {
  if (amount == null || !Number.isFinite(Number(amount))) return "";
  const v = Number(amount);
  if (v < 1) return "";
  if (v >= 1e6) {
    const m = v / 1e6;
    return `$${m >= 10 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  if (v >= 1e3) {
    const k = v / 1e3;
    return `$${k >= 100 ? k.toFixed(0) : k.toFixed(1)}K`;
  }
  return `$${Math.round(v)}`;
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

/** Resolve tooltip ticker list; backend keys may differ slightly from chart labels. */
function _tickersForBarLabel(tickersByLabel, label) {
  if (!tickersByLabel || typeof tickersByLabel !== "object" || Array.isArray(tickersByLabel)) return null;
  if (Object.prototype.hasOwnProperty.call(tickersByLabel, label) && tickersByLabel[label]) {
    return tickersByLabel[label];
  }
  const lk = String(label).trim().toLowerCase();
  for (const k of Object.keys(tickersByLabel)) {
    if (String(k).trim().toLowerCase() === lk) return tickersByLabel[k];
  }
  return null;
}

/** Tooltip line for bar charts: optional map label -> ticker symbols (from backend rollups). */
function _holdingsTooltipHtml(label, pct, tickersByLabel) {
  const base = `${label}: ${pct.toFixed(1)}%`;
  const list = _tickersForBarLabel(tickersByLabel, label);
  const arr = Array.isArray(list) ? list : null;
  if (!arr || !arr.length) return base;
  const maxShow = 28;
  const shown = arr.slice(0, maxShow);
  let tail = shown.join(", ");
  if (arr.length > maxShow) tail += ` (+${arr.length - maxShow} more)`;
  return `${base}<br/><span style="opacity:0.9;font-size:11px;line-height:1.35;display:block;margin-top:4px">Tickers: ${tail}</span>`;
}

/** Append a mount node directly after the correlation card (or after the metrics table if no correlation). */
function _appendAfterCorrelationSlot(panelWrapper, options) {
  if (typeof options?.afterCorrelationMount !== "function") return;
  const slot = document.createElement("div");
  slot.className = "charts-after-correlation-slot";
  panelWrapper.appendChild(slot);
  options.afterCorrelationMount(slot);
}

function renderInlineCharts(artifacts, parentEl, options = {}) {
  if (!artifacts) return;

  const layoutFullWidth = parentEl?.classList?.contains?.("charts-mount--full") ?? false;

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
      pieEl.style.minHeight = "200px";
      pieEl.style.width = "100%";
      pieEl.style.minWidth = "140px";
      pieCard.querySelector(".chart-div").appendChild(pieEl);
      _renderWeightBarChart(pieEl, tickers, { height: 200 });
      col.appendChild(pieCard);
      // Assets & weights table
      const tableCard = _makeChartCard(`${label} — Assets & Weights`);
      const table = document.createElement("table");
      table.className = "metrics-table scenario-assets-table";
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
      // Asset class, sector
      for (const def of PIE_DEFS) {
        if (def.key === "tickers") continue;
        const weights = data[def.key] || {};
        if (weights && Object.keys(weights).length) {
          const tickMap =
            def.key === "sectors" ? data.sectors_tickers : def.key === "industries" ? data.industries_tickers : null;
          const card = _makeChartCard(`${label} — ${def.suffix}`);
          const pieEl = document.createElement("div");
          pieEl.style.minHeight = "200px";
          pieEl.style.width = "100%";
          pieEl.style.minWidth = "140px";
          card.querySelector(".chart-div").appendChild(pieEl);
          _renderWeightBarChart(pieEl, weights, { height: 200, tickersByLabel: tickMap });
          col.appendChild(card);
        }
      }
      // Retirement
      if (data.retirement && Object.keys(data.retirement).length) {
        const ret = data.retirement;
        for (const def of RET_PIE_DEFS) {
          const weights = ret[def.key] || {};
          if (weights && Object.keys(weights).length) {
            const tickMap =
              def.key === "sectors" ? ret.sectors_tickers : def.key === "industries" ? ret.industries_tickers : null;
            const card = _makeChartCard(`${label} — ${def.suffix}`);
            const pieEl = document.createElement("div");
            pieEl.style.minHeight = "200px";
            pieEl.style.width = "100%";
            pieEl.style.minWidth = "140px";
            card.querySelector(".chart-div").appendChild(pieEl);
            _renderWeightBarChart(pieEl, weights, { height: 200, tickersByLabel: tickMap });
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
    _renderWeightBarChart(pieEl, composition);
    container.appendChild(pieCard);
  }

  // --- Post-backtest artifacts (chosen portfolio): left panel (plots) | right panel (performance table + pie) ---
  if (scenarios && scenarios.length) {
    const isRetirement = artifacts.is_retirement === true;
    const assetCorr = artifacts.asset_correlations;
    const displayUnit = artifacts.intake?.display_unit ?? null;
    const horizonForLabels = _firstPositiveYears(
      artifacts.intake?.horizon_years,
      artifacts.mc_years,
      artifacts.years,
    );

    let barChartCard = null;
    let tableCard = _makeChartCard(isRetirement ? "Retirement Monte Carlo Results" : "Performance Metrics");

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
      rangeEl.style.cssText = _chartRangeLineStyle();
      rangeEl.textContent = `Historical data: ${dataDateRange}`;
      tableCard.querySelector(".chart-div").appendChild(rangeEl);
    }
    if (isRetirement) {
      _renderRetirementMetricsTable(tableCard, scenarios[0], artifacts.mc_years, displayUnit);
    } else {
      _renderMetricsTable(tableCard, scenarios, artifacts.years, artifacts.mc_years, horizonForLabels, displayUnit);
    }

    const corrCard =
      assetCorr && assetCorr.rows && assetCorr.rows.length
        ? (() => {
            const c = _makeChartCard("Asset Correlations and Returns");
            _renderAssetCorrelationTable(c.querySelector(".chart-div"), assetCorr, isRetirement);
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
    const hasGrowth = !isRetirement && ((ts && ts.length) || (sp && sp.mean && sp.mean.length));

    const panelWrapper = document.createElement("div");
    panelWrapper.className = "chosen-portfolio-panels";

    // 2x2 grid: row1 = Backtesting | Growth MC; row2 = Spaghetti | Chosen Portfolio pie
    const chartsGrid = document.createElement("div");
    chartsGrid.className = "chosen-portfolio-charts-grid";
    const deferredCharts = [];
    const composition = artifacts.portfolio_composition || artifacts.retirement_composition;

    if (!isRetirement) {
      barChartCard = _renderPortfolioValueBarChart(scenarios, { defer: true, layoutFullWidth });
      if (barChartCard) {
        const firstScenario = scenarios[0];
        const ts = firstScenario?.timeseries || [];
        const raw = _yearEndValues(ts);
        const barData = raw.years?.length ? raw.years.map((y, i) => ({ year: Number(y), value: raw.values[i] })) : [];
        const chartEl = barChartCard.querySelector(".chart-div").querySelector("div");
        if (chartEl && barData.length) {
          deferredCharts.push(() => _renderPortfolioValueBarChartInto(chartEl, barData, displayUnit));
        }
        chartsGrid.appendChild(barChartCard);
      }
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
            horizon_years: _firstPositiveYears(artifacts.mc_years, 25),
            longevity_years: _firstPositiveYears(artifacts.mc_years, 25) + 30,
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
          const horizon = _firstPositiveYears(
            artifacts.intake?.horizon_years,
            artifacts.mc_years,
            artifacts.years,
          );
          const du = artifacts.intake?.display_unit ?? null;
          _renderCombinedChart(
            chartEl,
            chartScenarios,
            horizon,
            artifacts.frequency || "monthly",
            du,
          );
        }
      });
      chartsGrid.appendChild(comboCard);
    }
    if (hasSpaghetti && !isRetirement) {
      const mcSimsSub = artifacts.mc_sims != null ? `${Number(artifacts.mc_sims)} scenarios` : null;
      const spaghettiCard = _makeChartCard(
        "Monte Carlo scenarios",
        false,
        _spaghettiPlotSubtitle(mcSimsSub),
      );
      const chartEl = spaghettiCard.querySelector(".chart-div");
      deferredCharts.push(() => _renderSpaghettiPlot(chartEl, spaghettiPaths, spaghettiYears, displayUnit));
      chartsGrid.appendChild(spaghettiCard);
    }

    if (composition && Object.keys(composition).length && !isRetirement) {
      const pieCard = _makeChartCard("Chosen Portfolio");
      const pieEl = document.createElement("div");
      pieEl.style.minHeight = "240px";
      pieEl.style.height = "100%";
      pieEl.style.width = "100%";
      pieEl.className = "chosen-portfolio-pie-container";
      pieCard.querySelector(".chart-div").appendChild(pieEl);
      deferredCharts.push(() => _renderWeightBarChart(pieEl, composition));
      chartsGrid.appendChild(pieCard);
    }

    const sectorWeights = artifacts.portfolio_sectors;
    const industryWeights = artifacts.portfolio_industries;
    const sectorTickersMap = artifacts.portfolio_sectors_tickers;
    const industryTickersMap = artifacts.portfolio_industries_tickers;
    if (!isRetirement) {
      if (sectorWeights && Object.keys(sectorWeights).length) {
        const secCard = _makeChartCard("Asset class");
        const secEl = document.createElement("div");
        secEl.style.minHeight = "200px";
        secEl.style.width = "100%";
        secEl.className = "chosen-portfolio-pie-container";
        secCard.querySelector(".chart-div").appendChild(secEl);
        deferredCharts.push(() =>
          _renderWeightBarChart(secEl, sectorWeights, { tickersByLabel: sectorTickersMap }),
        );
        chartsGrid.appendChild(secCard);
      }
      if (industryWeights && Object.keys(industryWeights).length) {
        const indCard = _makeChartCard("Sector");
        const indEl = document.createElement("div");
        indEl.style.minHeight = "200px";
        indEl.style.width = "100%";
        indEl.className = "chosen-portfolio-pie-container";
        indCard.querySelector(".chart-div").appendChild(indEl);
        deferredCharts.push(() =>
          _renderWeightBarChart(indEl, industryWeights, { tickersByLabel: industryTickersMap }),
        );
        chartsGrid.appendChild(indCard);
      }
    }

    if (isRetirement) {
      const tickersSpaghettiRow = document.createElement("div");
      tickersSpaghettiRow.className = "retirement-charts-row-1";
      tickersSpaghettiRow.style.display = "grid";
      tickersSpaghettiRow.style.gridTemplateColumns = "1fr 1fr";
      tickersSpaghettiRow.style.gap = "16px";
      const tickersCard = composition && Object.keys(composition).length
        ? (() => {
            const c = _makeChartCard("Retirement Portfolio — Tickers & Weights");
            const el = document.createElement("div");
            el.style.minHeight = "240px";
            el.className = "chosen-portfolio-pie-container";
            c.querySelector(".chart-div").appendChild(el);
            deferredCharts.push(() => _renderWeightBarChart(el, composition));
            return c;
          })()
        : null;
      const spaghettiCardRet = hasSpaghetti
        ? (() => {
            const mcLine =
              artifacts.mc_sims != null
                ? `${Number(artifacts.mc_sims).toLocaleString()} simulations`
                : null;
            const c = _makeChartCard("Monte Carlo scenarios", false, _spaghettiPlotSubtitle(mcLine));
            const el = c.querySelector(".chart-div");
            deferredCharts.push(() => _renderSpaghettiPlot(el, spaghettiPaths, spaghettiYears, displayUnit));
            return c;
          })()
        : null;
      if (tickersCard) tickersSpaghettiRow.appendChild(tickersCard);
      if (spaghettiCardRet) tickersSpaghettiRow.appendChild(spaghettiCardRet);
      panelWrapper.appendChild(tickersSpaghettiRow);

      if (
        (sectorWeights && Object.keys(sectorWeights).length) ||
        (industryWeights && Object.keys(industryWeights).length)
      ) {
        const siRow = document.createElement("div");
        siRow.className = "retirement-sector-industry-row";
        siRow.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:16px;width:100%;margin-top:16px;";
        if (sectorWeights && Object.keys(sectorWeights).length) {
          const c = _makeChartCard("Asset class");
          const el = document.createElement("div");
          el.style.minHeight = "200px";
          el.style.width = "100%";
          el.className = "chosen-portfolio-pie-container";
          c.querySelector(".chart-div").appendChild(el);
          deferredCharts.push(() =>
            _renderWeightBarChart(el, sectorWeights, { tickersByLabel: sectorTickersMap }),
          );
          siRow.appendChild(c);
        }
        if (industryWeights && Object.keys(industryWeights).length) {
          const c = _makeChartCard("Sector");
          const el = document.createElement("div");
          el.style.minHeight = "200px";
          el.style.width = "100%";
          el.className = "chosen-portfolio-pie-container";
          c.querySelector(".chart-div").appendChild(el);
          deferredCharts.push(() =>
            _renderWeightBarChart(el, industryWeights, { tickersByLabel: industryTickersMap }),
          );
          siRow.appendChild(c);
        }
        panelWrapper.appendChild(siRow);
      }

      // Tables: each on its own row
      panelWrapper.appendChild(tableCard);
      if (corrCard) panelWrapper.appendChild(corrCard);
      _appendAfterCorrelationSlot(panelWrapper, options);
    } else {
      // Growth: charts 2 per row, then tables each on own row
      panelWrapper.appendChild(chartsGrid);
      panelWrapper.appendChild(tableCard);
      if (corrCard) panelWrapper.appendChild(corrCard);
      _appendAfterCorrelationSlot(panelWrapper, options);
    }

    if (isRetirement && artifacts.retirement_yearly_table?.length && composition && Object.keys(composition).length) {
      const retirementAge = artifacts.retirement_age ?? 65;
      const chartsRow2 = document.createElement("div");
      chartsRow2.className = "charts-row";
      chartsRow2.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:16px;width:100%;";

      const portfolioCard = _makeChartCard("Portfolio projections vs age");
      const portfolioEl = document.createElement("div");
      portfolioEl.style.minHeight = "280px";
      portfolioEl.style.width = "100%";
      portfolioEl.className = "retirement-portfolio-chart";
      portfolioCard.querySelector(".chart-div").appendChild(portfolioEl);
      chartsRow2.appendChild(portfolioCard);

      const flowsCard = _makeChartCard("Cash flow projections (P50): growth, outflow, net — click legend to isolate");
      const flowsEl = document.createElement("div");
      flowsEl.style.minHeight = "280px";
      flowsEl.style.width = "100%";
      flowsEl.className = "retirement-flows-chart";
      flowsCard.querySelector(".chart-div").appendChild(flowsEl);
      chartsRow2.appendChild(flowsCard);

      deferredCharts.push(() => {
        _renderRetirementPortfolioValueChart(
          portfolioEl,
          artifacts.retirement_yearly_table,
          retirementAge,
          displayUnit,
          artifacts.one_time_lump_by_table_year,
        );
        _renderRetirementCashFlowsChart(flowsEl, artifacts.retirement_yearly_table, retirementAge, displayUnit);
      });
      // Insert charts row 2 after tickers/spaghetti row, before tables
      panelWrapper.insertBefore(chartsRow2, tableCard);
    }

    const backtestSummaryHeader = document.createElement("div");
    backtestSummaryHeader.className = "backtest-result-header";
    if (isRetirement) {
      const mc = bestScenario?.monte_carlo || {};
      const pos = mc.probability_of_success;
      const endAge = mc.max_age_assumed;
      let pctStr = "N/A";
      if (pos != null && Number.isFinite(Number(pos))) {
        pctStr = `${(Number(pos) * 100).toFixed(2)}%`;
      }
      let ageStr = "—";
      if (endAge != null && Number.isFinite(Number(endAge))) {
        ageStr = String(Math.round(Number(endAge)));
      }
      backtestSummaryHeader.textContent = `${pctStr} that your portfolio will last till age ${ageStr}`;
    } else {
      const tv50 = bestScenario?.monte_carlo?.terminal_value_p50;
      backtestSummaryHeader.textContent = `Median Portfolio value at retirement: ${_fmtVal(tv50, displayUnit)}`;
    }
    panelWrapper.prepend(backtestSummaryHeader);

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

function _spaghettiPlotSubtitle(mcSimsLine) {
  const hint =
    "Click a path to show it alone (axes zoom). Double-click chart to restore all paths.";
  const parts = [mcSimsLine, hint].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
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
    p.style.cssText = _chartNotePStyle();
    p.textContent = subtitle;
    card.appendChild(p);
  }
  const div = document.createElement("div");
  div.className = "chart-div";
  card.appendChild(div);
  return card;
}

// Horizontal bar chart: X = weight (%), Y = segment labels (tickers, asset classes, sectors, etc.)
function _renderWeightBarChart(el, weightMap, opts) {
  if (typeof d3 === "undefined") return;
  let height = (opts && opts.height) ?? (el.clientHeight || el.offsetHeight) ?? 170;
  const sorted = Object.entries(weightMap).sort((a, b) => Number(b[1]) - Number(a[1]));
  const total = sorted.reduce((s, [, w]) => s + Number(w), 0) || 1;

  if (!sorted.length || total <= 0) return;

  const data = sorted.map(([label, w]) => ({
    label,
    weight: Number(w),
    pct: (Number(w) / total) * 100,
  }));

  d3.select(el).selectAll("*").remove();
  const w = el.clientWidth || el.offsetWidth || 200;
  const axisFontSize = CHART_BODY_FONT_PX;
  const hasLongLabels = data.some((d) => d.label.length > 10 && d.label.includes(" "));
  const margin = { top: 8, right: 16, bottom: 32, left: hasLongLabels ? 88 : 72 };
  const minRowHeight = hasLongLabels ? 34 : 22;
  const innerHeightNeeded = data.length * minRowHeight;
  const heightNeeded = margin.top + margin.bottom + innerHeightNeeded;
  height = Math.max(height, heightNeeded);
  const innerWidth = Math.max(0, w - margin.left - margin.right);
  const innerHeight = Math.max(0, height - margin.top - margin.bottom);

  const yScale = d3.scaleBand()
    .domain(data.map((d) => d.label))
    .range([0, innerHeight])
    .padding(0.25);
  const maxPct = data.length ? Math.max(...data.map((d) => d.pct)) : 0;
  const xMax = Math.min(100, maxPct + 5);
  const xScale = d3.scaleLinear()
    .domain([0, xMax])
    .range([0, innerWidth])
    .clamp(true);

  const color = d3.scaleOrdinal(PLOTLY_COLORS).domain(data.map((d) => d.label));
  const tooltip = _chartTooltip();
  const tickersByLabel = opts && opts.tickersByLabel;

  const svg = d3.select(el)
    .append("svg")
    .attr("width", w)
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet")
    .style("display", "block");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  g.selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", 0)
    .attr("y", (d) => yScale(d.label))
    .attr("width", (d) => xScale(d.pct))
    .attr("height", yScale.bandwidth())
    .attr("fill", (d) => color(d.label))
    .attr("rx", 2)
    .attr("ry", 2)
    .on("mouseenter", (event, d) => {
      const tip = document.getElementById("chart-tooltip");
      if (tip && tickersByLabel) tip.style.maxWidth = "min(420px, 92vw)";
      tooltip.show(_holdingsTooltipHtml(d.label, d.pct, tickersByLabel), event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => {
      const tip = document.getElementById("chart-tooltip");
      if (tip) tip.style.maxWidth = "280px";
      tooltip.hide();
    });

  const pctTicks = Math.max(2, Math.min(6, Math.floor(innerWidth / 52)));
  g.append("g")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(xScale).ticks(pctTicks).tickFormat((v) => v + "%"))
    .selectAll(".tick text")
    .attr("fill", chartTextFill())
    .attr("font-size", axisFontSize);
  const yAxisG = g.append("g").call(d3.axisLeft(yScale).tickSize(0));
  yAxisG.selectAll(".tick text")
    .attr("x", -6)
    .attr("text-anchor", "end")
    .attr("fill", chartTextFill())
    .attr("font-size", axisFontSize)
    .each(function () {
      const el = d3.select(this);
      const label = el.text();
      if (label.length > 10 && label.includes(" ")) {
        const idx = label.indexOf(" ");
        const part1 = label.slice(0, idx);
        const part2 = label.slice(idx + 1);
        const tc = chartTextFill();
        el.text(null);
        el.append("tspan").attr("x", -6).attr("dy", 0).attr("text-anchor", "end").attr("fill", tc).text(part1);
        el.append("tspan").attr("x", -6).attr("dy", "1.1em").attr("text-anchor", "end").attr("fill", tc).text(part2);
      }
    });
}

function _renderRetirementMetricsTable(card, scenario, mcYears, displayUnit) {
  const mc = scenario?.monte_carlo || {};
  const terminalFormatter = (v) => {
    if (v == null) return "N/A";
    if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (Math.abs(v) >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
    return "$" + (v != null ? Number(v).toFixed(2) : "0");
  };
  const pctFmt = (v) => (v == null ? "N/A" : (Number(v) * 100).toFixed(2) + "%");
  /** Age 100 here is the planning horizon cap (no depletion within plan), not a real failure age. */
  const fmtPlanFailureAge = (a) => {
    if (a == null || !Number.isFinite(Number(a))) return "N/A";
    return Math.round(Number(a)) === 100 ? "-" : "age " + Math.round(Number(a));
  };

  const table = document.createElement("table");
  table.className = "metrics-table";
  table.style.minWidth = "max-content";
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>Metric</th><th>Value</th><th>What it means</th></tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");

  const magP50 = mc.magnitude_of_failure_p50 != null ? terminalFormatter(mc.magnitude_of_failure_p50) : "N/A (no failures)";
  const magP90 = mc.magnitude_of_failure_p90 != null ? terminalFormatter(mc.magnitude_of_failure_p90) : "N/A (no failures)";
  const maxAgeSuffix = mc.max_age_assumed != null ? ` (through age ${mc.max_age_assumed})` : "";
  const rows = [
    [`Probability of success (confidence score)${maxAgeSuffix}`, pctFmt(mc.probability_of_success), "Share of simulated paths where your portfolio lasted through retirement without running out."],
    ["Magnitude of failure (P50 / P90)", `${magP50} / ${magP90}`, "When the plan fails, how much in planned withdrawals you would miss — median and worst-case (90th percentile)."],
    ["Goal completion (P10 / P50 / P90)", [mc.goal_completion_p10, mc.goal_completion_p50, mc.goal_completion_p90].map(pctFmt).join(" / "), "How much of your target income each path achieved — pessimistic, median, and optimistic."],
    ["Age of plan failure (P10 / P50 / P90)", mc.age_at_depletion_p10 != null ? [mc.age_at_depletion_p10, mc.age_at_depletion_p50, mc.age_at_depletion_p90].map((a) => fmtPlanFailureAge(a)).join(" / ") : "N/A (retirement age not set)", "When the portfolio runs out in failed paths — earlier ages mean worse outcomes. (— means no failure within the modeled horizon.)"],
    ["TWR (P10 / P50 / P90)", [mc.twr_p10, mc.twr_p50, mc.twr_p90].map(pctFmt).join(" / "), "Time-weighted return across paths; measures investment performance excluding cash flows."],
    ["Annualized average yield", pctFmt(mc.portfolio_yield_mean_annual), "Average dividend/income yield from the portfolio over the simulation period."],
    ["Annualized capital growth rate", pctFmt(mc.portfolio_log_return_mean_annual), "Average price appreciation (excluding dividends) over the simulation period."],
  ];
  rows.forEach(([label, val, explanation]) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${label}</td><td>${val}</td><td style="${_chartTableMutedCellStyle()}">${explanation}</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  card.querySelector(".chart-div").appendChild(table);
}

function _renderWithdrawalRatesByYear(container, ratesByYear, retirementAge) {
  if (!container || !ratesByYear?.length) return;
  const pctFmt = (v) => (v == null || isNaN(v) ? "N/A" : (Number(v) * 100).toFixed(2) + "%");
  const dollarFmt = (v) => {
    if (v == null || isNaN(v)) return "N/A";
    const n = Number(v);
    if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (Math.abs(n) >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
    return "$" + n.toFixed(0);
  };
  const safeDollar = (r, key) => {
    const direct = r["safe_withdrawal_" + key];
    if (direct != null && !isNaN(direct)) return direct;
    const rate = r[key];
    const port = r["portfolio_" + key];
    if (rate != null && port != null && !isNaN(rate) && !isNaN(port)) return rate * port;
    return null;
  };
  const joinSafe = (r) => [safeDollar(r, "p10"), safeDollar(r, "p50"), safeDollar(r, "p90")].map(dollarFmt).join(" / ");
  const endPortfolio = (r, key) => {
    const k = "portfolio_end_after_year_" + key;
    const v = r[k];
    return v != null && !isNaN(v) ? v : null;
  };
  const joinEndPortfolio = (r) =>
    [endPortfolio(r, "p10"), endPortfolio(r, "p50"), endPortfolio(r, "p90")].map(dollarFmt).join(" / ");
  const retAge = retirementAge ?? 65;
  // Show years 0, 5, 10, ... and last 3 years
  const maxYear = ratesByYear.length - 1;
  const sampleYears = new Set();
  for (let y = 0; y <= maxYear; y += 5) sampleYears.add(y);
  if (maxYear > 0) {
    sampleYears.add(maxYear);
    if (maxYear >= 2) sampleYears.add(maxYear - 1);
    if (maxYear >= 3) sampleYears.add(maxYear - 2);
  }
  const sorted = [...sampleYears].sort((a, b) => a - b);
  const table = document.createElement("table");
  table.className = "metrics-table withdrawal-rates-table";
  table.style.fontSize = `${CHART_BODY_FONT_PX}px`;
  table.innerHTML =
    "<thead><tr><th>Age</th><th>Withdrawal rate (P10/P50/P90)</th><th>Safe withdrawal $ (P10/P50/P90)</th><th>Portfolio end of year (P10/P50/P90)</th></tr></thead><tbody></tbody>";
  const tbody = table.querySelector("tbody");
  sorted.forEach((y) => {
    const r = ratesByYear[y];
    if (!r) return;
    const age = retAge + y;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${age}</td>` +
      `<td>${[r.p10, r.p50, r.p90].map(pctFmt).join(" / ")}</td>` +
      `<td>${joinSafe(r)}</td>` +
      `<td>${joinEndPortfolio(r)}</td>`;
    tbody.appendChild(tr);
  });
  container.appendChild(table);
}

function _renderMetricsTable(card, scenarios, years, mcYears, horizonOverride, displayUnit) {
  // horizonOverride = years projected to retirement (from intake when available)
  const horizon = horizonOverride ?? mcYears ?? years ?? 25;
  const terminalFormatter = (v) => {
    if (v == null) return "N/A";
    if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (Math.abs(v) >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
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
  // MC terminal values and chart summary_paths include inflows/outflows
  const MC_LABELS = {
    cagr_p50: "Annualized TWR — MC P50 (%)",
    cagr_p10: "Annualized TWR — MC P10 (%)",
    cagr_p90: "Annualized TWR — MC P90 (%)",
    terminal_value_p50: `Ending portfolio value ($) — MC P50, ${horizon}yr horizon`,
    terminal_value_p10: `Ending portfolio value ($) — MC P10, ${horizon}yr horizon`,
    terminal_value_p90: `Ending portfolio value ($) — MC P90, ${horizon}yr horizon`,
    prob_loss: "Prob. of Loss",
    prob_outperform_benchmark: "Prob. Outperform Benchmark",
  };
  const MC_DESCRIPTIONS = {
    cagr_p50: "Median simulated TWR; 50% of paths had at least this return.",
    cagr_p10: "10th percentile TWR; 10% of paths had this or lower return.",
    cagr_p90: "90th percentile TWR; 90% of paths had at most this return.",
    terminal_value_p50: "Median portfolio value at retirement (with inflows/outflows).",
    terminal_value_p10: "10th percentile; 10% chance of ending below this value (with inflows/outflows).",
    terminal_value_p90: "90th percentile; 90% chance of ending below this value (with inflows/outflows).",
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
  const headerStyle = _chartTableHeaderCellStyle();
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
    html += `<td style="${_chartTableDescCellStyle()}">${description || ""}</td>`;
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
  const mcSepStyle = _chartTableHeaderCellStyle();
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
    const fmt = (d) => {
      const x = new Date(d);
      return `${String(x.getMonth() + 1).padStart(2, "0")}/${x.getFullYear()}`;
    };
    dateRangeSub = `${fmt(ts[0].date)} – ${fmt(ts[ts.length - 1].date)}`;
  }
  const card = _makeChartCard("Growth Portfolio- historical performance", false, dateRangeSub);
  const chartEl = document.createElement("div");
  chartEl.style.height = "100%";
  chartEl.style.minHeight = "240px";
  chartEl.style.minWidth = opts?.layoutFullWidth ? "0" : "480px";
  chartEl.className = "backtest-chart-container";
  card.querySelector(".chart-div").appendChild(chartEl);
  if (opts?.defer) return card;
  _renderPortfolioValueBarChartInto(chartEl, raw.years.map((y, i) => ({ year: Number(y), value: raw.values[i] })));
  return card;
}

function _renderPortfolioValueBarChartInto(chartEl, data, displayUnit) {
  if (typeof d3 === "undefined" || !chartEl || !data?.length) return;
  d3.select(chartEl).selectAll("*").remove();
  const w = Math.max(chartEl.clientWidth || chartEl.offsetWidth || 0, 400);
  const height = Math.max(240, chartEl.clientHeight || 240);
  const margin = { top: 28, right: 28, bottom: 58, left: 55 };
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
      tooltip.show(`Year ${d.year}<br>Value: ${_fmtVal(d.value, displayUnit)}`, event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => tooltip.hide());

  const yTickFmt = (v) => (v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v);
  const xTicksPb = _adaptiveXAxisTicks(xScale.domain(), { innerWidth });
  const pbTickVals = xTicksPb.length ? xTicksPb : xScale.domain();
  const xAxisPb = g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).tickValues(pbTickVals));
  _styleBandAxisXLabels(xAxisPb.selectAll(".tick text"), innerWidth, pbTickVals.length);
  xAxisPb.selectAll(".tick text").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat(yTickFmt))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);
}

function _getRetirementValidRows(rows) {
  const validRows = [];
  for (let i = 0; i < rows.length; i++) {
    if ((rows[i].portfolio_p50 ?? 0) <= 0) break;
    validRows.push(rows[i]);
  }
  return validRows;
}

function _renderRetirementPortfolioValueChart(chartEl, rows, retirementAge, displayUnit, oneTimeLumpByTableYear) {
  if (typeof d3 === "undefined" || !chartEl || !rows?.length) return;
  d3.select(chartEl).selectAll("*").remove();

  const validRows = _getRetirementValidRows(rows);
  if (!validRows.length) {
    const w = 400;
    const height = 280;
    const g = d3.select(chartEl).append("svg").attr("width", "100%").attr("height", height).attr("viewBox", `0 0 ${w} ${height}`).append("g").attr("transform", "translate(40,20)");
    g.append("text").attr("x", 160).attr("y", 120).attr("text-anchor", "middle").attr("fill", chartTextFill()).text("No portfolio data to display");
    return;
  }

  const retAge = retirementAge ?? 65;
  const barData = validRows.map((r) => {
    const p50 = Math.max(0, r.portfolio_p50 ?? 0);
    const p10 = Math.max(0, r.portfolio_p10 ?? 0);
    const p90 = Math.max(0, r.portfolio_p90 ?? 0);
    return {
      age: retAge + r.year,
      tableYear: r.year,
      p10,
      p50,
      p90,
      value: p50,
    };
  });

  const lumps = Array.isArray(oneTimeLumpByTableYear) ? oneTimeLumpByTableYear : [];
  const w = Math.max(chartEl.clientWidth || chartEl.offsetWidth || 0, 400);
  const height = Math.max(280, chartEl.clientHeight || 280);
  const margin = { top: 24, right: 24, bottom: 58, left: 56 };
  const innerWidth = w - margin.left - margin.right;

  const xScale = d3.scaleBand()
    .domain(barData.map((d) => String(d.age)))
    .range([0, innerWidth])
    .paddingInner(0.2)
    .paddingOuter(0.1);

  const bandW = xScale.bandwidth();
  const xBandStep = xScale.step();
  const ageSet = new Set(barData.map((d) => d.age));
  const byTableYear = new Map(barData.map((d) => [d.tableYear, d]));
  /** Vertical separation between stacked one-time labels (plot px). */
  const staggerStep = 15;
  /** @type {{ d: (typeof barData)[0], text: string, xCenter: number, stagger: number }[]} */
  const lumpMarkers = [];
  lumps.forEach((rawAmt, tableYear) => {
    const amt = Number(rawAmt);
    if (!Number.isFinite(amt) || amt < 1) return;
    const text = _fmtOneTimeExpenseLabel(amt);
    if (!text) return;
    const d = byTableYear.get(tableYear);
    if (!d || !ageSet.has(d.age)) return;
    const xCenter = xScale(String(d.age)) + bandW / 2;
    lumpMarkers.push({ d, text, xCenter, stagger: 0 });
  });
  lumpMarkers.sort((a, b) => a.xCenter - b.xCenter || a.d.tableYear - b.d.tableYear);
  // Adjacent age columns are ~xBandStep apart; bandW*1.35 was too small, so nothing staggered and labels overlapped.
  let staggerRun = 0;
  let prevXCenter = null;
  let maxStagger = 0;
  const nearNeighborPx = xBandStep * 1.15;
  lumpMarkers.forEach((m) => {
    if (prevXCenter != null && m.xCenter - prevXCenter < nearNeighborPx) staggerRun += 1;
    else staggerRun = 0;
    prevXCenter = m.xCenter;
    m.stagger = staggerRun;
    maxStagger = Math.max(maxStagger, staggerRun);
  });
  margin.top = 24 + (lumpMarkers.length ? 12 : 0) + 14;
  const innerHeight = height - margin.top - margin.bottom;
  const maxP50 = Math.max(1, ...barData.map((d) => d.p50));
  /** Y-axis max: 20% headroom above the highest P50 (P10–P90 may clip at top if wider). */
  const maxVal = maxP50 * 1.2;
  // Reserve inner-top band for one-time labels so negative SVG y (clip) is avoided; stagger stacks upward in this band.
  const labelPadTop = lumpMarkers.length ? 10 + maxStagger * staggerStep + 6 : 0;
  const yScale = d3.scaleLinear()
    .domain([0, maxVal])
    .range([innerHeight, labelPadTop]);

  const yTickFmt = (v) => {
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
    return "$" + v;
  };

  const tooltip = _chartTooltip();
  const svg = d3.select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const clipId = `ret-portfolio-clip-${Math.random().toString(36).slice(2, 11)}`;
  svg
    .append("defs")
    .append("clipPath")
    .attr("id", clipId)
    .append("rect")
    .attr("x", 0)
    .attr("y", labelPadTop)
    .attr("width", innerWidth)
    .attr("height", Math.max(0, innerHeight - labelPadTop));

  const plotG = g.append("g").attr("clip-path", `url(#${clipId})`);

  const bandLo = (d) => Math.min(d.p10, d.p90);
  const bandHi = (d) => Math.max(d.p10, d.p90);

  if (barData.length >= 2) {
    const areaGen = d3
      .area()
      .x((d) => xScale(String(d.age)) + bandW / 2)
      .y0((d) => yScale(bandLo(d)))
      .y1((d) => yScale(bandHi(d)))
      .curve(d3.curveMonotoneX);

    plotG.append("path")
      .datum(barData)
      .attr("fill", "rgba(217, 119, 6, 0.32)")
      .attr("stroke", "none")
      .attr("d", areaGen);
  } else if (barData.length === 1) {
    const d = barData[0];
    const x0 = xScale(String(d.age));
    const yTop = yScale(bandHi(d));
    const yBot = yScale(bandLo(d));
    plotG.append("rect")
      .attr("x", x0 + bandW / 2 - 4)
      .attr("y", yTop)
      .attr("width", 8)
      .attr("height", Math.max(0, yBot - yTop))
      .attr("rx", 2)
      .attr("fill", "rgba(217, 119, 6, 0.32)");
  }

  barData.forEach((d) => {
    const x = xScale(String(d.age));
    const barHeight = innerHeight - yScale(d.p50);
    plotG.append("rect")
      .attr("x", x)
      .attr("y", yScale(d.p50))
      .attr("width", bandW)
      .attr("height", barHeight)
      .attr("fill", "rgba(59,130,246,0.85)")
      .attr("rx", 2);
  });

  svg
    .append("text")
    .attr("x", w - margin.right)
    .attr("y", margin.top - 4)
    .attr("text-anchor", "end")
    .attr("font-size", CHART_BODY_FONT_PX)
    .attr("fill", chartHeadingFill())
    .text("Amber band: P10–P90  •  Blue bars: P50");

  barData.forEach((d) => {
    const x = xScale(String(d.age));
    g.append("rect")
      .attr("x", x)
      .attr("y", labelPadTop)
      .attr("width", bandW)
      .attr("height", innerHeight - labelPadTop)
      .attr("fill", "transparent")
      .attr("pointer-events", "all")
      .style("cursor", "crosshair")
      .on("mouseenter", (event) => {
        tooltip.show(
          `Age ${d.age}<br>P10: ${_fmtVal(d.p10, displayUnit)}<br>P50: ${_fmtVal(d.p50, displayUnit)}<br>P90: ${_fmtVal(d.p90, displayUnit)}`,
          event.pageX,
          event.pageY,
        );
      })
      .on("mousemove", (e) => tooltip.move(e.pageX, e.pageY))
      .on("mouseleave", () => tooltip.hide());
  });

  lumpMarkers.forEach((m) => {
    const yBarTop = yScale(m.d.value);
    const lift = 6 + m.stagger * staggerStep;
    const yText = yBarTop - lift;
    g.append("text")
      .attr("class", "one-time-expense-marker")
      .attr("x", m.xCenter)
      .attr("y", yText)
      .attr("text-anchor", "middle")
      .attr("fill", "#c8a96e")
      .attr("font-size", CHART_BODY_FONT_PX)
      .attr("font-weight", "600")
      .attr("dominant-baseline", "alphabetic")
      .text(m.text);
  });

  const portfolioXTicks = _adaptiveXAxisTicks(xScale.domain(), { innerWidth, minPxPerTick: 36 });
  const retPbTicks = portfolioXTicks.length ? portfolioXTicks : xScale.domain();
  const xAxisRet = g.append("g")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(xScale).tickValues(retPbTicks));
  _styleBandAxisXLabels(xAxisRet.selectAll(".tick text"), innerWidth, retPbTicks.length);
  xAxisRet.selectAll(".tick text").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);

  g.append("g")
    .call(d3.axisLeft(yScale).tickFormat(yTickFmt))
    .selectAll(".tick text").attr("x", -8).attr("text-anchor", "end").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);
}

function _renderRetirementCashFlowsChart(chartEl, rows, retirementAge, displayUnit) {
  if (typeof d3 === "undefined" || !chartEl || !rows?.length) return;

  const port = (r) => Math.max(0, r.portfolio_p50 ?? 0);
  const out = (r) => (port(r) <= 0 ? 0 : (r.outflow ?? 0));
  const yv = (r) => (port(r) <= 0 ? 0 : (r.yield_p50 ?? 0));
  const growthVal = (r) => (port(r) <= 0 ? 0 : (r.price_p50 ?? 0));
  const growthPlusYield = (r) => growthVal(r) + yv(r);
  const netVal = (r) => (port(r) <= 0 ? null : (r.net_p50 ?? null));

  /** @type {{ key: string, label: string, color: string, getVal: (r: object) => number | null }[]} */
  const allSeries = [
    {
      key: "growth",
      label: "Growth",
      color: "rgba(59,130,246,0.85)",
      getVal: growthPlusYield,
    },
    {
      key: "outflow",
      label: "Outflow",
      color: "rgba(239,68,68,0.8)",
      getVal: (r) => -(out(r) || 0),
    },
    {
      key: "net",
      label: "Net",
      color: "rgba(168,85,247,0.8)",
      getVal: netVal,
    },
  ];

  const validRows = _getRetirementValidRows(rows);
  if (!validRows.length) {
    d3.select(chartEl).selectAll("*").remove();
    const w = 400;
    const height = 280;
    const g = d3.select(chartEl).append("svg").attr("width", "100%").attr("height", height).attr("viewBox", `0 0 ${w} ${height}`).append("g").attr("transform", "translate(40,20)");
    g.append("text").attr("x", 160).attr("y", 120).attr("text-anchor", "middle").attr("fill", chartTextFill()).text("No cash flow projection data to display");
    return;
  }

  const barData = validRows.map((r) => ({
    year: r.year,
    age: (retirementAge ?? 65) + r.year,
    row: r,
  }));

  let filterKey = /** @type {string | null} */ (null);

  const yTickFmt = (v) => {
    const abs = Math.abs(v);
    const sign = v < 0 ? "-" : "";
    if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(0) + "K";
    return sign + "$" + v;
  };

  const draw = () => {
    d3.select(chartEl).selectAll("*").remove();
    const activeSeries = filterKey == null ? allSeries : allSeries.filter((s) => s.key === filterKey);

    const valsForY = (seriesList) =>
      barData.flatMap((d) => seriesList.map((s) => s.getVal(d.row)).filter((v) => v != null && Number.isFinite(v)));

    let yMin;
    let yMax;
    if (filterKey != null) {
      const fv = valsForY(activeSeries);
      const m = fv.length ? Math.max(...fv.map((x) => Math.abs(x)), 1) : 1;
      const pad = m * 0.5;
      yMin = -m - pad;
      yMax = m + pad;
    } else {
      const allV = valsForY(allSeries);
      const maxAbs = allV.length ? Math.max(1, ...allV.map(Math.abs)) : 1;
      yMin = -maxAbs;
      yMax = maxAbs;
    }

    const w = Math.max(chartEl.clientWidth || chartEl.offsetWidth || 0, 400);
    const height = Math.max(280, chartEl.clientHeight || 280);
    const margin = { top: 42, right: 24, bottom: 58, left: 56 };
    const innerWidth = w - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const xScale = d3
      .scaleBand()
      .domain(barData.map((d) => String(d.age)))
      .range([0, innerWidth])
      .paddingInner(0.2)
      .paddingOuter(0.1);

    const yScale = d3
      .scaleLinear()
      .domain([yMin, yMax])
      .range([innerHeight, 0])
      .nice();

    const n = activeSeries.length;
    const packW = xScale.bandwidth() * 0.88;
    const barGap = Math.min(6, packW * 0.06);
    const barWidth = n > 0 ? (packW - (n - 1) * barGap) / n : packW;

    const tooltip = _chartTooltip();
    const svg = d3
      .select(chartEl)
      .append("svg")
      .attr("width", "100%")
      .attr("height", height)
      .attr("viewBox", `0 0 ${w} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    g.append("line")
      .attr("x1", 0)
      .attr("x2", innerWidth)
      .attr("y1", yScale(0))
      .attr("y2", yScale(0))
      .attr("stroke", chartTextFill())
      .attr("stroke-opacity", 0.3)
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "4,4");

    barData.forEach((d) => {
      const x0 = xScale(String(d.age)) + (xScale.bandwidth() - packW) / 2;
      activeSeries.forEach((s, si) => {
        const v = s.getVal(d.row);
        if (v == null || !Number.isFinite(v)) return;
        const x = x0 + si * (barWidth + barGap);
        const yBase = yScale(0);
        const yTop = yScale(v);
        const barHeight = Math.abs(yTop - yBase);
        const y = v >= 0 ? yTop : yBase;

        g.append("rect")
          .attr("x", x)
          .attr("y", y)
          .attr("width", barWidth)
          .attr("height", barHeight)
          .attr("fill", s.color)
          .attr("rx", 2)
          .on("mouseenter", (event) => {
            tooltip.show(`${s.label}: ${_fmtVal(v, displayUnit)}<br>Age ${d.age}`, event.pageX, event.pageY);
          })
          .on("mousemove", (e) => tooltip.move(e.pageX, e.pageY))
          .on("mouseleave", () => tooltip.hide());
      });
    });

    const flowsXTicks = _adaptiveXAxisTicks(xScale.domain(), { innerWidth, minPxPerTick: 36 });
    const flowTickVals = flowsXTicks.length ? flowsXTicks : xScale.domain();
    const xAxisFlows = g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).tickValues(flowTickVals));
    _styleBandAxisXLabels(xAxisFlows.selectAll(".tick text"), innerWidth, flowTickVals.length);
    xAxisFlows.selectAll(".tick text").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);

    g.append("g")
      .call(d3.axisLeft(yScale).tickFormat(yTickFmt))
      .selectAll(".tick text")
      .attr("x", -8)
      .attr("text-anchor", "end")
      .attr("fill", chartTextFill())
      .attr("font-size", CHART_BODY_FONT_PX);

    const legendY = -6;
    const legendItemW = 108;
    const legend = g.append("g").attr("transform", `translate(0, ${legendY})`);

    allSeries.forEach((s, i) => {
      const selected = filterKey === s.key;
      const dimmed = filterKey != null && filterKey !== s.key;
      const lg = legend
        .append("g")
        .attr("transform", `translate(${i * legendItemW}, 0)`)
        .style("cursor", "pointer")
        .attr("opacity", dimmed ? 0.35 : 1)
        .on("click", () => {
          filterKey = filterKey === s.key ? null : s.key;
          draw();
        });

      lg.append("rect")
        .attr("class", "retirement-cashflow-legend-hit")
        .attr("x", -2)
        .attr("y", -2)
        .attr("width", legendItemW - 8)
        .attr("height", 22)
        .attr("fill", "transparent");
      lg.append("rect")
        .attr("x", 0)
        .attr("y", 2)
        .attr("width", 12)
        .attr("height", 8)
        .attr("fill", s.color)
        .attr("rx", 2)
        .attr("stroke", selected ? chartHeadingFill() : "none")
        .attr("stroke-width", selected ? 2 : 0);
      lg.append("text")
        .attr("x", 16)
        .attr("y", 10)
        .attr("font-size", CHART_BODY_FONT_PX)
        .attr("font-weight", selected ? 700 : 400)
        .attr("fill", chartHeadingFill())
        .text(s.label);
    });
  };

  draw();
}

/** @param {boolean} [isRetirementPortfolio] — use TTM cash yield (12m) column instead of expected return */
function _renderAssetCorrelationTable(container, assetCorr, isRetirementPortfolio) {
  const { tickers, rows } = assetCorr;
  if (!rows || !rows.length) return;
  const useTtmYield =
    !!isRetirementPortfolio &&
    assetCorr.retirement_yield_column === "ttm" &&
    Object.prototype.hasOwnProperty.call(rows[0], "ttm_yield");
  const yieldHeader = useTtmYield ? "TTM Yield (12m)" : "Expected Annual Return";
  const wrapper = document.createElement("div");
  wrapper.style.overflowX = "auto";
  const table = document.createElement("table");
  table.className = "metrics-table asset-correlation-table";
  table.style.fontSize = `${CHART_BODY_FONT_PX}px`;
  table.style.minWidth = "max-content";
  const thead = document.createElement("thead");
  const headerCells = ["Name", "Ticker", "Weight", ...tickers, yieldHeader, "Annualized Volatility"];
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
    const yieldVal = useTtmYield ? row.ttm_yield : row.expected_return;
    const pctCells = [
      pctFmt(yieldVal),
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

// Aggregate backtest timeseries to year-end portfolio values (one value per year; last row in each year wins).
// Uses portfolio_value_market when present (market only, no inflows/outflows) for chart display.
function _yearEndValues(timeseries) {
  const byYear = {};
  const useMarket = timeseries.length && timeseries[0].portfolio_value_market != null;
  const valueKey = useMarket ? "portfolio_value_market" : "portfolio_value";
  for (const row of timeseries) {
    const v = row[valueKey];
    if (v == null) continue;
    const d = new Date(row.date);
    const yr = d.getFullYear();
    if (!Number.isFinite(yr) || yr < 1900 || yr > 2100) continue;
    byYear[yr] = v;
  }
  const years = Object.keys(byYear).map(Number).filter((y) => Number.isFinite(y)).sort((a, b) => a - b);
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

// Sample MC summary_paths at year boundaries. Year 0 = partial (remaining months in current year).
// E.g. March 2026 -> monthsInYear0=9, year 0 at index 8, year k at index monthsInYear0 + k*12 - 1.
function _annualMC(summaryPaths, totalYears, frequency, startMonth) {
  const mean = summaryPaths.mean || [];
  const nYears = Number(totalYears);
  if (!mean.length || !Number.isFinite(nYears) || nYears <= 0) return null;
  const periodsPerYear = frequency === "monthly" ? 12 : 252;
  const n = mean.length;
  const monthsInYear0 = Math.max(1, 12 - (startMonth ?? new Date().getMonth() + 1));
  const indices = [];
  for (let yr = 0; yr <= nYears; yr++) {
    const idx = yr === 0
      ? Math.min(monthsInYear0 - 1, n - 1)
      : Math.min(monthsInYear0 - 1 + yr * periodsPerYear, n - 1);
    indices.push(idx);
  }
  const sample = (arr) => indices.map((i) => (arr && arr[i] != null ? arr[i] : null));
  return {
    years: indices.map((_, i) => i),
    p10: sample(summaryPaths.p10),
    p50: sample(summaryPaths.p50),
    p90: sample(summaryPaths.p90),
    mean: sample(mean),
    monthsInYear0,
  };
}

// Build timeline data for D3 chart: years from portfolio creation to longevity.
// MC summary_paths include contributions and one-time expenses (inflow/outflow).
function _buildTimelineData(scenarios, artifacts) {
  const best = scenarios[0];
  const sp = best?.summary_paths || {};
  const intake = artifacts?.intake || {};
  const frequency = artifacts.frequency || "monthly";
  const periodsPerYear = frequency === "monthly" ? 12 : 252;
  let horizon = _firstPositiveYears(intake.horizon_years, artifacts.mc_years, 25);
  const longevity = intake.longevity_years ?? horizon + 30;
  const initialValue = intake.initial_value ?? 1;

  // Cap horizon by MC series length. Use max(1, …) so partial year 0 (< 12 monthly points) still charts.
  const nMean = sp.mean?.length ?? 0;
  const dataYears =
    nMean > 0 ? Math.max(1, Math.floor(nMean / periodsPerYear)) : horizon;
  const effectiveHorizon = Math.min(horizon, dataYears);
  const startMonth = intake.start_month ?? new Date().getMonth() + 1;
  const mc = _annualMC(sp, effectiveHorizon, frequency, startMonth);
  if (!mc) return null;

  // Scale only when MC paths look **unit-normalized** (~1 at start), not full dollars.
  // The old heuristic (firstP50 < initialValue/2) wrongly scaled real-dollar paths after a
  // volatile partial first year, inflating labels (e.g. ~$1.15M → ~$13M vs terminal_value_p50).
  let scale = 1;
  const firstP50 = mc.p50[0];
  const iv = Number(initialValue) || 1;
  const pathsLookUnitNormalized =
    firstP50 != null &&
    firstP50 > 0 &&
    firstP50 <= 100 &&
    iv >= 100;
  if (pathsLookUnitNormalized) {
    scale = iv / firstP50;
  }

  const monthlySavings = Number(intake.monthly_savings ?? 0) || 0;
  const rawInflation = (v => Number.isFinite(v) ? v : 3)(Number(intake.inflation_rate ?? intake.inflation_assumption ?? 3));
  const inflationRate = rawInflation > 1 ? rawInflation / 100 : rawInflation;
  const gapYears = new Set((intake.gap_years || []).map((y) => Number(y)));
  const startYear = intake.start_year ?? new Date().getFullYear();
  const data = [];
  let lastValue = (mc.p50[0] != null ? mc.p50[0] * scale : initialValue);

  // Only show accumulation phase (current year through retirement) — no synthetic retirement projection
  // Contribution per year: inflation-adjusted. Year 0 = partial (monthsInYear0 months); year k >= 1 = 12 months.
  const monthsInYear0 = mc.monthsInYear0 ?? Math.max(1, 12 - startMonth);
  for (let yr = 0; yr <= effectiveHorizon; yr++) {
    const calendarYear = startYear + yr;
    let p50, p10, p90;
    if (mc.p50[yr] != null) {
      p50 = mc.p50[yr] * scale;
      p10 = (mc.p10[yr] != null ? mc.p10[yr] * scale : p50 * 0.9);
      p90 = (mc.p90[yr] != null ? mc.p90[yr] * scale : p50 * 1.1);
      lastValue = p50;
    } else {
      p50 = lastValue;
      p10 = lastValue * 0.9;
      p90 = lastValue * 1.1;
    }
    const contribThisYear = gapYears.has(calendarYear)
      ? 0
      : yr === 0
        ? monthlySavings * monthsInYear0 * Math.pow(1 + inflationRate, (monthsInYear0 - 1) / 24)
        : monthlySavings * 12 * Math.pow(1 + inflationRate, yr - 0.5);
    const contribSegment = Math.min(contribThisYear, p50);
    const marketSegment = Math.max(0, p50 - contribSegment);
    data.push({
      year: yr,
      calendarYear,
      p50,
      p10,
      p90,
      marketSegment,
      contribSegment,
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
  const fallbackHorizon = _firstPositiveYears(
    artifacts.intake?.horizon_years,
    artifacts.mc_years,
    25,
  );
  const displayUnit = artifacts?.intake?.display_unit ?? null;
  if (typeof d3 === "undefined") {
    _renderCombinedChart(el, scenarios, fallbackHorizon, artifacts.frequency || "monthly", displayUnit);
    return;
  }

  const timeline = _buildTimelineData(scenarios, artifacts);
  if (!timeline) {
    _renderCombinedChart(el, scenarios, fallbackHorizon, artifacts.frequency || "monthly", displayUnit);
    return;
  }

  const { data, startYear, horizon, longevity, events } = timeline;
  const best = scenarios[0];
  const containerWidth = el.clientWidth || el.offsetWidth || 600;
  const layoutFullWidth = typeof el?.closest === "function" && !!el.closest(".charts-mount--full");
  const width = layoutFullWidth
    ? Math.max(360, containerWidth)
    : Math.min(Math.max(360, containerWidth), 540);
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
    .attr("fill", "rgba(180,180,180,0.35)")
    .attr("stroke", "none")
    .attr("d", area);

  const tooltip = _chartTooltip();

  // P50 stacked bars: bottom = market growth, top = annual contributions (only if contribSegment > 0)
  const barGroup = g.selectAll(".bar-group").data(data).join("g").attr("class", "bar-group");
  // Bottom segment: market growth only
  barGroup.append("rect")
    .attr("class", "bar bar-market")
    .attr("x", (d) => xScale(d.calendarYear))
    .attr("y", (d) => yScale(d.marketSegment))
    .attr("width", xScale.bandwidth())
    .attr("height", (d) => Math.max(0, innerHeight - yScale(d.marketSegment)))
    .attr("fill", (d) => (d.isRetirement ? "#64748b" : "#2563eb"))
    .attr("opacity", 0.85);
  // Top segment: annual contribution stacked
  barGroup.append("rect")
    .attr("class", "bar bar-contrib")
    .attr("x", (d) => xScale(d.calendarYear))
    .attr("y", (d) => yScale(d.p50))
    .attr("width", xScale.bandwidth())
    .attr("height", (d) => Math.max(0, yScale(d.marketSegment) - yScale(d.p50)))
    .attr("fill", "rgba(148,163,184,0.9)")
    .attr("opacity", 0.9);
  barGroup
    .on("mouseenter", (event, d) => {
      let tip = `Year ${d.calendarYear}<br>P50: ${_fmtVal(d.p50)}<br>P10-P90: ${_fmtVal(d.p10)} - ${_fmtVal(d.p90)}`;
      if (d.contribSegment > 0) tip += `<br>Market: ${_fmtVal(d.marketSegment)} · Contrib: ${_fmtVal(d.contribSegment)}`;
      tooltip.show(tip, event.pageX, event.pageY);
    })
    .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
    .on("mouseleave", () => tooltip.hide());

  // Helper: wrap long label into two lines at space near middle; return [line1, line2] or [line]
  const wrapLabel = (text, maxLen = 18) => {
    if (!text || text.length <= maxLen) return [text || ""];
    const mid = Math.floor(text.length / 2);
    const before = text.lastIndexOf(" ", mid);
    const splitAt = before >= 0 ? before : mid;
    return [text.slice(0, splitAt).trim(), text.slice(splitAt).trim()];
  };

  // Collect all vertical markers (retirement + events) with x position and label lines; assign vertical slots to avoid overlap
  const minGap = 36;
  const markers = [];
  const retX = xScale(startYear + horizon) + xScale.bandwidth() / 2;
  const retDataPoint = data.find((d) => d.year === horizon);
  const terminalP50Auth = best?.monte_carlo?.terminal_value_p50;
  const mcP50AtRet =
    terminalP50Auth != null && Number.isFinite(Number(terminalP50Auth))
      ? Number(terminalP50Auth)
      : retDataPoint?.p50;
  const retLabelLines = [`Retirement (${horizon}yr)`];
  if (mcP50AtRet != null) retLabelLines.push(`P50: ${_fmtVal(mcP50AtRet, displayUnit)}`);
  markers.push({ x: retX, lines: retLabelLines, fontWeight: "600" });

  events.forEach((ev) => {
    const evX = xScale(startYear + ev.year) + xScale.bandwidth() / 2;
    if (evX >= 0 && evX <= innerWidth) {
      const lines = wrapLabel(ev.label, 18);
      markers.push({ x: evX, lines, fontWeight: "normal" });
    }
  });

  markers.sort((a, b) => a.x - b.x);
  let slot = 0;
  let lastX = -999;
  markers.forEach((m) => {
    if (m.x - lastX < minGap) slot += 1;
    else slot = 0;
    m.slot = slot;
    lastX = m.x;
  });

  // Retirement line
  g.append("line")
    .attr("x1", retX)
    .attr("x2", retX)
    .attr("y1", 0)
    .attr("y2", innerHeight)
    .attr("stroke", "#dc2626")
    .attr("stroke-width", 2)
    .attr("stroke-dasharray", "4,4");

  // Big expense lines
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
    }
  });

  // Draw all labels with staggered y; retirement P50 $ line slightly larger for readability
  const slotHeight = 16;
  markers.forEach((m) => {
    const baseY = 6 + m.slot * slotHeight;
    const isRetirementP50 =
      m.lines.length > 1 && String(m.lines[1] || "").trim().toLowerCase().startsWith("p50:");
    const textEl = g.append("text")
      .attr("x", m.x - 6)
      .attr("y", baseY)
      .attr("text-anchor", "end")
      .attr("fill", chartTextFill())
      .attr("font-weight", m.fontWeight || "normal");
    if (m.lines.length === 1) {
      textEl.attr("font-size", CHART_BODY_FONT_PX).text(m.lines[0]);
    } else if (isRetirementP50) {
      m.lines.forEach((line, i) => {
        textEl
          .append("tspan")
          .attr("x", m.x - 6)
          .attr("dy", i === 0 ? 0 : "1.12em")
          .attr("font-size", CHART_BODY_FONT_PX)
          .attr("text-anchor", "end")
          .text(line);
      });
    } else {
      m.lines.forEach((line, i) => {
        textEl
          .append("tspan")
          .attr("x", m.x - 6)
          .attr("dy", i === 0 ? 0 : "1.1em")
          .attr("font-size", CHART_BODY_FONT_PX)
          .attr("text-anchor", "end")
          .text(line);
      });
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
        .attr("font-size", CHART_BODY_FONT_PX)
        .attr("fill", "#059669")
        .text(d.incomeDrawn >= 1e6 ? `Income: $${(d.incomeDrawn / 1e6).toFixed(1)}M` : `Income: $${(d.incomeDrawn / 1e3).toFixed(0)}K`);
    }
  });

  const timelineXTicks = _adaptiveXAxisTicks(xScale.domain(), { innerWidth, minPxPerTick: 34 });
  const tlTicks = timelineXTicks.length ? timelineXTicks : xScale.domain();
  g.append("g")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(xScale).tickValues(tlTicks))
    .selectAll("text")
    .attr("transform", "rotate(-42)")
    .attr("dx", "-0.2em")
    .attr("dy", "0.5em")
    .style("text-anchor", "end")
    .attr("fill", chartTextFill())
    .attr("font-size", CHART_BODY_FONT_PX);

  const yFmt = (v) => "$" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : v.toFixed(0));
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat(yFmt))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);

  // Titles
  g.append("text")
    .attr("x", innerWidth / 2)
    .attr("y", -12)
    .attr("text-anchor", "middle")
    .attr("font-size", CHART_BODY_FONT_PX)
    .attr("font-weight", "600")
    .attr("fill", chartTextFill())
    .text(`Portfolio Value by Year (${startYear} – ${startYear + longevity})`);

  // Retirement pie (at retirement year)
  if (retirementComposition && Object.keys(retirementComposition).length) {
    const pieSize = 35;
    const retX = xScale(startYear + horizon) + xScale.bandwidth() / 2;
    const retY = 24;
    g.append("text").attr("x", retX).attr("y", retY - 2).attr("text-anchor", "middle").attr("font-size", CHART_BODY_FONT_PX).attr("fill", chartTextFill()).text("Retirement");
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
    .attr("font-size", CHART_BODY_FONT_PX)
    .attr("fill", chartTextFill())
    .text((d) => d.data[0] + " " + ((d.data[1] / total) * 100).toFixed(0) + "%");
}

function _renderSpaghettiPlot(el, pathsSample, yearsAxis, displayUnit) {
  if (typeof d3 === "undefined") return;
  d3.select(el).selectAll("*").remove();

  const w = el.clientWidth || el.offsetWidth || 400;
  const height = Math.max(252, el.clientHeight || 252);
  const margin = { top: 28, right: 28, bottom: 44, left: 55 };
  const innerWidth = w - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  const tooltip = _chartTooltip();
  let focusedPathIndex = null;
  /** Current scales (updated by redraw) for tooltip on background. */
  let xScale;
  let yScale;

  const computeDomains = (focusedIdx) => {
    const xFull = [d3.min(yearsAxis), d3.max(yearsAxis)];
    if (focusedIdx == null) {
      const allValues = pathsSample.flat().filter((v) => v != null && v > 0);
      const maxVal = d3.max(allValues) || 1;
      const minVal = Math.max(0, d3.min(allValues) || 0);
      return { xDomain: xFull, yDomain: [minVal, Math.max(maxVal * 1.05, minVal + 1e-9)] };
    }
    const pv = pathsSample[focusedIdx];
    const pairs = yearsAxis.map((y, i) => ({ y, v: pv[i] })).filter(
      (p) => p.v != null && Number.isFinite(p.v),
    );
    if (!pairs.length) {
      const allValues = pathsSample.flat().filter((v) => v != null && v > 0);
      const maxVal = d3.max(allValues) || 1;
      const minVal = Math.max(0, d3.min(allValues) || 0);
      return { xDomain: xFull, yDomain: [minVal, Math.max(maxVal * 1.05, minVal + 1e-9)] };
    }
    const ys = pairs.map((p) => p.v);
    let yMax = d3.max(ys);
    let yMin = d3.min(ys);
    if (yMax === yMin) {
      const pad = Math.max(yMax * 0.05, 1);
      return {
        xDomain: [d3.min(pairs, (p) => p.y), d3.max(pairs, (p) => p.y)],
        yDomain: [Math.max(0, yMin - pad), yMax + pad],
      };
    }
    const span = yMax - yMin;
    const yPad = Math.max(span * 0.08, yMax * 0.02, 1);
    const xDomain = [d3.min(pairs, (p) => p.y), d3.max(pairs, (p) => p.y)];
    const lo = Math.max(0, yMin - yPad * 0.35);
    const hi = yMax + yPad;
    return { xDomain, yDomain: [lo, hi] };
  };

  const svg = d3.select(el)
    .append("svg")
    .attr("width", "100%")
    .attr("height", height)
    .attr("viewBox", `0 0 ${w} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet")
    .style("cursor", "default");

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const bg = g
    .append("rect")
    .attr("width", innerWidth)
    .attr("height", innerHeight)
    .attr("fill", "transparent")
    .attr("pointer-events", "all");

  const xAxisG = g.append("g").attr("transform", `translate(0,${innerHeight})`);
  const yAxisG = g.append("g").attr("class", "y-axis");
  const pathGroup = g.append("g").attr("class", "spaghetti-paths");

  g.append("text")
    .attr("x", innerWidth / 2)
    .attr("y", innerHeight + 36)
    .attr("text-anchor", "middle")
    .attr("font-size", CHART_BODY_FONT_PX)
    .attr("fill", chartTextFill())
    .text("Year");

  const yTickFmtSp = (v) =>
    v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v.toFixed(0);
  const spTickTarget = Math.max(2, Math.min(12, Math.floor(innerWidth / 48)));

  pathsSample.forEach((pathValues, i) => {
    pathGroup
      .append("path")
      .attr("class", "mc-spaghetti-line")
      .datum(pathValues)
      .attr("data-i", i)
      .attr("fill", "none")
      .style("cursor", "pointer")
      .on("click", (event) => {
        event.stopPropagation();
        focusedPathIndex = i;
        redraw();
        tooltip.hide();
      });
  });

  function redraw() {
    const { xDomain, yDomain } = computeDomains(focusedPathIndex);
    xScale = d3.scaleLinear().domain(xDomain).range([0, innerWidth]);
    yScale = d3.scaleLinear().domain(yDomain).range([innerHeight, 0]);

    const line = d3
      .line()
      .x((_, i) => xScale(yearsAxis[i]))
      .y((d) => yScale(d != null && Number.isFinite(d) ? d : yDomain[0]))
      .defined((d) => d != null && Number.isFinite(d))
      .curve(d3.curveMonotoneX);

    pathGroup.selectAll("path.mc-spaghetti-line").each(function () {
      const pathValues = d3.select(this).datum();
      d3.select(this)
        .attr("d", line(pathValues))
        .attr("stroke", function () {
          const idx = +d3.select(this).attr("data-i");
          if (focusedPathIndex == null) return "rgba(37,99,235,0.25)";
          return idx === focusedPathIndex ? "rgba(37,99,235,0.95)" : "rgba(37,99,235,0)";
        })
        .attr("stroke-width", function () {
          const idx = +d3.select(this).attr("data-i");
          return focusedPathIndex != null && idx === focusedPathIndex ? 2.2 : 1;
        })
        .attr("pointer-events", function () {
          const idx = +d3.select(this).attr("data-i");
          if (focusedPathIndex != null && idx !== focusedPathIndex) return "none";
          return "stroke";
        });
    });

    xAxisG
      .call(d3.axisBottom(xScale).ticks(spTickTarget).tickFormat((d) => Number(d) + 1))
      .selectAll(".tick text")
      .attr("fill", chartTextFill())
      .attr("font-size", CHART_BODY_FONT_PX);
    yAxisG
      .call(d3.axisLeft(yScale).tickFormat(yTickFmtSp))
      .selectAll(".tick text")
      .attr("x", -9)
      .attr("text-anchor", "end")
      .attr("fill", chartTextFill())
      .attr("font-size", CHART_BODY_FONT_PX);
  }

  g.on("mousemove", (event) => {
    const [mx, my] = d3.pointer(event, g.node());
    if (mx < 0 || mx > innerWidth || my < 0 || my > innerHeight) {
      tooltip.hide();
      return;
    }
    const xVal = xScale.invert(mx);
    let idx = 0;
    let best = Infinity;
    yearsAxis.forEach((y, i) => {
      const d0 = Math.abs(y - xVal);
      if (d0 < best) {
        best = d0;
        idx = i;
      }
    });
    const year = yearsAxis[idx];
    let html;
    if (focusedPathIndex == null) {
      const vals = pathsSample.map((p) => p[idx]).filter((v) => v != null && v > 0);
      const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      html =
        `Year ${year}<br>Paths: ${pathsSample.length}` +
        (avg != null ? `<br>Avg: ${_fmtVal(avg, displayUnit)}` : "");
    } else {
      const v = pathsSample[focusedPathIndex][idx];
      html =
        `Year ${year}<br>Path ${focusedPathIndex + 1} of ${pathsSample.length}` +
        (v != null && Number.isFinite(v) ? `<br>Value: ${_fmtVal(v, displayUnit)}` : "");
    }
    tooltip.show(html, event.pageX, event.pageY);
  }).on("mouseleave", () => tooltip.hide());

  svg.on("dblclick", (event) => {
    event.preventDefault();
    focusedPathIndex = null;
    redraw();
    tooltip.hide();
  });
  svg.on("mouseleave", () => tooltip.hide());

  redraw();
}

function _renderCombinedChart(el, scenarios, years, frequency, displayUnit) {
  if (typeof d3 === "undefined") return;
  d3.select(el).selectAll("*").remove();

  const best = scenarios[0];
  const ts = best.timeseries || [];
  const sp = best.summary_paths || {};
  const periodsPerYear = frequency === "monthly" ? 12 : 252;
  let plotYears = years;
  if (plotYears == null || plotYears <= 0) {
    const n = sp.mean?.length ?? 0;
    plotYears = n > 0 ? Math.max(1, Math.floor(n / periodsPerYear)) : 25;
  }
  plotYears = _firstPositiveYears(plotYears);
  const startYear = new Date().getFullYear();
  const retirementYear = startYear + plotYears;

  let agg = null;
  if (ts.length && plotYears > 0) agg = _backtestToProjection(ts, plotYears);
  const mc = sp.mean?.length && plotYears > 0 ? _annualMC(sp, plotYears, frequency, new Date().getMonth() + 1) : null;

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
  const margin = { top: 40, right: 40, bottom: 58, left: 55 };
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
      .on("mouseenter", (event) => tooltip.show(`Year ${d.year}<br>Backtest: ${_fmtVal(d.value, displayUnit)}`, event.pageX, event.pageY))
      .on("mousemove", (event) => tooltip.move(event.pageX, event.pageY))
      .on("mouseleave", () => tooltip.hide());
  });

  if (allYears.includes(retirementYear)) {
    const retX = xScale(retirementYear) + xScale.bandwidth() / 2;
    g.append("line").attr("x1", retX).attr("x2", retX).attr("y1", 0).attr("y2", innerHeight).attr("stroke", "#dc2626").attr("stroke-width", 2).attr("stroke-dasharray", "4,4");
    const mcPoint = mcData.find((d) => d.year === retirementYear);
    const tv50Comb = best?.monte_carlo?.terminal_value_p50;
    const p50RetLabel =
      tv50Comb != null && Number.isFinite(Number(tv50Comb)) ? Number(tv50Comb) : mcPoint?.p50;
    if (p50RetLabel != null) {
      g.append("text")
        .attr("x", retX + 6)
        .attr("y", 8)
        .attr("font-size", CHART_BODY_FONT_PX)
        .attr("font-weight", "600")
        .attr("fill", chartTextFill())
        .text(`At retirement (${plotYears}yr): MC P50 ${_fmtVal(p50RetLabel, displayUnit)}`);
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
    if (bar) html += `<br>Backtest: ${_fmtVal(bar.value, displayUnit)}`;
    if (mcPt) html += `<br>MC P50: ${_fmtVal(mcPt.p50, displayUnit)}<br>MC P10-P90: ${_fmtVal(mcPt.p10, displayUnit)} - ${_fmtVal(mcPt.p90, displayUnit)}`;
    tooltip.show(html, event.pageX, event.pageY);
  }).on("mouseleave", () => tooltip.hide());

  const combinedXTicks = _adaptiveXAxisTicks(allYears, { innerWidth });
  const combVals = combinedXTicks.length ? combinedXTicks : allYears;
  const xAxisComb = g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).tickValues(combVals));
  _styleBandAxisXLabels(xAxisComb.selectAll(".tick text"), innerWidth, combVals.length);
  xAxisComb.selectAll(".tick text").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);
  const yFmtComb = (v) => (v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "K" : v);
  g.append("g").attr("class", "y-axis")
    .call(d3.axisLeft(yScale).tickFormat(yFmtComb))
    .selectAll(".tick text").attr("x", -9).attr("text-anchor", "end").attr("fill", chartTextFill()).attr("font-size", CHART_BODY_FONT_PX);
  g.append("text").attr("x", innerWidth / 2).attr("y", innerHeight + 36).attr("text-anchor", "middle").attr("font-size", CHART_BODY_FONT_PX).attr("fill", chartTextFill()).text("Year");
}

/** @type {WeakMap<Element, number>} */
const _comparePairedGenByParent = new WeakMap();

/**
 * Semantic chart slot for Connect (growth vs retirement): same kind lines up side-by-side
 * even when DOM order differs between growth and retirement layouts.
 */
const _COMPARE_CHART_KIND_ORDER = [
  "headline",
  "spaghetti",
  "tickers",
  "asset_class",
  "sector",
  "projections",
  "metrics_table",
  "correlations",
];

/**
 * @param {HTMLElement} el
 * @returns {string}
 */
function _compareChartKindFromElement(el) {
  if (!el) return "unknown";
  if (el.classList?.contains("backtest-result-header")) return "headline";
  const h3 = el.querySelector?.(":scope > h3");
  const t = (h3?.textContent || "").trim();
  if (!t) return "unknown";
  if (t === "Performance Metrics" || t.includes("Retirement Monte Carlo Results")) return "metrics_table";
  if (t.includes("Asset Correlations")) return "correlations";
  if (t.includes("Monte Carlo scenarios")) return "spaghetti";
  if (t.includes("historical performance")) return "historical";
  if (t.includes("Cash flow projections")) return "cash_flow";
  if (t.includes("future projections") || t.includes("Portfolio projections vs age")) return "projections";
  if (t === "Chosen Portfolio" || t.includes("Tickers & Weights")) return "tickers";
  if (t === "Asset class") return "asset_class";
  if (t === "Sector") return "sector";
  return "unknown";
}

/**
 * @param {HTMLElement[]} units
 * @returns {{ map: Map<string, HTMLElement>, unknown: HTMLElement[] }}
 */
function _compareUnitsByKind(units) {
  const map = new Map();
  const unknown = [];
  for (const el of units) {
    const k = _compareChartKindFromElement(el);
    if (k === "unknown") unknown.push(el);
    else if (!map.has(k)) map.set(k, el);
    else unknown.push(el);
  }
  return { map, unknown };
}

/**
 * Pair left/right chart units by kind; leftover unknowns pair by index (legacy behavior).
 * @param {HTMLElement[]} lu
 * @param {HTMLElement[]} ru
 * @returns {{ left: HTMLElement | null, right: HTMLElement | null }[]}
 */
function _pairCompareChartUnits(lu, ru) {
  const { map: lm, unknown: luu } = _compareUnitsByKind(lu);
  const { map: rm, unknown: ruu } = _compareUnitsByKind(ru);
  const rows = [];
  for (const kind of _COMPARE_CHART_KIND_ORDER) {
    const l = lm.get(kind);
    const r = rm.get(kind);
    if (kind === "projections") {
      const lProj = lm.get("projections");
      const rProj = rm.get("projections");
      if (lProj || rProj) rows.push({ left: lProj ?? null, right: rProj ?? null });
      lm.delete("projections");
      rm.delete("projections");
      const histL = lm.get("historical");
      const histR = rm.get("historical");
      const cashL = lm.get("cash_flow");
      const cashR = rm.get("cash_flow");
      if (histL || histR || cashL || cashR) {
        rows.push({
          left: histL ?? cashL ?? null,
          right: histR ?? cashR ?? null,
        });
      }
      lm.delete("historical");
      rm.delete("historical");
      lm.delete("cash_flow");
      rm.delete("cash_flow");
      continue;
    }
    if (l || r) rows.push({ left: l ?? null, right: r ?? null });
  }
  const n = Math.max(luu.length, ruu.length);
  for (let i = 0; i < n; i++) {
    rows.push({ left: luu[i] ?? null, right: ruu[i] ?? null });
  }
  return rows;
}

/**
 * Flatten panel DOM into chart cards in document order (for compare pairing).
 * @param {HTMLElement | null} panelWrapper
 * @returns {HTMLElement[]}
 */
function _collectCompareChartUnits(panelWrapper) {
  const units = [];
  if (!panelWrapper) return units;
  for (const child of panelWrapper.children) {
    const cl = child.classList;
    // One-line headline (growth: median at retirement; retirement: % till age N) — must pair like chart cards
    if (cl.contains("backtest-result-header")) {
      units.push(child);
      continue;
    }
    if (cl.contains("chosen-portfolio-charts-grid")) {
      child.querySelectorAll(":scope > .chart-card").forEach((c) => units.push(c));
    } else if (
      cl.contains("retirement-charts-row-1") ||
      cl.contains("charts-row") ||
      cl.contains("retirement-sector-industry-row")
    ) {
      child.querySelectorAll(":scope > .chart-card").forEach((c) => units.push(c));
    } else if (cl.contains("chart-card")) {
      units.push(child);
    }
  }
  return units;
}

/**
 * @param {HTMLElement} host Mount element passed to renderInlineCharts (direct child is .inline-charts).
 * @returns {HTMLElement[]}
 */
function _collectCompareUnitsFromHost(host) {
  const inline = host?.querySelector?.(".inline-charts");
  if (!inline) return [];
  const panels = inline.querySelector(".chosen-portfolio-panels");
  if (panels) return _collectCompareChartUnits(panels);
  const out = [];
  for (const ch of inline.children) {
    if (ch.classList?.contains("chart-card")) out.push(ch);
  }
  return out;
}

/**
 * Renders two artifact sets as horizontal rows: each row pairs the same chart kind (MC spaghetti,
 * tickers, asset class, sector, projections, cash flow vs historical, metrics, etc.).
 * Uses off-screen hosts so renderInlineCharts layout matches the portfolio page.
 * @param {object | null | undefined} leftArtifacts
 * @param {object | null | undefined} rightArtifacts
 * @param {HTMLElement | null} parentEl
 */
function renderComparePairedCharts(leftArtifacts, rightArtifacts, parentEl) {
  if (!parentEl) return;
  const prev = _comparePairedGenByParent.get(parentEl) || 0;
  const myGen = prev + 1;
  _comparePairedGenByParent.set(parentEl, myGen);

  parentEl.innerHTML = "";

  if (!leftArtifacts && !rightArtifacts) return;

  const makeHost = () => {
    const h = document.createElement("div");
    h.className = "charts-mount charts-mount--full compare-paired-offscreen-host";
    h.setAttribute("aria-hidden", "true");
    h.style.cssText =
      "position:fixed;left:-10000px;top:0;width:min(1200px,100vw);max-height:6000px;overflow:auto;opacity:0;pointer-events:none;z-index:-1;";
    return h;
  };

  const lHost = makeHost();
  const rHost = makeHost();
  document.body.appendChild(lHost);
  document.body.appendChild(rHost);

  try {
    if (leftArtifacts) renderInlineCharts(leftArtifacts, lHost);
    if (rightArtifacts) renderInlineCharts(rightArtifacts, rHost);
  } catch (e) {
    console.warn(e);
  }

  const finish = () => {
    if (_comparePairedGenByParent.get(parentEl) !== myGen) {
      lHost.remove();
      rHost.remove();
      return;
    }
    const lu = _collectCompareUnitsFromHost(lHost);
    const ru = _collectCompareUnitsFromHost(rHost);
    lHost.remove();
    rHost.remove();

    const pairedWrapper = document.createElement("div");
    pairedWrapper.className = "compare-paired-charts";
    const pairs = _pairCompareChartUnits(lu, ru);
    for (const { left, right } of pairs) {
      const row = document.createElement("div");
      row.className = "compare-chart-pair-row";
      const lc = document.createElement("div");
      lc.className = "compare-pair-cell compare-pair-cell--left";
      const rc = document.createElement("div");
      rc.className = "compare-pair-cell compare-pair-cell--right";
      if (left) lc.appendChild(left);
      if (right) rc.appendChild(right);
      row.appendChild(lc);
      row.appendChild(rc);
      pairedWrapper.appendChild(row);
    }
    if (parentEl.isConnected) parentEl.appendChild(pairedWrapper);
  };

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(finish);
    });
  });
}

export { renderInlineCharts, renderComparePairedCharts };
