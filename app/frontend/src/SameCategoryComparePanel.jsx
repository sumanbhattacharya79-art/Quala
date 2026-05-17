import { useEffect, useMemo, useRef } from "react";
import { CompareIntakeDiffPanel } from "./CompareIntakeDiffPanel.jsx";
import { ComparePortfolioColumnHeader, titleFromSel } from "./ComparePortfolioColumnHeader.jsx";
import { CompareDropZone } from "./CompareDropZone.jsx";
import { renderComparePairedCharts } from "./charts.js";
import { AdvisorModelOutputDisclaimer } from "./advisorDisclaimer.jsx";

/**
 * Side-by-side compare: two growth OR two retirement portfolios/scenarios.
 * Intake diff + paired charts after Continue runs both backtests.
 */
export function SameCategoryComparePanel({
  onBack,
  notice,
  leftSel,
  rightSel,
  intakeLeft,
  intakeRight,
  hydrating,
  leftArtifacts,
  rightArtifacts,
  continueLoading,
  onDrop,
  onPick,
  savedPortfolios = [],
  savedScenarios = [],
  excludeScenarioIds = new Set(),
  onClearSide,
  onContinue,
  theme = "dark",
}) {
  const chartsRef = useRef(null);

  useEffect(() => {
    const el = chartsRef.current;
    if (!el) return;
    if (!leftArtifacts && !rightArtifacts) {
      renderComparePairedCharts(null, null, el);
      return;
    }
    renderComparePairedCharts(leftArtifacts, rightArtifacts, el);
    return () => {
      renderComparePairedCharts(null, null, el);
    };
  }, [leftArtifacts, rightArtifacts, theme]);

  const resolvedKind = leftSel?.kind || rightSel?.kind;
  const kindLabel = resolvedKind === "retirement" ? "retirement" : resolvedKind === "growth" ? "growth" : null;
  const showDiff = intakeLeft && intakeRight && !hydrating;

  const emptyDragHint = useMemo(() => {
    if (kindLabel) {
      return (
        <>
          <span className="compare-drop-zone-placeholder-primary">
            Use the menu above to pick a <strong style={{ color: "#c8a96e" }}>{kindLabel}</strong> item.
          </span>
          <span className="compare-drop-zone-placeholder-drag">On desktop, drag from the sidebar.</span>
        </>
      );
    }
    return (
      <>
        <span className="compare-drop-zone-placeholder-primary">
          Pick a <strong style={{ color: "#c8a96e" }}>growth</strong> or{" "}
          <strong style={{ color: "#c8a96e" }}>retirement</strong> item — both sides must match.
        </span>
        <span className="compare-drop-zone-placeholder-drag">On desktop, drag from the sidebar.</span>
      </>
    );
  }, [kindLabel]);

  return (
    <div className="messages-area compare-messages-area" style={{ padding: "20px 28px", width: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <button type="button" className="toggle-btn" onClick={onBack} title="Back">
          ←
        </button>
        <span className="topbar-title">Compare (same type)</span>
        <span className="compare-page-subhint">
          Line up <strong style={{ color: "#c8a96e" }}>two growth</strong> or{" "}
          <strong style={{ color: "#c8a96e" }}>two retirement</strong> portfolios or saved scenarios—choose from the menus
          or drag on desktop. Both columns must match (growth + growth or retirement + retirement). Review intake
          differences, then use <strong style={{ color: "#c8a96e" }}>Continue</strong> to run both backtests.
        </span>
      </div>

      {notice ? (
        <div className="compare-notice-banner" role="status">
          {notice}
        </div>
      ) : null}

      <div className="compare-page-scroll">
        <div className="compare-page-inner">
          <div className="compare-drop-zones-row">
            <CompareDropZone
              side="left"
              columnLabel="Left"
              requiredKind={resolvedKind}
              sel={leftSel}
              savedPortfolios={savedPortfolios}
              savedScenarios={savedScenarios}
              excludeScenarioIds={excludeScenarioIds}
              onDrop={onDrop}
              onPick={(payload) => onPick("left", payload)}
              onClear={onClearSide}
              emptyDragHint={emptyDragHint}
            />
            <CompareDropZone
              side="right"
              columnLabel="Right"
              requiredKind={resolvedKind}
              sel={rightSel}
              savedPortfolios={savedPortfolios}
              savedScenarios={savedScenarios}
              excludeScenarioIds={excludeScenarioIds}
              onDrop={onDrop}
              onPick={(payload) => onPick("right", payload)}
              onClear={onClearSide}
              emptyDragHint={emptyDragHint}
            />
          </div>
          {hydrating ? <div className="compare-drop-zone-loading">Loading intake…</div> : null}
        </div>
      </div>

      {showDiff ? (
        <div style={{ marginTop: 20 }}>
          <CompareIntakeDiffPanel
            labelLeft={titleFromSel(leftSel) || "Left"}
            labelRight={titleFromSel(rightSel) || "Right"}
            intakeLeft={intakeLeft}
            intakeRight={intakeRight}
          />
        </div>
      ) : null}

      <div className="compare-panel-primary-actions">
        <button
          type="button"
          className="form-primary-btn"
          disabled={continueLoading || !leftSel || !rightSel}
          onClick={onContinue}
        >
          {continueLoading ? "Running backtests…" : "Continue"}
        </button>
      </div>

      {leftSel || rightSel ? (
        <div className="compare-portfolio-headers-row" style={{ marginTop: 20, marginBottom: 10 }}>
          <ComparePortfolioColumnHeader sel={leftSel} />
          <ComparePortfolioColumnHeader sel={rightSel} />
        </div>
      ) : null}
      <div
        ref={chartsRef}
        className="compare-paired-mount charts-mount charts-mount--full"
        style={{ marginTop: leftSel || rightSel ? 0 : 16 }}
      />
      {leftArtifacts && rightArtifacts ? (
        <AdvisorModelOutputDisclaimer className="page-output-disclaimer" />
      ) : null}
    </div>
  );
}
