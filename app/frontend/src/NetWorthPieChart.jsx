import * as d3 from "d3";
import { useEffect, useRef, useState } from "react";

function useDocumentTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute("data-theme") || "dark");
  useEffect(() => {
    const root = document.documentElement;
    const read = () => setTheme(root.getAttribute("data-theme") || "dark");
    read();
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return theme;
}

function fmtUsd(n) {
  if (n == null || !Number.isFinite(n)) return "—";
  const x = Math.abs(n);
  if (x >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (x >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (x >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${Math.round(n).toLocaleString()}`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const COL_PHYSICAL = ["#22c55e", "#16a34a", "#4ade80", "#15803d", "#86efac"];
const COL_INVESTMENT = ["#15803d", "#22c55e", "#4ade80", "#166534"];
const AMOUNT_POS_COLOR = "#4ade80";
const AMOUNT_NEG_COLOR = "#f87171";

/**
 * @param {{ key: string, label: string, value: number, kind: 'physical'|'investment'|'debt' }[]} slices
 * @param {number} netWorth
 */
const KIND_LABEL = {
  physical: "Asset",
  investment: "Investment",
  debt: "Debt",
};

export function NetWorthPieChart({ slices, netWorth }) {
  const docTheme = useDocumentTheme();
  const isLight = docTheme === "light";
  const wrapRef = useRef(null);
  const ref = useRef(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const host = ref.current;
    if (!wrap || !host) return;
    const w = 300;
    const h = 300;
    const cx = w / 2;
    const cy = h / 2;
    const outerR = Math.min(w, h) / 2 - 14;
    const innerR = outerR * 0.45;

    d3.select(host).selectAll("*").remove();
    d3.select(wrap).selectAll(".nw-pie-tooltip").remove();

    const tip = d3
      .select(wrap)
      .append("div")
      .attr("class", "nw-pie-tooltip")
      .style("position", "absolute")
      .style("pointer-events", "none")
      .style("opacity", 0)
      .style("z-index", 5)
      .style("max-width", "220px")
      .style("padding", "8px 10px")
      .style("border-radius", "6px")
      .style("font-size", "12px")
      .style("line-height", 1.4)
      .style("color", "#e8e0d0")
      .style("background", "#0f172aea")
      .style("border", "1px solid #334155")
      .style("box-shadow", "0 4px 12px rgba(0,0,0,0.4)")
      .style("transition", "opacity 0.08s ease");

    const svg = d3
      .select(host)
      .append("svg")
      .attr("width", w)
      .attr("height", h)
      .attr("viewBox", `0 0 ${w} ${h}`)
      .style("maxWidth", "100%")
      .style("height", "auto");

    const data = slices.filter((s) => s.value > 0);
    if (!data.length) {
      svg
        .append("text")
        .attr("x", cx)
        .attr("y", cy)
        .attr("text-anchor", "middle")
        .attr("fill", isLight ? "#44403c" : "#64748b")
        .attr("font-size", 13)
        .text("Add assets or debts to see the chart");
      return;
    }

    let iPhys = 0;
    let iInv = 0;
    const colorFor = (d) => {
      if (d.kind === "debt") return "#7f1d1d";
      if (d.kind === "investment") {
        const c = COL_INVESTMENT[iInv % COL_INVESTMENT.length];
        iInv += 1;
        return c;
      }
      const c = COL_PHYSICAL[iPhys % COL_PHYSICAL.length];
      iPhys += 1;
      return c;
    };

    const pie = d3.pie().sort(null).value((d) => d.value);
    const arc = d3.arc().innerRadius(innerR).outerRadius(outerR);
    const g = svg.append("g").attr("transform", `translate(${cx},${cy})`);

    const arcs = g.selectAll("path").data(pie(data)).enter().append("path");

    const setTipHtml = (d) => {
      const row = d.data;
      const kind = KIND_LABEL[row.kind] || row.kind;
      const isDebt = row.kind === "debt";
      const amt = isDebt ? `-${fmtUsd(row.value)}` : fmtUsd(row.value);
      const amtColor = isDebt ? AMOUNT_NEG_COLOR : AMOUNT_POS_COLOR;
      return `<div style="font-weight:600;color:#f1f5f9;">${escapeHtml(row.label)}</div><div style="margin-top:4px;color:#94a3b8;font-size:11px;">${escapeHtml(kind)}</div><div style="margin-top:6px;color:${amtColor};">${escapeHtml(amt)}</div>`;
    };

    const sliceStrokeAsset = isLight ? "#e7e5e4" : "#0f172a";
    arcs
      .attr("d", arc)
      .attr("fill", (d) => colorFor(d.data))
      .attr("opacity", (d) => (d.data.kind === "debt" ? 0.72 : 0.92))
      .attr("stroke", (d) => (d.data.kind === "debt" ? "#fca5a5" : sliceStrokeAsset))
      .attr("stroke-width", (d) => (d.data.kind === "debt" ? 2 : 1))
      .attr("stroke-dasharray", (d) => (d.data.kind === "debt" ? "6 5" : "none"))
      .attr("stroke-linejoin", "round")
      .style("cursor", "pointer")
      .on("mouseenter", (event, d) => {
        tip.html(setTipHtml(d)).style("opacity", 1);
        d3.select(event.currentTarget).attr("opacity", 1);
      })
      .on("mousemove", (event) => {
        const [mx, my] = d3.pointer(event, wrap);
        const pad = 12;
        let left = mx + pad;
        let top = my + pad;
        tip.style("left", `${left}px`).style("top", `${top}px`);
        const node = tip.node();
        if (node) {
          const tw = node.offsetWidth;
          const th = node.offsetHeight;
          if (left + tw > w - 4) left = Math.max(4, mx - tw - pad);
          if (top + th > h - 4) top = Math.max(4, my - th - pad);
          tip.style("left", `${left}px`).style("top", `${top}px`);
        }
      })
      .on("mouseleave", (event) => {
        tip.style("opacity", 0);
        const slice = d3.select(event.currentTarget).datum();
        d3.select(event.currentTarget).attr("opacity", slice.data.kind === "debt" ? 0.72 : 0.92);
      });

    const centerTitleFill = isLight ? "#1c1917" : "#e8e0d0";
    const centerValueFill = isLight
      ? netWorth >= 0
        ? "#15803d"
        : "#b91c1c"
      : netWorth >= 0
        ? AMOUNT_POS_COLOR
        : AMOUNT_NEG_COLOR;

    g.append("text")
      .attr("text-anchor", "middle")
      .attr("dy", "-0.15em")
      .attr("fill", centerTitleFill)
      .attr("font-size", 11)
      .text("Net worth");

    g.append("text")
      .attr("text-anchor", "middle")
      .attr("dy", "1em")
      .attr("fill", centerValueFill)
      .attr("font-size", 16)
      .attr("font-weight", 600)
      .text(fmtUsd(netWorth));
  }, [slices, netWorth, isLight]);

  return (
    <div className="net-worth-pie-root" style={{ display: "flex", justifyContent: "center", width: "100%" }}>
      <div ref={wrapRef} style={{ position: "relative", width: "fit-content", maxWidth: "100%" }}>
        <div ref={ref} style={{ display: "block" }} />
      </div>
    </div>
  );
}
