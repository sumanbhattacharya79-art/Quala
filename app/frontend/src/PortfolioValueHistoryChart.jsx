import { area, curveMonotoneX, line, stack, stackOrderDescending } from "d3-shape";
import { useCallback, useEffect, useMemo, useState, useId } from "react";

const BODY_PX = 11;
const LINE_BLUE = "#2563eb";
const LINE_BLUE_SOFT = "rgba(37,99,235,0.85)";

/** Match `PLOTLY_COLORS` in charts.js (backtesting). */
const SERIES_COLORS = [
  "#2563eb",
  "#7c3aed",
  "#06b6d4",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#64748b",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#84cc16",
  "#a855f7",
];

const MAX_STACK_TICKERS = 11;

const MS_DAY = 86400000;

/** Preset ids for the time-range toolbar (x-axis window ending at the latest series point). */
export const PORTFOLIO_CHART_TIME_RANGES = /** @type {const} */ ([
  { id: "1d", label: "1D" },
  { id: "7d", label: "7D" },
  { id: "30d", label: "30D" },
  { id: "ytd", label: "YTD" },
  { id: "1y", label: "1Y" },
  { id: "5y", label: "5Y" },
]);

/**
 * @param {number} anchorMs latest data point time (ms)
 * @param {typeof PORTFOLIO_CHART_TIME_RANGES[number]["id"]} preset
 */
function computeWindowStartMs(anchorMs, preset) {
  const anchor = new Date(anchorMs);
  switch (preset) {
    case "1d":
      return anchorMs - MS_DAY;
    case "7d":
      return anchorMs - 7 * MS_DAY;
    case "30d":
      return anchorMs - 30 * MS_DAY;
    case "ytd": {
      const y = anchor.getFullYear();
      return new Date(y, 0, 1, 12, 0, 0, 0).getTime();
    }
    case "1y":
      return anchorMs - 365 * MS_DAY;
    case "5y":
      return anchorMs - 5 * 365 * MS_DAY;
    default:
      return anchorMs - 365 * MS_DAY;
  }
}

/** Warm reds/oranges for debt segments (stacked below zero). */
const DEBT_STACK_COLORS = [
  "#dc2626",
  "#ea580c",
  "#f97316",
  "#b91c1c",
  "#f87171",
  "#c2410c",
  "#fb923c",
  "#991b1b",
  "#fdba74",
  "#9a3412",
  "#fecaca",
];

function useChartTheme() {
  const [light, setLight] = useState(
    () => typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light",
  );
  useEffect(() => {
    const el = document.documentElement;
    const sync = () => setLight(el.getAttribute("data-theme") === "light");
    const mo = new MutationObserver(sync);
    mo.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return {
    light,
    text: light ? "#1c1917" : "#ffffff",
    grid: light ? "#78716c" : "#555555",
    subtle: light ? "#57534e" : "#a1a1aa",
  };
}

function niceYTicks(min, max, maxTicks) {
  if (max <= min) return [min];
  const span = max - min;
  const rough = span / Math.max(1, maxTicks - 1);
  const pow10 = 10 ** Math.floor(Math.log10(Math.max(rough, 1e-12)));
  const norm = rough / pow10;
  let step = pow10;
  if (norm <= 1) step = pow10;
  else if (norm <= 2) step = 2 * pow10;
  else if (norm <= 5) step = 5 * pow10;
  else step = 10 * pow10;
  const ticks = [];
  let v = Math.floor(min / step + 1e-9) * step;
  const guard = max + step * 20;
  while (v <= guard) {
    if (v >= min - 1e-9 && v <= max + 1e-9) ticks.push(v);
    v += step;
    if (ticks.length > maxTicks + 8) break;
  }
  if (ticks.length === 0) return [min, max];
  return ticks;
}

function fmtVal(v) {
  if (!Number.isFinite(v)) return "—";
  const neg = v < 0;
  const a = Math.abs(v);
  let s;
  if (a >= 1e6) s = `$${(a / 1e6).toFixed(2)}M`;
  else if (a >= 1e3) s = `$${(a / 1e3).toFixed(1)}K`;
  else s = `$${Math.round(a)}`;
  return neg ? `-${s}` : s;
}

function yTickFmt(v) {
  if (!Number.isFinite(v)) return "";
  const neg = v < 0;
  const a = Math.abs(v);
  let s;
  if (a >= 1e6) s = `${(a / 1e6).toFixed(a >= 10e6 ? 0 : 1)}M`;
  else if (a >= 1e3) s = `${(a / 1e3).toFixed(a >= 100e3 ? 0 : 1)}K`;
  else s = `${Math.round(a)}`;
  return neg ? `-${s}` : s;
}

function formatXTick(ms, rangeDays) {
  const d = new Date(ms);
  if (rangeDays > 700) {
    return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }
  if (rangeDays > 150) {
    return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }
  return d.toLocaleDateString(undefined, { month: "numeric", day: "numeric", year: "2-digit" });
}

function normalizeByTicker(raw) {
  if (!raw || typeof raw !== "object") return null;
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) out[String(k).toUpperCase()] = n;
  }
  return Object.keys(out).length ? out : null;
}

/** Stacked segment map: verbatim keys (e.g. net worth asset names) vs uppercased tickers. */
function normalizeStackMap(raw, verbatim) {
  if (!raw || typeof raw !== "object") return null;
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) continue;
    const key = verbatim ? String(k).trim() : String(k).toUpperCase();
    if (!key) continue;
    out[key] = n;
  }
  return Object.keys(out).length ? out : null;
}

/**
 * Portfolio / net worth value over time; optional stacked areas (`by_ticker` = holdings or assets).
 * @param {{ series: Array<{ date: string, value: number, by_ticker?: Record<string, number>, by_debt?: Record<string, number> }>, asOf?: string | null, granularity?: "daily" | "monthly", title?: string, stackKeysVerbatim?: boolean, flexibleYDomain?: boolean, segmentLabel?: string, debtSegmentLabel?: string, netLineLabel?: string, subtitleSuffix?: string, emptyMessage?: string, stackGroupNote?: string, hideSubtitle?: boolean }} props
 */
export function PortfolioValueHistoryChart({
  series,
  asOf,
  granularity = "daily",
  title = "Portfolio value over time",
  stackKeysVerbatim = false,
  flexibleYDomain = false,
  segmentLabel = "Ticker",
  debtSegmentLabel = "Debt",
  netLineLabel = "Total",
  subtitleSuffix = "",
  emptyMessage,
  stackGroupNote,
  hideSubtitle = false,
}) {
  const theme = useChartTheme();
  const clipId = useId().replace(/:/g, "");
  const gradId = `pv-area-${clipId}`;
  const [tipPos, setTipPos] = useState({ left: 0, top: 0 });
  const [timeRange, setTimeRange] = useState(/** @type {typeof PORTFOLIO_CHART_TIME_RANGES[number]["id"]} */ ("1y"));

  const layout = useMemo(() => {
    const W = 640;
    const H = 268;
    const margin = { top: 28, right: 24, bottom: 56, left: 56 };
    const innerW = W - margin.left - margin.right;
    const innerH = H - margin.top - margin.bottom;

    const raw = Array.isArray(series)
      ? series
          .map((p) => {
            const d = String(p.date || "").slice(0, 10);
            const v = Number(p.value);
            const by_ticker = normalizeStackMap(p.by_ticker, stackKeysVerbatim);
            const by_debt = normalizeStackMap(p.by_debt, stackKeysVerbatim);
            return { d, v, by_ticker, by_debt };
          })
          .filter((p) => p.d && Number.isFinite(p.v))
      : [];

    if (raw.length === 0) {
      return {
        empty: true,
        pts: [],
        linePath: "",
        areaPath: "",
        stackLayers: null,
        debtStackLayers: null,
        stackLegend: null,
        debtStackLegend: null,
        stackBandsByIndex: null,
        yTicks: [],
        xTicks: [],
        minV: 0,
        maxV: 0,
        w: W,
        h: H,
        margin,
        innerW,
        innerH,
        dateRangeLabel: "",
        rangeDays: 0,
        hasStackAreas: false,
        ySpan: 1,
        baselineY: margin.top + innerH,
      };
    }

    const ptsFull = raw.map((p) => {
      const t = new Date(`${p.d}T12:00:00`).getTime();
      return { d: p.d, v: p.v, t, by_ticker: p.by_ticker, by_debt: p.by_debt };
    });

    const anchorT = ptsFull[ptsFull.length - 1].t;
    const windowStartT = computeWindowStartMs(anchorT, timeRange);
    let pts = ptsFull.filter((p) => p.t >= windowStartT && p.t <= anchorT);
    if (pts.length === 0) {
      pts = [ptsFull[ptsFull.length - 1]];
    }

    const rawWin = pts.map((p) => ({
      d: p.d,
      v: p.v,
      by_ticker: p.by_ticker,
      by_debt: p.by_debt,
    }));
    const hasAssetStack = rawWin.some((p) => p.by_ticker && Object.keys(p.by_ticker).length > 0);
    const hasDebtStack = rawWin.some((p) => p.by_debt && Object.keys(p.by_debt).length > 0);
    const hasStackAreas = hasAssetStack || hasDebtStack;

    const vals = pts.map((p) => p.v);
    let maxStackSum = 0;
    pts.forEach((p) => {
      if (p.by_ticker) {
        const s = Object.values(p.by_ticker).reduce((acc, x) => acc + Number(x || 0), 0);
        maxStackSum = Math.max(maxStackSum, s);
      }
    });
    let maxDebtStack = 0;
    pts.forEach((p) => {
      if (p.by_debt) {
        const s = Object.values(p.by_debt).reduce((acc, x) => acc + Number(x || 0), 0);
        maxDebtStack = Math.max(maxDebtStack, s);
      }
    });
    const minVRaw = Math.min(...vals, 0, maxDebtStack > 0 ? -maxDebtStack : 0);
    const maxVRaw = Math.max(...vals, maxStackSum, 0);
    let yMin = 0;
    let yMax = 1;
    if (flexibleYDomain) {
      yMin = minVRaw < 0 ? minVRaw * 1.05 : 0;
      yMax = Math.max(maxVRaw * 1.05, maxVRaw || 1);
      if (yMax <= yMin) yMax = yMin + 1;
    } else {
      const spanData = Math.max(maxVRaw - minVRaw, Math.abs(maxVRaw) * 1e-6, 1);
      const pad = Math.max(spanData * 0.06, Math.abs(maxVRaw) * 0.015, 1);
      yMin = Math.max(0, minVRaw - pad);
      yMax = maxVRaw + pad;
      if (yMax <= yMin) yMax = yMin + 1;
    }
    const ySpan = yMax - yMin || 1;
    const tMin = pts[0].t;
    const tMax = pts[pts.length - 1].t;
    const tSpan = Math.max(tMax - tMin, 86400000);

    const xOf = (t) => margin.left + ((t - tMin) / tSpan) * innerW;
    const yOfVal = (val) => margin.top + innerH - ((val - yMin) / ySpan) * innerH;

    const scaled = pts.map((p) => ({
      d: p.d,
      v: p.v,
      t: p.t,
      by_ticker: p.by_ticker,
      by_debt: p.by_debt,
      x: xOf(p.t),
      y: yOfVal(p.v),
    }));

    const lineGen = line()
      .x((d) => d.x)
      .y((d) => d.y)
      .curve(curveMonotoneX);
    const bottomY = margin.top + innerH;
    const baselineY = flexibleYDomain
      ? Math.min(Math.max(yOfVal(0), margin.top), margin.top + innerH)
      : bottomY;
    const areaGen = area()
      .x((d) => d.x)
      .y0(baselineY)
      .y1((d) => d.y)
      .curve(curveMonotoneX);

    const linePath = scaled.length >= 2 ? lineGen(scaled) : "";
    let areaPath = "";
    if (!hasStackAreas) {
      areaPath = scaled.length >= 2 ? areaGen(scaled) : "";
      if (scaled.length === 1) {
        const p = scaled[0];
        const wbar = Math.min(innerW * 0.02, 8);
        areaPath = `M ${p.x - wbar} ${baselineY} L ${p.x - wbar} ${p.y} L ${p.x + wbar} ${p.y} L ${p.x + wbar} ${baselineY} Z`;
      }
    }

    let stackLayers = null;
    let stackLegend = null;
    let debtStackLayers = null;
    let debtStackLegend = null;
    let assetBandsByIndex = null;
    let debtBandsByIndex = null;

    if (hasAssetStack && scaled.length >= 2) {
      const allKeys = new Set();
      rawWin.forEach((p) => {
        if (p.by_ticker) Object.keys(p.by_ticker).forEach((k) => allKeys.add(k));
      });
      const lastBt = rawWin[rawWin.length - 1].by_ticker || {};
      const sortedKeys = [...allKeys].sort(
        (a, b) => (Number(lastBt[b]) || 0) - (Number(lastBt[a]) || 0),
      );
      const displayKeys = sortedKeys.slice(0, MAX_STACK_TICKERS);
      const otherKeys = sortedKeys.slice(MAX_STACK_TICKERS);
      const stackKeys = [...displayKeys];
      if (otherKeys.length) stackKeys.push("Other");

      const stackData = scaled.map((_, i) => {
        const p = rawWin[i];
        const bt = p.by_ticker || {};
        const row = { _x: scaled[i].x };
        for (const k of displayKeys) {
          row[k] = Number(bt[k]) || 0;
        }
        if (otherKeys.length) {
          row.Other = otherKeys.reduce((s, k) => s + (Number(bt[k]) || 0), 0);
        }
        return row;
      });

      const stacked = stack().keys(stackKeys).order(stackOrderDescending)(stackData);
      const colorByKey = Object.fromEntries(
        stackKeys.map((k, i) => [k, SERIES_COLORS[i % SERIES_COLORS.length]]),
      );

      stackLayers = stacked.map((layer) => {
        const pathD = area()
          .x((d) => d.data._x)
          .y0((d) => yOfVal(Math.max(0, d[0])))
          .y1((d) => yOfVal(Math.max(0, d[1])))
          .curve(curveMonotoneX)(layer);
        const fill = colorByKey[layer.key] || "#64748b";
        const fillOpacity = theme.light ? 0.72 : 0.82;
        return { key: String(layer.key), path: pathD || "", fill, fillOpacity };
      });

      stackLegend = stacked.map((layer) => ({
        key: String(layer.key),
        color: colorByKey[layer.key] || "#64748b",
      }));

      const nPtsA = stackData.length;
      assetBandsByIndex = [];
      for (let i = 0; i < nPtsA; i++) {
        const bands = stacked
          .map((layer) => ({
            key: String(layer.key),
            y0: Number(layer[i][0]),
            y1: Number(layer[i][1]),
          }))
          .filter((b) => b.y1 > b.y0 + 1e-9);
        assetBandsByIndex.push(bands);
      }
    }

    if (hasDebtStack && scaled.length >= 2) {
      const allDebtKeys = new Set();
      rawWin.forEach((p) => {
        if (p.by_debt) Object.keys(p.by_debt).forEach((k) => allDebtKeys.add(k));
      });
      const lastBd = rawWin[rawWin.length - 1].by_debt || {};
      const sortedDebtKeys = [...allDebtKeys].sort(
        (a, b) => (Number(lastBd[b]) || 0) - (Number(lastBd[a]) || 0),
      );
      const displayDebtKeys = sortedDebtKeys.slice(0, MAX_STACK_TICKERS);
      const otherDebtKeys = sortedDebtKeys.slice(MAX_STACK_TICKERS);
      const debtStackKeys = [...displayDebtKeys];
      if (otherDebtKeys.length) debtStackKeys.push("Other");

      const debtStackData = scaled.map((_, i) => {
        const p = rawWin[i];
        const bd = p.by_debt || {};
        const row = { _x: scaled[i].x };
        for (const k of displayDebtKeys) {
          row[k] = Number(bd[k]) || 0;
        }
        if (otherDebtKeys.length) {
          row.Other = otherDebtKeys.reduce((s, k) => s + (Number(bd[k]) || 0), 0);
        }
        return row;
      });

      const stackedDebt = stack().keys(debtStackKeys).order(stackOrderDescending)(debtStackData);
      const debtColorByKey = Object.fromEntries(
        debtStackKeys.map((k, i) => [k, DEBT_STACK_COLORS[i % DEBT_STACK_COLORS.length]]),
      );

      debtStackLayers = stackedDebt.map((layer) => {
        const pathD = area()
          .x((d) => d.data._x)
          .y0((d) => yOfVal(-Number(d[1])))
          .y1((d) => yOfVal(-Number(d[0])))
          .curve(curveMonotoneX)(layer);
        const fill = debtColorByKey[layer.key] || "#dc2626";
        const fillOpacity = theme.light ? 0.65 : 0.78;
        return { key: String(layer.key), path: pathD || "", fill, fillOpacity };
      });

      debtStackLegend = stackedDebt.map((layer) => ({
        key: String(layer.key),
        color: debtColorByKey[layer.key] || "#dc2626",
      }));

      const nPtsD = debtStackData.length;
      debtBandsByIndex = [];
      for (let i = 0; i < nPtsD; i++) {
        const bands = stackedDebt
          .map((layer) => ({
            key: String(layer.key),
            y0: -Number(layer[i][1]),
            y1: -Number(layer[i][0]),
          }))
          .filter((b) => b.y1 > b.y0 + 1e-9);
        debtBandsByIndex.push(bands);
      }
    }

    let stackBandsByIndex = null;
    if (assetBandsByIndex || debtBandsByIndex) {
      const nPts = scaled.length;
      stackBandsByIndex = [];
      for (let i = 0; i < nPts; i++) {
        const ab = assetBandsByIndex && assetBandsByIndex[i] ? assetBandsByIndex[i] : [];
        const db = debtBandsByIndex && debtBandsByIndex[i] ? debtBandsByIndex[i] : [];
        stackBandsByIndex.push([...ab, ...db]);
      }
    }

    const yTicks = niceYTicks(yMin, yMax, flexibleYDomain ? 6 : 5);

    const tickTarget = Math.min(6, Math.max(2, Math.floor(innerW / 72)));
    const xTicks = [];
    for (let i = 0; i < tickTarget; i++) {
      const t = tMin + (i / Math.max(1, tickTarget - 1)) * (tMax - tMin);
      xTicks.push({
        t,
        x: xOf(t),
        label: formatXTick(t, tSpan / 86400000),
      });
    }

    const d0 = pts[0].d;
    const d1 = pts[pts.length - 1].d;
    const fmtRange = (ds) => {
      const x = new Date(`${ds}T12:00:00`);
      return `${String(x.getMonth() + 1).padStart(2, "0")}/${x.getFullYear()}`;
    };
    const dateRangeLabel = `${fmtRange(d0)} – ${fmtRange(d1)}`;

    return {
      empty: false,
      pts: scaled,
      linePath: linePath || "",
      areaPath,
      stackLayers,
      stackLegend,
      stackBandsByIndex,
      yTicks,
      xTicks,
      minV: yMin,
      maxV: yMax,
      w: W,
      h: H,
      margin,
      innerW,
      innerH,
      bottomY,
      baselineY,
      tMin,
      tSpan,
      dateRangeLabel,
      rangeDays: tSpan / 86400000,
      hasStackAreas,
      debtStackLayers,
      debtStackLegend,
      ySpan,
    };
  }, [series, theme.light, stackKeysVerbatim, flexibleYDomain, timeRange]);

  /** idx + optional stack band under cursor (value space y0..y1). */
  const [pointerHover, setPointerHover] = useState(null);

  useEffect(() => {
    setPointerHover(null);
  }, [timeRange]);

  const pickStackBandAtValue = useCallback((bands, vHover, yMax) => {
    if (!bands?.length || !Number.isFinite(vHover)) return null;
    const eps = 1e-7 * Math.max(yMax, 1);
    const sorted = [...bands].sort((a, b) => b.y0 - a.y0);
    for (const b of sorted) {
      if (vHover >= b.y0 - eps && vHover <= b.y1 + eps) {
        return { key: b.key, y0: b.y0, y1: b.y1, value: b.y1 - b.y0 };
      }
    }
    return null;
  }, []);

  const onSvgPointer = useCallback(
    (e) => {
      const {
        pts,
        w,
        h,
        margin,
        innerW,
        innerH,
        tMin,
        tSpan,
        minV,
        maxV,
        ySpan,
        stackBandsByIndex,
        hasStackAreas,
      } = layout;
      if (!pts.length || !tSpan) return;
      const svg = e.currentTarget;
      const rect = svg.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / Math.max(rect.width, 1)) * w;
      const my = ((e.clientY - rect.top) / Math.max(rect.height, 1)) * h;
      const innerX = mx - margin.left;
      const innerY = my - margin.top;
      const clampedX = Math.max(0, Math.min(innerW, innerX));
      const tAt = tMin + (clampedX / innerW) * tSpan;
      let best = 0;
      let bestDist = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const dist = Math.abs(pts[i].t - tAt);
        if (dist < bestDist) {
          bestDist = dist;
          best = i;
        }
      }

      let band = null;
      if (hasStackAreas && stackBandsByIndex && stackBandsByIndex[best]) {
        const clampedY = Math.max(0, Math.min(innerH, innerY));
        const span = ySpan || maxV - minV || 1;
        const vHover = minV + (1 - clampedY / innerH) * span;
        if (innerY >= 0 && innerY <= innerH) {
          band = pickStackBandAtValue(stackBandsByIndex[best], vHover, maxV);
        }
      }

      setPointerHover({ idx: best, band });
      const tipW = 280;
      const left = Math.min(e.clientX + 12, (typeof window !== "undefined" ? window.innerWidth : 800) - tipW);
      const top = Math.max(8, e.clientY - 8);
      setTipPos({ left, top });
    },
    [layout, pickStackBandAtValue],
  );

  const onSvgLeave = useCallback(() => setPointerHover(null), []);

  const granLabel = granularity === "monthly" ? "month-end" : "daily";
  const hp =
    pointerHover != null && layout.pts[pointerHover.idx] ? layout.pts[pointerHover.idx] : null;
  const hoverBand = pointerHover?.band ?? null;

  if (!series || !Array.isArray(series) || series.length === 0 || layout.empty) {
    const emptyText =
      emptyMessage ||
      "No valuation history yet. Save the portfolio with a dollar amount and ensure price CSVs exist in data_output for each ticker.";
    return (
      <div className="chart-card portfolio-value-history-card portfolio-value-history-card--empty" style={{ marginBottom: 8 }}>
        <h3>{title}</h3>
        <div className="chart-div" style={{ padding: "12px 0", color: theme.subtle, fontSize: 13 }}>
          {emptyText}
        </div>
      </div>
    );
  }

  const {
    linePath,
    areaPath,
    stackLayers,
    stackLegend,
    yTicks,
    xTicks,
    w,
    h,
    margin,
    innerW,
    innerH,
    bottomY,
    baselineY,
    minV,
    ySpan,
    dateRangeLabel,
    hasStackAreas,
    debtStackLayers,
    debtStackLegend,
  } = layout;
  const areaFill = `url(#${gradId})`;

  const spanY = ySpan || layout.maxV - minV || 1;
  const markerY =
    hp && hoverBand
      ? margin.top + innerH - (((hoverBand.y0 + hoverBand.y1) / 2 - minV) / spanY) * innerH
      : hp?.y;

  const holdingValueForTip = (() => {
    if (!hoverBand || !hp) return null;
    const bt = hp.by_ticker;
    if (bt && Object.prototype.hasOwnProperty.call(bt, hoverBand.key)) {
      return Number(bt[hoverBand.key]);
    }
    const bd = hp.by_debt;
    if (bd && Object.prototype.hasOwnProperty.call(bd, hoverBand.key)) {
      return Number(bd[hoverBand.key]);
    }
    return hoverBand.value;
  })();

  const segmentLabelForTip =
    hp && hoverBand && hp.by_debt && Object.prototype.hasOwnProperty.call(hp.by_debt, hoverBand.key)
      ? debtSegmentLabel
      : segmentLabel;

  const hoverBandColor =
    hoverBand && (stackLegend || debtStackLegend)
      ? stackLegend?.find((l) => l.key === hoverBand.key)?.color ??
        debtStackLegend?.find((l) => l.key === hoverBand.key)?.color ??
        LINE_BLUE
      : LINE_BLUE;

  return (
    <div className="chart-card portfolio-value-history-card" style={{ marginBottom: 8 }}>
      <h3>{title}</h3>
      {!hideSubtitle ? (
        <p className="chart-subtitle" style={{ margin: "0 0 8px", fontSize: BODY_PX, color: theme.text }}>
          {dateRangeLabel}
          <span style={{ color: theme.subtle }}>
            {" "}
            · {granLabel}
            {subtitleSuffix ? ` · ${subtitleSuffix}` : ""}
            {hasStackAreas ? ` · ${stackGroupNote ?? "by holding"}` : ""}
            {asOf ? ` · priced as of ${asOf}` : ""}
          </span>
        </p>
      ) : null}
      <div
        role="toolbar"
        aria-label="Chart time range"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          marginBottom: 10,
          alignItems: "center",
        }}
      >
        {PORTFOLIO_CHART_TIME_RANGES.map((r) => {
          const active = timeRange === r.id;
          return (
            <button
              key={r.id}
              type="button"
              aria-pressed={active}
              onClick={() => setTimeRange(r.id)}
              style={{
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: active ? 600 : 500,
                borderRadius: 6,
                border: `1px solid ${active ? LINE_BLUE : theme.light ? "#d6d3d1" : "#52525b"}`,
                background: active
                  ? theme.light
                    ? "rgba(37,99,235,0.12)"
                    : "rgba(37,99,235,0.22)"
                  : theme.light
                    ? "rgba(255,255,255,0.55)"
                    : "rgba(255,255,255,0.05)",
                color: theme.text,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {r.label}
            </button>
          );
        })}
      </div>
      <div className="chart-div backtest-chart-container" style={{ minHeight: 248 }}>
        <svg
          width="100%"
          viewBox={`0 0 ${w} ${h}`}
          style={{ maxWidth: 720, display: "block", touchAction: "none" }}
          role="img"
          aria-label={title}
          onMouseMove={onSvgPointer}
          onMouseLeave={onSvgLeave}
          onTouchMove={(e) => {
            if (e.touches[0])
              onSvgPointer({
                ...e,
                clientX: e.touches[0].clientX,
                clientY: e.touches[0].clientY,
                currentTarget: e.currentTarget,
              });
          }}
          onTouchEnd={onSvgLeave}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={LINE_BLUE} stopOpacity="0.2" />
              <stop offset="100%" stopColor={LINE_BLUE} stopOpacity="0.02" />
            </linearGradient>
            <clipPath id={`clip-${clipId}`}>
              <rect x={margin.left} y={margin.top} width={innerW} height={innerH} />
            </clipPath>
          </defs>

          <g aria-hidden>
            {yTicks.map((tv) => {
              const y = margin.top + innerH - ((tv - minV) / spanY) * innerH;
              return (
                <g key={tv}>
                  <line
                    x1={margin.left}
                    x2={margin.left + innerW}
                    y1={y}
                    y2={y}
                    stroke={theme.grid}
                    strokeOpacity={0.45}
                    strokeWidth={1}
                  />
                  <text x={margin.left - 8} y={y + 4} textAnchor="end" fill={theme.text} fontSize={BODY_PX}>
                    {yTickFmt(tv)}
                  </text>
                </g>
              );
            })}
          </g>

          <line
            x1={margin.left}
            x2={margin.left + innerW}
            y1={baselineY}
            y2={baselineY}
            stroke={theme.grid}
            strokeWidth={1}
          />
          <line x1={margin.left} x2={margin.left} y1={margin.top} y2={bottomY} stroke={theme.grid} strokeWidth={1} />

          <g clipPath={`url(#clip-${clipId})`}>
            {debtStackLayers && debtStackLayers.length
              ? debtStackLayers.map((layer) => (
                  <path
                    key={`debt-${layer.key}`}
                    d={layer.path}
                    fill={layer.fill}
                    fillOpacity={layer.fillOpacity}
                    stroke="none"
                  />
                ))
              : null}
            {stackLayers && stackLayers.length
              ? stackLayers.map((layer) => (
                  <path
                    key={layer.key}
                    d={layer.path}
                    fill={layer.fill}
                    fillOpacity={layer.fillOpacity}
                    stroke="none"
                  />
                ))
              : null}
            {!hasStackAreas && areaPath ? <path d={areaPath} fill={areaFill} stroke="none" /> : null}
            {linePath ? (
              <path
                d={linePath}
                fill="none"
                stroke={hasStackAreas ? (theme.light ? "rgba(28,25,23,0.55)" : "rgba(255,255,255,0.75)") : LINE_BLUE_SOFT}
                strokeWidth={hasStackAreas ? 2 : 2.25}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            ) : layout.pts.length === 1 ? (
              <circle cx={layout.pts[0].x} cy={layout.pts[0].y} r={4} fill={LINE_BLUE} />
            ) : null}
          </g>

          <g aria-hidden>
            {xTicks.map((tk, i) => (
              <text key={`${tk.t}-${i}`} x={tk.x} y={baselineY + 22} textAnchor="middle" fill={theme.text} fontSize={BODY_PX}>
                {tk.label}
              </text>
            ))}
          </g>

          <text x={margin.left + innerW / 2} y={h - 10} textAnchor="middle" fill={theme.text} fontSize={BODY_PX}>
            Date
          </text>

          {hp ? (
            <>
              <line
                x1={hp.x}
                y1={margin.top}
                x2={hp.x}
                y2={bottomY}
                stroke={theme.grid}
                strokeWidth={1}
                strokeDasharray="4 3"
                opacity={0.95}
              />
              <circle
                cx={hp.x}
                cy={markerY != null ? markerY : hp.y}
                r={5.5}
                fill={theme.light ? "#ffffff" : "#111111"}
                stroke={hasStackAreas && hoverBand ? hoverBandColor : LINE_BLUE}
                strokeWidth={2}
              />
            </>
          ) : null}
        </svg>
        {stackLegend && stackLegend.length ? (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "10px 14px",
              marginTop: 10,
              fontSize: BODY_PX,
              color: theme.text,
            }}
          >
            {stackLegend.map((item) => (
              <span key={item.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: item.color,
                    opacity: 0.9,
                    flexShrink: 0,
                  }}
                />
                {item.key}
              </span>
            ))}
          </div>
        ) : null}
        {debtStackLegend && debtStackLegend.length ? (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "10px 14px",
              marginTop: stackLegend && stackLegend.length ? 6 : 10,
              fontSize: BODY_PX,
              color: theme.text,
            }}
          >
            {debtStackLegend.map((item) => (
              <span key={`debt-leg-${item.key}`} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: item.color,
                    opacity: 0.9,
                    flexShrink: 0,
                  }}
                />
                {item.key}
                <span style={{ opacity: 0.75, fontSize: BODY_PX - 1 }}>({debtSegmentLabel})</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {hp ? (
        <div
          style={{
            position: "fixed",
            left: tipPos.left,
            top: tipPos.top,
            transform: "translateY(-100%)",
            pointerEvents: "none",
            background: "#1f2937",
            color: "#fff",
            fontSize: 12,
            borderRadius: 8,
            padding: "8px 12px",
            maxWidth: 320,
            maxHeight: "min(70vh, 360px)",
            overflowY: "auto",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            zIndex: 9999,
            lineHeight: 1.45,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{hp.d}</div>
          {hasStackAreas && hoverBand && Number.isFinite(Number(holdingValueForTip)) ? (
            <>
              <div style={{ marginBottom: 4 }}>
                <span style={{ opacity: 0.85 }}>{segmentLabelForTip} </span>
                <span style={{ fontWeight: 600 }}>{hoverBand.key}</span>
              </div>
              <div style={{ marginBottom: 8, fontFamily: "DM Mono, ui-monospace, monospace" }}>
                {fmtVal(Number(holdingValueForTip))}
              </div>
              <div style={{ paddingTop: 6, borderTop: "1px solid rgba(255,255,255,0.12)" }}>
                <span style={{ opacity: 0.85 }}>{netLineLabel} </span>
                <span style={{ fontWeight: 600 }}>{fmtVal(hp.v)}</span>
              </div>
            </>
          ) : (
            <>
              <div style={{ fontWeight: 600 }}>
                {netLineLabel} {fmtVal(hp.v)}
              </div>
              {hasStackAreas ? (
                <div style={{ marginTop: 6, fontSize: 11, opacity: 0.8 }}>
                  Hover a colored band to see that holding.
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
