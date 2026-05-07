import { useEffect, useRef } from "react";
import { CompareIntakeDiffPanel } from "./CompareIntakeDiffPanel.jsx";
import { ComparePortfolioColumnHeader, titleFromSel } from "./ComparePortfolioColumnHeader.jsx";
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

  return (
    <div className="messages-area" style={{ padding: "20px 28px", width: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <button type="button" className="toggle-btn" onClick={onBack} title="Back">
          ←
        </button>
        <span className="topbar-title">Compare (same type)</span>
        <span className="compare-page-subhint">
          Line up <strong style={{ color: "#c8a96e" }}>two growth</strong> or{" "}
          <strong style={{ color: "#c8a96e" }}>two retirement</strong> portfolios or saved scenarios—the left and right
          columns must match (growth + growth or retirement + retirement, not mixed). Review intake differences, then use{" "}
          <strong style={{ color: "#c8a96e" }}>Continue</strong> to run both backtests and refresh the paired charts.
        </span>
      </div>

      {notice ? (
        <div className="compare-notice-banner" role="status">
          {notice}
        </div>
      ) : null}

      <div style={{ overflowX: "auto", overflowY: "visible", width: "100%", WebkitOverflowScrolling: "touch", paddingBottom: 8 }}>
        <div style={{ minWidth: 920 }}>
          <div className="compare-drop-zones-row">
            {["left", "right"].map((side) => {
              const isLeft = side === "left";
              const sel = isLeft ? leftSel : rightSel;
              return (
                <div key={side} className="compare-drop-zone-col" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div
                    role="region"
                    aria-label={isLeft ? "Drop zone left" : "Drop zone right"}
                    onDragOver={(e) => {
                      e.preventDefault();
                      e.dataTransfer.dropEffect = "copy";
                    }}
                    onDragEnter={(e) => {
                      e.preventDefault();
                      e.currentTarget.classList.add("compare-page-drop-active");
                    }}
                    onDragLeave={(e) => {
                      if (!e.currentTarget.contains(e.relatedTarget)) {
                        e.currentTarget.classList.remove("compare-page-drop-active");
                      }
                    }}
                    onDrop={(e) => onDrop(side, e)}
                    className="compare-drop-zone-panel"
                  >
                    {sel ? (
                      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, justifyContent: "space-between" }}>
                        <div>
                          <div className="compare-drop-zone-title">{sel.label}</div>
                          <div className="compare-drop-zone-meta">
                            {sel.kind}
                            {sel.source === "scenario" ? " · Scenario" : " · Portfolio"}
                          </div>
                        </div>
                        <button type="button" className="login-cancel-btn" style={{ fontSize: 11, padding: "6px 12px" }} onClick={() => onClearSide(side)}>
                          Clear
                        </button>
                      </div>
                    ) : (
                      <div className="compare-drop-zone-placeholder">
                        {kindLabel ? (
                          <>
                            Drop a <strong style={{ color: "#c8a96e" }}>{kindLabel}</strong> portfolio or scenario here (
                            {isLeft ? "left" : "right"} column). Both sides must stay {kindLabel}.
                          </>
                        ) : (
                          <>
                            Drop a <strong style={{ color: "#c8a96e" }}>growth</strong> or{" "}
                            <strong style={{ color: "#c8a96e" }}>retirement</strong> portfolio or scenario here (
                            {isLeft ? "left" : "right"}). Use two growth or two retirement—same type on both sides.
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
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
        <div
          className="compare-portfolio-headers-row"
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 16,
            marginTop: 20,
            marginBottom: 10,
            alignItems: "stretch",
          }}
        >
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
