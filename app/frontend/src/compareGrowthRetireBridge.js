/**
 * Growth → retirement handoff for Compare (linked) flow.
 * P50 = median terminal portfolio value from growth Monte Carlo at the accumulation horizon.
 */

/**
 * True when backtest response has chart/MC payload (not null, not {}, not missing scenarios).
 * Empty objects are truthy in JS; do not use bare `if (artifacts)` for gating edit mode.
 */
export function compareBacktestArtifactsReady(artifacts) {
  if (!artifacts || typeof artifacts !== "object") return false;
  const scenarios = artifacts.scenarios;
  return Array.isArray(scenarios) && scenarios.length > 0;
}

/** @param {unknown} artifacts - saved-portfolio / Ana-style artifacts */
export function extractGrowthTerminalValueP50(artifacts) {
  if (!artifacts || typeof artifacts !== "object") return null;
  const scenarios = artifacts.scenarios;
  if (!Array.isArray(scenarios) || scenarios.length === 0) return null;
  const mid = scenarios.length >= 3 ? scenarios[1] : scenarios[0];
  const mc = mid?.monte_carlo || {};
  const raw = mc.terminal_value_p50;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Same scenario index as growth helper; probability 0–1 (or 0–100) that paths last through retirement. */
/**
 * Goal funded % (0–100): latest growth portfolio value ÷ median portfolio value at retirement
 * (MC terminal P50) that was frozen when the life scenario was saved. Capped at 100%.
 */
export function computeGoalFundedPercent(frozenMedianAtRetirement, currentPortfolioValueUsd) {
  if (
    frozenMedianAtRetirement == null ||
    !Number.isFinite(Number(frozenMedianAtRetirement)) ||
    Number(frozenMedianAtRetirement) <= 0
  ) {
    return null;
  }
  if (
    currentPortfolioValueUsd == null ||
    !Number.isFinite(Number(currentPortfolioValueUsd)) ||
    Number(currentPortfolioValueUsd) <= 0
  ) {
    return null;
  }
  return Math.min(100, Math.max(0, (Number(currentPortfolioValueUsd) / Number(frozenMedianAtRetirement)) * 100));
}

export function extractRetirementProbabilityOfSuccess(artifacts) {
  if (!artifacts || typeof artifacts !== "object") return null;
  const scenarios = artifacts.scenarios;
  if (!Array.isArray(scenarios) || scenarios.length === 0) return null;
  const mid = scenarios.length >= 3 ? scenarios[1] : scenarios[0];
  const mc = mid?.monte_carlo || {};
  const raw = mc.probability_of_success;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  if (n > 1) return Math.min(100, n) / 100;
  if (n < 0) return null;
  return n;
}

/** Retirement Monte Carlo success as 0–100 for UI / persistence (moderate scenario slot when triple). */
export function extractRetirementSuccessPercentForDial(artifacts) {
  const frac = extractRetirementProbabilityOfSuccess(artifacts);
  if (frac == null || !Number.isFinite(frac)) return null;
  const n = Number(frac);
  const pct = n <= 1 ? n * 100 : Math.min(100, n);
  return Math.min(100, Math.max(0, pct));
}

/** Format dollars like intake initial_value strings (M / K). */
export function formatUsdForFormField(n) {
  if (n == null || !Number.isFinite(n) || n <= 0) return "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) {
    const m = n / 1_000_000;
    const s = (Math.round(m * 1000) / 1000).toString().replace(/\.?0+$/, "");
    return `${s}M`;
  }
  if (abs >= 1000) {
    const k = Math.round(n / 1000);
    return `${k}K`;
  }
  return String(Math.round(n));
}

/**
 * After growth backtest: set retirement initial portfolio to growth MC median (P50) at horizon only.
 * Other retirement intake fields stay as hydrated from the dropped retirement item / user edits.
 * @param {Record<string, unknown>} _growthForm - reserved for callers; not merged into retirement
 * @param {Record<string, unknown>} retireForm - React form state (retirement)
 * @param {number | null} terminalP50
 */
export function mergeRetirementFormAfterGrowthBacktest(_growthForm, retireForm, terminalP50) {
  const r = retireForm && typeof retireForm === "object" ? { ...retireForm } : {};
  if (terminalP50 != null && Number.isFinite(terminalP50)) {
    r.investmentValue = formatUsdForFormField(terminalP50);
  }
  return r;
}
