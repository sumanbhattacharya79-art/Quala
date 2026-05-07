/**
 * Title stripe for a compare column (portfolio or scenario name + type).
 * @param {{ label?: string, source?: string, kind?: string, portfolioId?: string } | null | undefined} sel
 * @param {string} [roleLabel] — e.g. fixed "Growth" / "Retirement" for connect flow; falls back to sel.kind
 * @param {boolean} [frozenShortHeader] — saved Life planner: one line "Growth · Scenario" (no long derived name).
 */
export function titleFromSel(sel) {
  if (!sel) return "";
  const raw = sel.label;
  if (raw != null && String(raw).trim() !== "") return String(raw).trim();
  const pid = sel.portfolioId != null ? String(sel.portfolioId).trim() : "";
  if (pid) return pid.length > 24 ? `${pid.slice(0, 22)}…` : pid;
  return "";
}

export function ComparePortfolioColumnHeader({ sel, roleLabel, frozenShortHeader }) {
  const kindText =
    roleLabel ||
    (sel?.kind === "growth" ? "Growth" : sel?.kind === "retirement" ? "Retirement" : "") ||
    "";
  const meta =
    sel && kindText
      ? `${kindText} · ${sel.source === "scenario" ? "Scenario" : "Portfolio"}`
      : sel
        ? (sel.source === "scenario" ? "Scenario" : "Portfolio")
        : "";

  const title = titleFromSel(sel);

  if (frozenShortHeader && roleLabel && sel) {
    return (
      <div className="compare-column-portfolio-header">
        <div className="compare-column-portfolio-header-title">{meta}</div>
      </div>
    );
  }

  if (!sel) {
    return (
      <div className="compare-column-portfolio-header compare-column-portfolio-header--empty">
        <span className="compare-column-portfolio-header-empty-text">No portfolio selected</span>
      </div>
    );
  }

  if (!title) {
    return (
      <div className="compare-column-portfolio-header compare-column-portfolio-header--empty">
        <span className="compare-column-portfolio-header-empty-text">No portfolio selected</span>
      </div>
    );
  }

  return (
    <div className="compare-column-portfolio-header">
      <div className="compare-column-portfolio-header-title">{title}</div>
      {meta ? <div className="compare-column-portfolio-header-meta">{meta}</div> : null}
    </div>
  );
}
