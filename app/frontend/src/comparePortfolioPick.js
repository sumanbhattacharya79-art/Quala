/** Drag/drop + dropdown selection payloads for Life planner and same-category compare. */

export function portfolioCategoryKind(portfolioRow) {
  return portfolioRow?.portfolio_category === "retirement" ? "retirement" : "growth";
}

export function buildCompareDragPayload({ kind, source, portfolioId, scenarioId, label }) {
  return {
    kind,
    source,
    portfolioId,
    scenarioId: scenarioId ?? null,
    label: label || "Item",
  };
}

/** Stable id for life-planner hydration (avoids re-fetch when selection object reference changes). */
export function connectSelectionKey(left, right) {
  if (!left?.portfolioId || !right?.portfolioId) return "";
  return [
    left.portfolioId,
    left.scenarioId ?? "",
    left.source ?? "",
    right.portfolioId,
    right.scenarioId ?? "",
    right.source ?? "",
  ].join("|");
}

export function compareSelFromDragPayload(payload) {
  if (!payload?.portfolioId || (payload.kind !== "growth" && payload.kind !== "retirement")) {
    return null;
  }
  const rawSid = payload.scenarioId ?? payload.scenario_id ?? null;
  return {
    kind: payload.kind,
    source:
      payload.source === "scenario" || (rawSid != null && String(rawSid).trim() !== "")
        ? "scenario"
        : "portfolio",
    portfolioId: payload.portfolioId,
    scenarioId: rawSid != null && String(rawSid).trim() !== "" ? String(rawSid).trim() : null,
    label: payload.label || "Item",
  };
}

export function scenarioListLabel(scenarioName) {
  const n = (scenarioName || "").trim();
  if (!n) return "Scenario";
  if (/—\s*growth-/i.test(n)) return "Growth · Scenario";
  if (/—\s*retire-/i.test(n)) return "Retirement · Scenario";
  return n;
}

/**
 * @param {Array} savedPortfolios
 * @param {Array} savedScenarios
 * @param {{ requiredKind?: 'growth' | 'retirement' | null, excludeScenarioIds?: Set<string> }} [opts]
 * @returns {{ value: string, label: string, group: string }[]}
 */
export function buildComparePickOptions(savedPortfolios, savedScenarios, opts = {}) {
  const { requiredKind = null, excludeScenarioIds = new Set() } = opts;
  const options = [];
  for (const p of savedPortfolios || []) {
    const kind = portfolioCategoryKind(p);
    if (requiredKind && kind !== requiredKind) continue;
    const portfolioName = p.portfolio_name || "My Portfolio";
    const group = kind === "growth" ? "Growth portfolios" : "Retirement portfolios";
    options.push({
      value: JSON.stringify(
        buildCompareDragPayload({
          kind,
          source: "portfolio",
          portfolioId: p.portfolio_id,
          scenarioId: null,
          label: portfolioName,
        }),
      ),
      label: portfolioName,
      group,
    });
    const scenarios = (savedScenarios || []).filter(
      (s) => s.portfolio_id === p.portfolio_id && !excludeScenarioIds.has(s.scenario_id),
    );
    for (const s of scenarios) {
      const scenLabel = `${portfolioName} — ${scenarioListLabel(s.scenario_name)}`;
      options.push({
        value: JSON.stringify(
          buildCompareDragPayload({
            kind,
            source: "scenario",
            portfolioId: p.portfolio_id,
            scenarioId: s.scenario_id,
            label: scenLabel,
          }),
        ),
        label: scenLabel,
        group: kind === "growth" ? "Growth scenarios" : "Retirement scenarios",
      });
    }
  }
  return options;
}

export function validateLifePlannerPick(side, payload) {
  if (!payload?.portfolioId) return null;
  if (side === "left" && payload.kind !== "growth") {
    return "Left panel is for growth portfolios or scenarios only.";
  }
  if (side === "right" && payload.kind !== "retirement") {
    return "Right panel is for retirement portfolios or scenarios only.";
  }
  return null;
}

export function validateSameCategoryPick(side, payload, leftSel, rightSel) {
  if (!payload?.portfolioId) return null;
  const sel = compareSelFromDragPayload(payload);
  if (!sel) return null;
  if (side === "left" && rightSel && rightSel.kind !== sel.kind) {
    return `Right is ${rightSel.kind}; choose a ${rightSel.kind} item on the left.`;
  }
  if (side === "right" && leftSel && leftSel.kind !== sel.kind) {
    return `Left is ${leftSel.kind}; choose a ${leftSel.kind} item on the right.`;
  }
  return null;
}
