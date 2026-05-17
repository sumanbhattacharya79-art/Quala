import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { renderInlineCharts } from "./charts.js";
import { compareBacktestArtifactsReady } from "./compareGrowthRetireBridge.js";
import { decorateLifePlannerChartsForSharing } from "./copyChartImage.js";

export function ChartContainer({
  artifacts,
  fullWidth = false,
  chartsBeforeNarrative = false,
  afterCorrelation = null,
  /** When this changes (e.g. light ⟷ dark), charts re-run so D3 fills and inline table styles match the theme. */
  theme = "dark",
  /** Add per-chart "Copy" controls for clipboard / social sharing (portfolio, scenario, chat). */
  enableShareCopy = true,
}) {
  const containerRef = useRef(null);
  const afterCorrSlotRef = useRef(null);
  const afterCorrRootRef = useRef(null);
  const afterCorrelationRef = useRef(afterCorrelation);
  afterCorrelationRef.current = afterCorrelation;

  const wantAfterCorrelation = afterCorrelation != null;

  useEffect(() => {
    if (!artifacts || !containerRef.current) return;
    const parent = containerRef.current;
    const hasCharts =
      (artifacts.all_portfolios && Object.keys(artifacts.all_portfolios).length) ||
      (artifacts.portfolio_composition && Object.keys(artifacts.portfolio_composition).length) ||
      (artifacts.scenarios && artifacts.scenarios.length);
    if (!hasCharts) return;
    parent.innerHTML = "";
    if (afterCorrRootRef.current) {
      afterCorrRootRef.current.unmount();
      afterCorrRootRef.current = null;
    }
    afterCorrSlotRef.current = null;

    renderInlineCharts(artifacts, parent, {
      afterCorrelationMount: wantAfterCorrelation
        ? (slot) => {
            afterCorrSlotRef.current = slot;
            afterCorrRootRef.current = createRoot(slot);
            afterCorrRootRef.current.render(afterCorrelationRef.current);
          }
        : undefined,
    });
    return () => {
      if (afterCorrRootRef.current) {
        afterCorrRootRef.current.unmount();
        afterCorrRootRef.current = null;
      }
      afterCorrSlotRef.current = null;
      parent.innerHTML = "";
    };
  }, [artifacts, fullWidth, chartsBeforeNarrative, wantAfterCorrelation, theme]);

  useEffect(() => {
    if (!wantAfterCorrelation) {
      afterCorrRootRef.current?.unmount();
      afterCorrRootRef.current = null;
      return;
    }
    const slot = afterCorrSlotRef.current;
    if (!slot) return;
    if (!afterCorrRootRef.current) {
      afterCorrRootRef.current = createRoot(slot);
    }
    afterCorrRootRef.current.render(afterCorrelation);
  }, [afterCorrelation, wantAfterCorrelation]);

  const chartsReady = artifacts && compareBacktestArtifactsReady(artifacts);

  useEffect(() => {
    if (!enableShareCopy || !chartsReady || !containerRef.current) return undefined;
    return decorateLifePlannerChartsForSharing(containerRef.current, { enabled: true });
  }, [enableShareCopy, chartsReady, artifacts, fullWidth, chartsBeforeNarrative, wantAfterCorrelation, theme]);

  if (!artifacts) return null;
  const hasCharts =
    (artifacts.all_portfolios && Object.keys(artifacts.all_portfolios).length) ||
    (artifacts.portfolio_composition && Object.keys(artifacts.portfolio_composition).length) ||
    (artifacts.scenarios && artifacts.scenarios.length);
  if (!hasCharts) return null;

  const mountClass = fullWidth ? "charts-mount charts-mount--full" : "charts-mount";
  const blockSpacing = chartsBeforeNarrative
    ? { marginTop: 4, marginBottom: 16 }
    : { marginTop: 12 };
  return (
    <div
      ref={containerRef}
      className={mountClass}
      style={{
        ...blockSpacing,
        width: fullWidth ? "100%" : undefined,
        maxWidth: fullWidth ? "none" : undefined,
        boxSizing: "border-box",
      }}
    />
  );
}
