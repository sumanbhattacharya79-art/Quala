import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getJson, postJson, putJson } from "./api";
import { NetWorthPieChart } from "./NetWorthPieChart";
import { PortfolioValueHistoryChart } from "./PortfolioValueHistoryChart.jsx";
import { MrBrownChat } from "./MrBrownChat.jsx";
import { AdvisorModelOutputDisclaimer } from "./advisorDisclaimer.jsx";

function fmtUsd(n) {
  if (n == null || !Number.isFinite(n)) return "—";
  const x = Math.abs(n);
  if (x >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (x >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (x >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${Math.round(n).toLocaleString()}`;
}

function parseNum(s) {
  if (s === "" || s == null) return 0;
  const n = parseFloat(String(s).replace(/,/g, ""));
  return Number.isFinite(n) ? n : 0;
}

/** Parse YoY field; strips % and commas (user may type "-20%"). */
function parseYoyPct(s) {
  if (s === "" || s == null) return 0;
  const t = String(s).replace(/,/g, "").replace(/%/g, "").trim();
  if (t === "" || t === "-" || t === "+") return 0;
  const n = parseFloat(t);
  return Number.isFinite(n) ? n : 0;
}

function newRow() {
  return { key: crypto.randomUUID(), label: "", price: "", yoy_pct: "" };
}

function newLinkedRow(portfolioId = "") {
  return { key: crypto.randomUUID(), portfolioId };
}

/**
 * Format YoY for display: always show at least one meaningful fraction digit for small values
 * (e.g. 0.3%, 0.6%, 0.04%) and trim unnecessary trailing zeros for larger numbers.
 */
function formatYoyPctForDisplay(n) {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const x = Number(n);
  if (x === 0) return "0.0%";

  const abs = Math.abs(x);
  if (abs >= 100) return `${Math.round(x)}%`;

  let maxFrac;
  if (abs >= 10) maxFrac = 1;
  else if (abs >= 1) maxFrac = 1;
  else if (abs >= 0.1) maxFrac = 1;
  else if (abs >= 0.01) maxFrac = 2;
  else maxFrac = 3;

  let s = x.toFixed(maxFrac);
  s = s.replace(/(\.\d*?[1-9])0+$/, "$1");
  if (s.endsWith(".")) s = s.slice(0, -1);
  s = s.replace(/\.0+$/, "");
  return `${s}%`;
}

/** Line value as % of gross exposure (investments + physical assets + debts), same basis as the composition chart. */
function formatShareOfPortfolioPct(value, grossTotal) {
  if (grossTotal == null || !Number.isFinite(grossTotal) || grossTotal <= 0) return "—";
  const v = Number(value);
  if (!Number.isFinite(v) || v < 0) return "—";
  const pct = (100 * v) / grossTotal;
  if (!Number.isFinite(pct) || pct < 0) return "—";
  if (pct === 0) return "0.0%";
  const abs = Math.abs(pct);
  let maxFrac = 1;
  if (abs < 0.1) maxFrac = 3;
  else if (abs < 1) maxFrac = 2;
  else if (abs >= 99.95 && abs <= 100.05) return "100%";
  let s = pct.toFixed(maxFrac);
  s = s.replace(/(\.\d*?[1-9])0+$/, "$1");
  if (s.endsWith(".")) s = s.slice(0, -1);
  s = s.replace(/\.0+$/, "");
  return `${s}%`;
}

/** Raw cell value (string/number from API or form); empty → em dash. */
function formatYoyPercentDisplay(raw) {
  if (raw === "" || raw == null) return "—";
  const t = String(raw).trim().replace(/%/g, "");
  if (t === "") return "—";
  const n = parseFloat(t.replace(/,/g, ""));
  if (!Number.isFinite(n)) return "—";
  return formatYoyPctForDisplay(n);
}

const NW_POS = "#4ade80";
const NW_NEG = "#f87171";

const cellInputStyle = {
  width: "100%",
  padding: "8px 10px",
  fontSize: 13,
  background: "#111",
  border: "1px solid #2a2a2a",
  borderRadius: 4,
  color: "var(--text)",
  boxSizing: "border-box",
};

const addBtnStyle = {
  padding: "6px 12px",
  fontSize: 18,
  lineHeight: 1,
  minWidth: 40,
  cursor: "pointer",
};

/** YoY column width (px). Label / holding / value columns use 1.5× this for input box room. */
const NW_YOY_W_READONLY = 88;
const NW_YOY_W_EDIT_LINE = 120;
const NW_YOY_W_EDIT_PORTFOLIO = 140;
const nwMainCol = (yoyPx) => Math.round(yoyPx * 1.5);
const NW_MAIN_READONLY = nwMainCol(NW_YOY_W_READONLY);
const NW_MAIN_EDIT_LINE = nwMainCol(NW_YOY_W_EDIT_LINE);
const NW_MAIN_EDIT_PORTFOLIO = nwMainCol(NW_YOY_W_EDIT_PORTFOLIO);

/** Native tooltip on YoY for physical & other asset lines (house, etc.). */
const NW_YOY_TOOLTIP_ASSET =
  "Year-over-year %: each year this line’s dollar value is assumed to move by about this much (e.g. a home or other asset appreciating or depreciating).";

export function NetWorthPanel({ userId, savedPortfolios, onBack, onNetWorthSaved, onLiveNetWorthChange }) {
  const [assetRows, setAssetRows] = useState([]);
  const [debtRows, setDebtRows] = useState([]);
  const [linkedIds, setLinkedIds] = useState(() => new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [portfolioById, setPortfolioById] = useState({});

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draftAssets, setDraftAssets] = useState([]);
  const [draftDebts, setDraftDebts] = useState([]);
  const [draftLinkedRows, setDraftLinkedRows] = useState([]);
  /** Dedupe debounced history-snapshot payloads while editing. */
  const lastHistSnapRef = useRef("");
  const [nwChartSeries, setNwChartSeries] = useState([]);
  const [nwChartAsOf, setNwChartAsOf] = useState(null);

  const load = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError("");
    try {
      const [data, chartPayload] = await Promise.all([
        getJson(`/api/user/net-worth?user_id=${encodeURIComponent(userId)}`),
        getJson(`/api/user/net-worth/chart-series?user_id=${encodeURIComponent(userId)}`).catch(() => ({
          series: [],
          valuation_as_of: null,
        })),
      ]);
      const assets = Array.isArray(data.assets) ? data.assets : [];
      const debts = Array.isArray(data.debts) ? data.debts : [];
      const linked = Array.isArray(data.linked_portfolio_ids) ? data.linked_portfolio_ids : [];
      const fromRowLinks = assets.filter((a) => a.portfolio_id).map((a) => String(a.portfolio_id));
      const mergedLinked = [...new Set([...linked.map(String), ...fromRowLinks])];
      const physicalOnly = assets.filter((a) => !a.portfolio_id);
      setAssetRows(
        physicalOnly.length
          ? physicalOnly.map((a) => ({
              key: a.id && String(a.id).trim() ? String(a.id) : crypto.randomUUID(),
              label: String(a.label ?? ""),
              price: a.price != null ? String(a.price) : "",
              yoy_pct: a.yoy_pct != null ? String(a.yoy_pct) : "",
            }))
          : [],
      );
      setDebtRows(
        debts.length
          ? debts.map((d) => ({
              key: d.id && String(d.id).trim() ? String(d.id) : crypto.randomUUID(),
              label: String(d.label ?? ""),
              price: d.price != null ? String(d.price) : "",
              yoy_pct: d.yoy_pct != null ? String(d.yoy_pct) : "",
            }))
          : [],
      );
      setLinkedIds(new Set(mergedLinked));
      setNwChartSeries(Array.isArray(chartPayload?.series) ? chartPayload.series : []);
      setNwChartAsOf(chartPayload?.valuation_as_of ?? null);
    } catch (err) {
      setError(err.message || "Could not load net worth");
      setAssetRows([]);
      setDebtRows([]);
      setLinkedIds(new Set());
      setNwChartSeries([]);
      setNwChartAsOf(null);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const fromSaved = (savedPortfolios || []).map((p) => p.portfolio_id).filter(Boolean);
    const fromDraft =
      editing && draftLinkedRows.length
        ? draftLinkedRows.map((r) => String(r.portfolioId || "").trim()).filter(Boolean)
        : [];
    const ids = [...new Set([...Array.from(linkedIds), ...fromSaved, ...fromDraft])];
    if (!ids.length) {
      setPortfolioById({});
      return;
    }
    (async () => {
      const next = {};
      await Promise.all(
        ids.map(async (id) => {
          try {
            const row = await getJson(`/api/portfolio/saved/${encodeURIComponent(id)}`);
            if (!cancelled) next[id] = row;
          } catch {
            if (!cancelled) next[id] = null;
          }
        }),
      );
      if (!cancelled) setPortfolioById(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [linkedIds, savedPortfolios, editing, draftLinkedRows]);

  const resolvedPortfolioValue = useCallback(
    (portfolioId) => {
      const fetched = portfolioById[portfolioId];
      if (
        fetched &&
        fetched.portfolio_value != null &&
        Number.isFinite(Number(fetched.portfolio_value))
      ) {
        return Number(fetched.portfolio_value);
      }
      const lite = (savedPortfolios || []).find((p) => p.portfolio_id === portfolioId);
      if (lite && lite.portfolio_value != null && Number.isFinite(Number(lite.portfolio_value))) {
        return Number(lite.portfolio_value);
      }
      return null;
    },
    [portfolioById, savedPortfolios],
  );

  /** Log name/value snapshots with server timestamp while user edits (debounced). */
  useEffect(() => {
    if (!editing || !userId) return;
    const t = setTimeout(() => {
      const items = [];
      for (const r of draftAssets) {
        const name = String(r.label ?? "").trim();
        const value = parseNum(r.price);
        if (!name && value <= 0) continue;
        items.push({ kind: "asset", name: name || "—", value });
      }
      for (const r of draftDebts) {
        const name = String(r.label ?? "").trim();
        const value = parseNum(r.price);
        if (!name && value <= 0) continue;
        items.push({ kind: "debt", name: name || "—", value });
      }
      for (const row of draftLinkedRows) {
        const pid = String(row.portfolioId ?? "").trim();
        if (!pid) continue;
        const v = resolvedPortfolioValue(pid);
        if (v == null || !Number.isFinite(v) || v <= 0) continue;
        const fetched = portfolioById[pid];
        const nm =
          fetched?.portfolio_name ||
          (savedPortfolios || []).find((p) => p.portfolio_id === pid)?.portfolio_name ||
          "Portfolio";
        items.push({
          kind: "asset",
          name: String(nm || "Portfolio").trim() || "Portfolio",
          value: v,
          portfolio_id: pid,
        });
      }
      const key = JSON.stringify(items);
      if (!items.length || key === lastHistSnapRef.current) return;
      lastHistSnapRef.current = key;
      postJson("/api/user/net-worth/history-snapshot", { user_id: userId, items }).catch(() => {});
    }, 1200);
    return () => clearTimeout(t);
  }, [
    editing,
    userId,
    draftAssets,
    draftDebts,
    draftLinkedRows,
    portfolioById,
    savedPortfolios,
    resolvedPortfolioValue,
  ]);

  const startEditing = useCallback(() => {
    setError("");
    const assets =
      assetRows.length > 0 ? assetRows.map((r) => ({ ...r })) : [newRow()];
    const debts = debtRows.length > 0 ? debtRows.map((r) => ({ ...r })) : [newRow()];
    const linked = linkedIds.size > 0 ? Array.from(linkedIds).map((id) => newLinkedRow(id)) : [newLinkedRow("")];
    setDraftAssets(assets);
    setDraftDebts(debts);
    setDraftLinkedRows(linked);
    setEditing(true);
  }, [assetRows, debtRows, linkedIds]);

  const cancelEditing = useCallback(() => {
    setEditing(false);
    setDraftAssets([]);
    setDraftDebts([]);
    setDraftLinkedRows([]);
  }, []);

  const saveEdits = useCallback(async () => {
    if (!userId) return;
    const assetsPayload = draftAssets.map((r) => ({
      label: String(r.label ?? "").trim(),
      price: parseNum(r.price),
      yoy_pct: parseYoyPct(r.yoy_pct),
    }));
    const debtsPayload = draftDebts.map((r) => ({
      label: String(r.label ?? "").trim(),
      price: parseNum(r.price),
      yoy_pct: parseYoyPct(r.yoy_pct),
    }));
    const seen = new Set();
    const linkedPayload = [];
    const linkedYoyPayload = {};
    for (const row of draftLinkedRows) {
      const pid = String(row.portfolioId ?? "").trim();
      if (!pid || seen.has(pid)) continue;
      seen.add(pid);
      linkedPayload.push(pid);
      linkedYoyPayload[pid] = 0;
    }
    setSaving(true);
    setError("");
    try {
      const result = await putJson("/api/user/net-worth", {
        user_id: userId,
        assets: assetsPayload,
        debts: debtsPayload,
        linked_portfolio_ids: linkedPayload,
        linked_portfolio_yoy: linkedYoyPayload,
      });
      lastHistSnapRef.current = "";
      onNetWorthSaved?.(result);
      setEditing(false);
      await load();
    } catch (err) {
      setError(err.message || "Could not save net worth");
    } finally {
      setSaving(false);
    }
  }, [userId, draftAssets, draftDebts, draftLinkedRows, load, onNetWorthSaved]);

  const displayAssets = editing ? draftAssets : assetRows;
  const displayDebts = editing ? draftDebts : debtRows;

  const assetTotal = useMemo(
    () => displayAssets.reduce((s, r) => s + Math.max(0, parseNum(r.price)), 0),
    [displayAssets],
  );
  const debtTotal = useMemo(
    () => displayDebts.reduce((s, r) => s + Math.max(0, parseNum(r.price)), 0),
    [displayDebts],
  );
  const investmentsTotal = useMemo(() => {
    let s = 0;
    const ids = editing
      ? [...new Set(draftLinkedRows.map((r) => String(r.portfolioId || "").trim()).filter(Boolean))]
      : [...linkedIds];
    for (const id of ids) {
      const v = resolvedPortfolioValue(id);
      if (v != null) s += v;
    }
    return s;
  }, [editing, draftLinkedRows, linkedIds, resolvedPortfolioValue]);

  const linkedPortfolioLines = useMemo(() => {
    const ids = editing
      ? draftLinkedRows.map((r) => String(r.portfolioId || "").trim()).filter(Boolean)
      : [...linkedIds];
    const rows = [];
    const seen = new Set();
    for (const id of ids) {
      if (seen.has(id)) continue;
      seen.add(id);
      const fetched = portfolioById[id];
      const name =
        fetched?.portfolio_name ||
        (savedPortfolios || []).find((p) => p.portfolio_id === id)?.portfolio_name ||
        "Portfolio";
      rows.push({ id, name: String(name || "Portfolio").trim() || "Portfolio" });
    }
    return rows;
  }, [editing, draftLinkedRows, linkedIds, portfolioById, savedPortfolios]);

  const netWorth = assetTotal + investmentsTotal - debtTotal;

  /** Keep app sidebar total in sync while this page is open (including unsaved row edits). */
  useEffect(() => {
    if (!onLiveNetWorthChange || !userId) return;
    if (loading) return;
    onLiveNetWorthChange(netWorth);
  }, [loading, netWorth, userId, onLiveNetWorthChange]);

  /** Denominator for % of portfolio (each slice vs total gross exposure). */
  const grossCompositionTotal = useMemo(
    () => investmentsTotal + assetTotal + debtTotal,
    [investmentsTotal, assetTotal, debtTotal],
  );

  const pieSlices = useMemo(() => {
    const out = [];
    const invIds = editing
      ? [...new Set(draftLinkedRows.map((r) => String(r.portfolioId || "").trim()).filter(Boolean))]
      : [...linkedIds];
    for (const id of invIds) {
      const v = resolvedPortfolioValue(id);
      if (v == null || v <= 0) continue;
      const row = portfolioById[id];
      const name =
        row?.portfolio_name ||
        (savedPortfolios || []).find((p) => p.portfolio_id === id)?.portfolio_name ||
        "Portfolio";
      out.push({ key: `inv-${id}`, label: name, value: v, kind: "investment" });
    }
    for (const r of displayAssets) {
      const v = Math.max(0, parseNum(r.price));
      if (v <= 0) continue;
      const lab = String(r.label || "").trim() || "Asset";
      out.push({ key: `phys-${r.key}`, label: lab, value: v, kind: "physical" });
    }
    for (const r of displayDebts) {
      const v = Math.max(0, parseNum(r.price));
      if (v <= 0) continue;
      const lab = String(r.label || "").trim() || "Debt";
      out.push({ key: `debt-${r.key}`, label: lab, value: v, kind: "debt" });
    }
    return out;
  }, [displayAssets, displayDebts, editing, draftLinkedRows, linkedIds, portfolioById, savedPortfolios, resolvedPortfolioValue]);

  const portfolioOptions = useMemo(
    () =>
      (savedPortfolios || [])
        .filter((p) => p.portfolio_id)
        .map((p) => ({
          id: p.portfolio_id,
          name: p.portfolio_name || p.portfolio_id,
        })),
    [savedPortfolios],
  );

  const tableHeaderTh = {
    textAlign: "left",
    padding: "8px 8px 10px",
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
    borderBottom: "1px solid var(--border)",
  };

  const renderLineTable = (title, rows, setRows, kind) => (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 10 }}>{title}</div>
      <div style={{ overflowX: "auto" }}>
        <table aria-label={title} style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              <th style={{ ...tableHeaderTh, minWidth: NW_MAIN_EDIT_LINE, width: NW_MAIN_EDIT_LINE }}>Label</th>
              <th style={{ ...tableHeaderTh, textAlign: "right", minWidth: NW_MAIN_EDIT_LINE, width: NW_MAIN_EDIT_LINE }}>
                Value ($)
              </th>
              <th
                style={{ ...tableHeaderTh, textAlign: "right", width: NW_YOY_W_EDIT_LINE }}
                title={kind === "asset" ? NW_YOY_TOOLTIP_ASSET : undefined}
              >
                YoY
              </th>
              <th style={{ ...tableHeaderTh, textAlign: "right", width: 100, whiteSpace: "normal", lineHeight: 1.25 }}>
                % of portfolio
              </th>
              <th style={{ ...tableHeaderTh, width: 100 }} aria-label="Row actions" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ padding: 12 }}>
                  <button type="button" className="form-primary-btn analyze-table-add-btn" onClick={() => setRows([newRow()])}>
                    + Add row
                  </button>
                </td>
              </tr>
            ) : (
              rows.map((r, idx) => (
                <tr key={r.key} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                  <td style={{ padding: 8, verticalAlign: "middle", minWidth: NW_MAIN_EDIT_LINE }}>
                    <input
                      type="text"
                      value={r.label}
                      onChange={(e) =>
                        setRows((prev) =>
                          prev.map((x) => (x.key === r.key ? { ...x, label: e.target.value } : x)),
                        )
                      }
                      placeholder={kind === "debt" ? "e.g. Mortgage" : "e.g. Tesla"}
                      style={cellInputStyle}
                    />
                  </td>
                  <td style={{ padding: 8, verticalAlign: "middle", minWidth: NW_MAIN_EDIT_LINE }}>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={r.price}
                      onChange={(e) =>
                        setRows((prev) =>
                          prev.map((x) => (x.key === r.key ? { ...x, price: e.target.value } : x)),
                        )
                      }
                      placeholder="0"
                      style={{ ...cellInputStyle, textAlign: "right" }}
                    />
                  </td>
                  <td style={{ padding: 8, verticalAlign: "middle" }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "flex-end",
                        gap: 6,
                        maxWidth: NW_YOY_W_EDIT_LINE,
                        marginLeft: "auto",
                      }}
                    >
                      <input
                        type="text"
                        inputMode="decimal"
                        value={r.yoy_pct}
                        onChange={(e) =>
                          setRows((prev) =>
                            prev.map((x) => (x.key === r.key ? { ...x, yoy_pct: e.target.value } : x)),
                          )
                        }
                        placeholder="e.g. -20"
                        title={kind === "asset" ? NW_YOY_TOOLTIP_ASSET : "Year-over-year % change"}
                        style={{ ...cellInputStyle, flex: "1 1 56px", minWidth: 48, textAlign: "right" }}
                      />
                      <span
                        style={{ color: "var(--text-muted)", fontSize: 13, flexShrink: 0, userSelect: "none" }}
                        title={kind === "asset" ? NW_YOY_TOOLTIP_ASSET : undefined}
                      >
                        %
                      </span>
                    </div>
                  </td>
                  <td
                    style={{
                      padding: 8,
                      verticalAlign: "middle",
                      textAlign: "right",
                      color: "var(--text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                    title="Share of investments + physical assets + debts (gross)"
                  >
                    {formatShareOfPortfolioPct(parseNum(r.price), grossCompositionTotal)}
                  </td>
                  <td style={{ padding: 8, verticalAlign: "middle" }}>
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                      <button
                        type="button"
                        className="form-primary-btn analyze-table-add-btn"
                        title="Remove row"
                        onClick={() =>
                          setRows((prev) => {
                            const next = prev.filter((x) => x.key !== r.key);
                            return next.length > 0 ? next : [];
                          })
                        }
                        style={addBtnStyle}
                      >
                        −
                      </button>
                      {idx === rows.length - 1 ? (
                        <button
                          type="button"
                          className="form-primary-btn analyze-table-add-btn"
                          title="Add row"
                          onClick={() => setRows((prev) => [...prev, newRow()])}
                          style={addBtnStyle}
                        >
                          +
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );

  const renderReadonlyLineTable = (title, rows, kind) => (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 10 }}>{title}</div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>None</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table aria-label={title} style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ ...tableHeaderTh, minWidth: NW_MAIN_READONLY, width: NW_MAIN_READONLY }}>Label</th>
                <th style={{ ...tableHeaderTh, textAlign: "right", minWidth: NW_MAIN_READONLY, width: NW_MAIN_READONLY }}>
                  Value ($)
                </th>
                <th
                  style={{ ...tableHeaderTh, textAlign: "right", width: NW_YOY_W_READONLY }}
                  title={kind === "asset" ? NW_YOY_TOOLTIP_ASSET : undefined}
                >
                  YoY
                </th>
                <th style={{ ...tableHeaderTh, textAlign: "right", width: 100, whiteSpace: "normal", lineHeight: 1.25 }}>
                  % of portfolio
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                  <td style={{ padding: 8, color: "var(--text)", minWidth: NW_MAIN_READONLY }}>{r.label || "—"}</td>
                  <td style={{ padding: 8, textAlign: "right", color: "var(--text)", minWidth: NW_MAIN_READONLY }}>
                    {fmtUsd(parseNum(r.price))}
                  </td>
                  <td
                    style={{ padding: 8, textAlign: "right", color: "var(--text-muted)" }}
                    title={kind === "asset" ? NW_YOY_TOOLTIP_ASSET : undefined}
                  >
                    {formatYoyPercentDisplay(r.yoy_pct)}
                  </td>
                  <td
                    style={{ padding: 8, textAlign: "right", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}
                    title="Share of investments + physical assets + debts (gross)"
                  >
                    {formatShareOfPortfolioPct(parseNum(r.price), grossCompositionTotal)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  const renderReadonlyPortfolioSection = () => (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 10 }}>Portfolio</div>
      {linkedPortfolioLines.length === 0 ? (
        <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>None linked</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table aria-label="Portfolio" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ ...tableHeaderTh, minWidth: NW_MAIN_READONLY, width: NW_MAIN_READONLY }}>Holding</th>
                <th style={{ ...tableHeaderTh, textAlign: "right", minWidth: NW_MAIN_READONLY, width: NW_MAIN_READONLY }}>
                  Value ($)
                </th>
                <th style={{ ...tableHeaderTh, textAlign: "right", width: NW_YOY_W_READONLY }}>YoY</th>
                <th style={{ ...tableHeaderTh, textAlign: "right", width: 100, whiteSpace: "normal", lineHeight: 1.25 }}>
                  % of portfolio
                </th>
              </tr>
            </thead>
            <tbody>
              {linkedPortfolioLines.map(({ id, name }) => {
                const v = resolvedPortfolioValue(id);
                return (
                  <tr key={id} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                    <td style={{ padding: 8, color: "var(--text)", minWidth: NW_MAIN_READONLY }}>{name}</td>
                    <td style={{ padding: 8, textAlign: "right", color: "var(--text)", minWidth: NW_MAIN_READONLY }}>
                      {v != null ? fmtUsd(v) : "—"}
                    </td>
                    <td style={{ padding: 8, textAlign: "right", color: "var(--text-muted)" }}>N/A</td>
                    <td
                      style={{ padding: 8, textAlign: "right", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}
                      title="Share of investments + physical assets + debts (gross)"
                    >
                      {v != null ? formatShareOfPortfolioPct(v, grossCompositionTotal) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  return (
    <div className="messages-area" style={{ padding: "20px 28px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
        <button type="button" className="toggle-btn" onClick={onBack} title="Back">
          ←
        </button>
        <h2 className="topbar-title" style={{ margin: 0 }}>
          Net Worth (investments+assets-debts)
        </h2>
      </div>

      <div className="form-panel form-panel--analyze-full" style={{ marginBottom: 24 }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.55, marginBottom: 16 }}>
          Overview of linked holdings, physical and other assets, and debts (holdings + assets − debts).
        </p>

        {loading ? <div style={{ color: "#888", fontSize: 13 }}>Loading…</div> : null}
        {error ? <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 12 }}>{error}</div> : null}

        <div
          style={{
            display: "grid",
            gap: 12,
            marginBottom: 20,
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          }}
        >
          <div className="sidebar-total">
            <div className="sidebar-total-label">Portfolio</div>
            <div className="sidebar-total-value" style={{ fontSize: 20, color: NW_POS }}>
              {fmtUsd(investmentsTotal)}
            </div>
            {linkedPortfolioLines.length > 0 ? (
              <ul
                style={{
                  listStyle: "none",
                  padding: 0,
                  margin: "12px 0 0",
                  fontSize: 12,
                  lineHeight: 1.4,
                }}
              >
                {linkedPortfolioLines.map(({ id, name }, idx) => (
                  <li
                    key={id}
                    style={{
                      marginTop: idx === 0 ? 8 : 6,
                      borderTop: idx === 0 ? "none" : "1px solid var(--border-soft)",
                      paddingTop: idx === 0 ? 0 : 6,
                      color: "var(--text)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={name}
                  >
                    {name}
                  </li>
                ))}
              </ul>
            ) : !loading && !editing ? (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 10, fontStyle: "italic" }}>None linked</div>
            ) : !loading && editing && linkedPortfolioLines.length === 0 ? (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 10, fontStyle: "italic" }}>
                Add in the Portfolio section below
              </div>
            ) : null}
          </div>
          <div className="sidebar-total">
            <div className="sidebar-total-label">Physical & other assets</div>
            <div className="sidebar-total-value" style={{ fontSize: 20, color: NW_POS }}>
              {fmtUsd(assetTotal)}
            </div>
          </div>
          <div className="sidebar-total">
            <div className="sidebar-total-label">Total debts</div>
            <div className="sidebar-total-value" style={{ fontSize: 20, color: NW_NEG }}>
              {debtTotal > 0 ? `-${fmtUsd(debtTotal)}` : fmtUsd(debtTotal)}
            </div>
          </div>
          <div className="sidebar-total" style={{ borderColor: "#c8a96e55" }}>
            <div className="sidebar-total-label">Net worth (today)</div>
            <div className="sidebar-total-value" style={{ color: netWorth >= 0 ? NW_POS : NW_NEG }}>
              {fmtUsd(netWorth)}
            </div>
          </div>
        </div>

        {!loading ? (
          <div className="form-panel net-worth-composition-card" style={{ marginBottom: 20, padding: 16 }}>
            <h3 style={{ fontSize: 14, margin: "0 0 12px", color: "var(--text)" }}>Composition</h3>
            <NetWorthPieChart slices={pieSlices} netWorth={netWorth} />
          </div>
        ) : null}

        {!loading ? (
          <div className="net-worth-history-chart-wrap" style={{ marginBottom: 20 }}>
            <PortfolioValueHistoryChart
              series={nwChartSeries}
              asOf={nwChartAsOf}
              title="Net worth over time"
              stackKeysVerbatim
              flexibleYDomain
              segmentLabel="Asset"
              netLineLabel="Net worth"
              hideSubtitle
              emptyMessage="Add net worth entries (and optional history snapshots while editing) to see trends. With no history, values repeat daily for the last year."
            />
          </div>
        ) : null}

        {!loading && (pieSlices.length > 0 || (Array.isArray(nwChartSeries) && nwChartSeries.length > 0)) ? (
          <AdvisorModelOutputDisclaimer className="page-output-disclaimer" />
        ) : null}

        {!loading && !editing ? (
          <>
            {renderReadonlyPortfolioSection()}
            {renderReadonlyLineTable("Physical & other assets", assetRows, "asset")}
            {renderReadonlyLineTable("Debts", debtRows, "debt")}
          </>
        ) : null}

        {!loading && editing ? (
          <>
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 10 }}>Portfolio</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.45 }}>
                Choose holdings to include; values follow the sidebar (MTM daily). YoY does not apply to linked portfolios.
              </div>
              {portfolioOptions.length === 0 ? (
                <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>Nothing to link yet.</div>
              ) : draftLinkedRows.length === 0 ? (
                <button
                  type="button"
                  className="form-primary-btn analyze-table-add-btn"
                  onClick={() => setDraftLinkedRows([newLinkedRow("")])}
                >
                  + Add row
                </button>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table aria-label="Portfolio" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr>
                        <th style={{ ...tableHeaderTh, minWidth: NW_MAIN_EDIT_PORTFOLIO, width: NW_MAIN_EDIT_PORTFOLIO }}>
                          Holding
                        </th>
                        <th
                          style={{
                            ...tableHeaderTh,
                            textAlign: "right",
                            minWidth: NW_MAIN_EDIT_PORTFOLIO,
                            width: NW_MAIN_EDIT_PORTFOLIO,
                          }}
                        >
                          Value ($)
                        </th>
                        <th style={{ ...tableHeaderTh, textAlign: "right", width: NW_YOY_W_EDIT_PORTFOLIO }}>YoY</th>
                        <th style={{ ...tableHeaderTh, textAlign: "right", width: 100, whiteSpace: "normal", lineHeight: 1.25 }}>
                          % of portfolio
                        </th>
                        <th style={{ ...tableHeaderTh, width: 100 }} aria-label="Row actions" />
                      </tr>
                    </thead>
                    <tbody>
                      {draftLinkedRows.map((r, idx) => {
                        const rowVal = resolvedPortfolioValue(r.portfolioId);
                        return (
                          <tr key={r.key} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                            <td style={{ padding: 8, verticalAlign: "middle", minWidth: NW_MAIN_EDIT_PORTFOLIO }}>
                              <select
                                value={r.portfolioId}
                                onChange={(e) =>
                                  setDraftLinkedRows((prev) =>
                                    prev.map((x) => (x.key === r.key ? { ...x, portfolioId: e.target.value } : x)),
                                  )
                                }
                                style={{ ...cellInputStyle, maxWidth: "100%" }}
                              >
                                <option value="">— Select —</option>
                                {portfolioOptions.map((opt) => (
                                  <option key={opt.id} value={opt.id}>
                                    {opt.name}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td
                              style={{
                                padding: 8,
                                verticalAlign: "middle",
                                textAlign: "right",
                                color: "var(--text)",
                                minWidth: NW_MAIN_EDIT_PORTFOLIO,
                              }}
                            >
                              {rowVal != null ? fmtUsd(rowVal) : "—"}
                            </td>
                            <td
                              style={{
                                padding: 8,
                                verticalAlign: "middle",
                                textAlign: "right",
                                color: "var(--text-muted)",
                              }}
                              title="Linked portfolios are marked to market daily; YoY is not used"
                            >
                              N/A
                            </td>
                            <td
                              style={{
                                padding: 8,
                                verticalAlign: "middle",
                                textAlign: "right",
                                color: "var(--text-muted)",
                                fontVariantNumeric: "tabular-nums",
                              }}
                              title="Share of investments + physical assets + debts (gross)"
                            >
                              {rowVal != null ? formatShareOfPortfolioPct(rowVal, grossCompositionTotal) : "—"}
                            </td>
                            <td style={{ padding: 8, verticalAlign: "middle" }}>
                              <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                                <button
                                  type="button"
                                  className="form-primary-btn analyze-table-add-btn"
                                  title="Remove row"
                                  onClick={() => setDraftLinkedRows((prev) => prev.filter((x) => x.key !== r.key))}
                                  style={addBtnStyle}
                                >
                                  −
                                </button>
                                {idx === draftLinkedRows.length - 1 ? (
                                  <button
                                    type="button"
                                    className="form-primary-btn analyze-table-add-btn"
                                    title="Add row"
                                    onClick={() => setDraftLinkedRows((prev) => [...prev, newLinkedRow("")])}
                                    style={addBtnStyle}
                                  >
                                    +
                                  </button>
                                ) : null}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            {renderLineTable("Physical & other assets", draftAssets, setDraftAssets, "asset")}
            {renderLineTable("Debts", draftDebts, setDraftDebts, "debt")}
          </>
        ) : null}

        {!loading ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 24, alignItems: "center" }}>
            {!editing ? (
              <button type="button" className="form-primary-btn" onClick={startEditing} disabled={saving}>
                Edit net worth
              </button>
            ) : (
              <>
                <button type="button" className="form-primary-btn" onClick={saveEdits} disabled={saving}>
                  {saving ? "Saving…" : "Save changes"}
                </button>
                <button
                  type="button"
                  className="form-primary-btn secondary"
                  onClick={cancelEditing}
                  disabled={saving}
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
      <MrBrownChat
        userId={userId}
        page="net_worth"
        linkedPortfolioIds={linkedPortfolioLines.map((x) => x.id).filter(Boolean)}
      />
    </div>
  );
}
