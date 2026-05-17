import { useMemo, useState } from "react";
import { buildComparePickOptions } from "./comparePortfolioPick.js";

/**
 * Drop zone + saved-portfolio dropdown (mobile-friendly) for compare / life planner.
 */
export function CompareDropZone({
  side,
  columnLabel,
  requiredKind = null,
  sel,
  savedPortfolios = [],
  savedScenarios = [],
  excludeScenarioIds = new Set(),
  disabled = false,
  onDrop,
  onPick,
  onClear,
  emptyDragHint,
}) {
  const [pickValue, setPickValue] = useState("");

  const options = useMemo(
    () =>
      buildComparePickOptions(savedPortfolios, savedScenarios, {
        requiredKind,
        excludeScenarioIds,
      }),
    [savedPortfolios, savedScenarios, requiredKind, excludeScenarioIds],
  );

  const grouped = useMemo(() => {
    const map = new Map();
    for (const opt of options) {
      if (!map.has(opt.group)) map.set(opt.group, []);
      map.get(opt.group).push(opt);
    }
    return map;
  }, [options]);

  const handleSelectChange = (e) => {
    const v = e.target.value;
    setPickValue(v);
    if (!v || disabled) return;
    try {
      onPick(JSON.parse(v));
    } catch {
      /* ignore */
    }
    setPickValue("");
  };

  const kindWord = requiredKind === "growth" ? "growth" : requiredKind === "retirement" ? "retirement" : null;

  return (
    <div className="compare-drop-zone-col" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <label className="compare-drop-zone-picker">
        <span className="compare-drop-zone-picker-label">{columnLabel}</span>
        <select
          className="compare-drop-zone-select"
          value={pickValue}
          onChange={handleSelectChange}
          disabled={disabled || options.length === 0}
          aria-label={`Choose ${columnLabel} portfolio or scenario`}
        >
          <option value="">
            {options.length === 0
              ? `No saved ${kindWord || ""} portfolios yet`
              : "Choose saved portfolio or scenario…"}
          </option>
          {[...grouped.entries()].map(([group, opts]) => (
            <optgroup key={group} label={group}>
              {opts.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      <div
        role="region"
        aria-label={`${columnLabel} drop zone`}
        onDragOver={(e) => {
          if (disabled) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragEnter={(e) => {
          if (disabled) return;
          e.preventDefault();
          e.currentTarget.classList.add("compare-page-drop-active");
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget)) {
            e.currentTarget.classList.remove("compare-page-drop-active");
          }
        }}
        onDrop={(e) => {
          if (disabled) return;
          onDrop(side, e);
        }}
        className="compare-drop-zone-panel"
      >
        {sel ? (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, justifyContent: "space-between" }}>
            <div>
              <div className="compare-drop-zone-title">{sel.label}</div>
              <div className="compare-drop-zone-meta">
                {columnLabel}
                {sel.source === "scenario" ? " · Scenario" : " · Portfolio"}
              </div>
            </div>
            <button
              type="button"
              className="login-cancel-btn"
              style={{ fontSize: 11, padding: "6px 12px" }}
              onClick={() => onClear(side)}
              disabled={disabled}
            >
              Clear
            </button>
          </div>
        ) : (
          <div className="compare-drop-zone-placeholder">
            {emptyDragHint || (
              <>
                <span className="compare-drop-zone-placeholder-primary">
                  Use the menu above to pick a{" "}
                  <strong style={{ color: "#c8a96e" }}>{kindWord || "matching"}</strong> item.
                </span>
                <span className="compare-drop-zone-placeholder-drag">
                  On desktop, you can also drag from the sidebar.
                </span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
