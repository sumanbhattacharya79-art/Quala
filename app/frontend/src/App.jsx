import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  postJson,
  putJson,
  getJson,
  deleteJson,
  fetchPersistedBacktestArtifacts,
  resolveApiUrl,
  SESSION_KEY,
  USER_ID_KEY,
  USER_EMAIL_KEY,
  FORM_STATE_KEY,
  parseAmount,
  parseDisplayUnit,
  parseHorizonYears,
  inferHorizonYears,
  parseExpenses,
  expensesFromBigSpendingRows,
  bigSpendingNarrativeFromRows,
  spendingFieldDeclaresOneTimeOutflows,
} from "./api";

/** Attach logged-in user for server-side Gemini token accounting on /api/chat/money-manager. */
function appendMoneyManagerUserId(payload, explicitUserId) {
  const uid =
    (explicitUserId && String(explicitUserId).trim()) ||
    (typeof localStorage !== "undefined" ? localStorage.getItem(USER_ID_KEY) : null);
  if (uid) payload.user_id = uid;
}

const THEME_STORAGE_KEY = "portfolio-optimizer-theme";
const COMPARE_SBS_KEY_PREFIX = "portfolio-optimizer:compare-sbs:";
const COMPARE_CONNECT_KEY_PREFIX = "portfolio-optimizer:compare-connect:";
function compareSbsStorageKey(uid) {
  return `${COMPARE_SBS_KEY_PREFIX}${uid}`;
}
function compareConnectStorageKey(uid) {
  return `${COMPARE_CONNECT_KEY_PREFIX}${uid}`;
}
function readPersistedUid() {
  try {
    return typeof localStorage !== "undefined" ? localStorage.getItem(USER_ID_KEY) : null;
  } catch {
    return null;
  }
}
function readPersistedSbs(uid) {
  if (!uid || typeof localStorage === "undefined") return { left: null, right: null };
  try {
    const raw = localStorage.getItem(compareSbsStorageKey(uid));
    if (!raw) return { left: null, right: null };
    const p = JSON.parse(raw);
    return { left: p.left ?? null, right: p.right ?? null };
  } catch {
    return { left: null, right: null };
  }
}
function readPersistedConnect(uid) {
  if (!uid || typeof localStorage === "undefined") return { left: null, right: null };
  try {
    const raw = localStorage.getItem(compareConnectStorageKey(uid));
    if (!raw) return { left: null, right: null };
    const p = JSON.parse(raw);
    return { left: p.left ?? null, right: p.right ?? null };
  } catch {
    return { left: null, right: null };
  }
}
import { ChartContainer } from "./ChartContainer";
import { PortfolioValueHistoryChart } from "./PortfolioValueHistoryChart.jsx";
import { CompareView } from "./CompareView.jsx";
import {
  compareSelFromDragPayload,
  connectSelectionKey,
  validateLifePlannerPick,
  validateSameCategoryPick,
} from "./comparePortfolioPick.js";
import { SameCategoryComparePanel } from "./SameCategoryComparePanel.jsx";
import { BigSpendingUpcomingEditor } from "./BigSpendingUpcomingEditor.jsx";
import {
  compareBacktestArtifactsReady,
  normalizeBacktestArtifacts,
  computeGoalFundedPercent,
  extractGrowthTerminalValueP50,
  extractRetirementSuccessPercentForDial,
  mergeRetirementFormAfterGrowthBacktest,
} from "./compareGrowthRetireBridge.js";
import { LifePlannerDials } from "./LifePlannerDials.jsx";
import { NetWorthPanel } from "./NetWorthPanel.jsx";
import { MrBrownChat } from "./MrBrownChat.jsx";
import QUALA_TNC_FULL_TEXT from "./legal/terms-conditions.md?raw";
import { QUALA_TNC_VERSION } from "./legalConstants.js";
import { isGoogleSignInConfigured, mountGoogleSignInButton } from "./googleSignIn.js";
import { AdvisorModelOutputDisclaimer } from "./advisorDisclaimer.jsx";
import { LegalStickyFooter } from "./legalFooter.jsx";
import { AboutUsModalBody } from "./AboutUsModalBody.jsx";
import { MOBILE_MAX_WIDTH_PX, readIsMobileViewport } from "./useMobileViewport.js";

function useSessionId() {
  // New session ID on every load: never read or write session to localStorage so it cannot persist
  const [sessionId, setSessionIdState] = useState(() => crypto.randomUUID());
  useEffect(() => {
    localStorage.removeItem(SESSION_KEY);
  }, []);
  // When page is restored from bfcache, get a new session so ID always changes after "coming back"
  useEffect(() => {
    const onPageShow = (e) => {
      if (e.persisted) {
        setSessionIdState(crypto.randomUUID());
        localStorage.removeItem(SESSION_KEY);
      }
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);
  // Do NOT sync session_id from backend — we keep the id we generated so it never reverts to an old one
  const syncFromResponse = () => {};
  const startNewSession = useCallback(() => {
    const newId = crypto.randomUUID();
    localStorage.removeItem(SESSION_KEY);
    setSessionIdState(newId);
    return newId;
  }, []);
  return [sessionId, syncFromResponse, startNewSession];
}

function TypingDots() {
  return (
    <div style={{ display: "flex", gap: "5px", padding: "4px 0", alignItems: "center" }}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: "#c8a96e",
            display: "inline-block",
            animation: `typingBounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

function renderText(text) {
  if (!text || typeof text !== "string") return "";
  const cleaned = text
    .replace(/<<<PORTFOLIOS_JSON>>>[\s\S]*?<<<END_PORTFOLIOS_JSON>>>/g, "")
    .replace(/```\s*json\s*```/gi, "")
    .trim();
  return cleaned.split(/\*\*(.*?)\*\*/g).map((part, i) =>
    i % 2 === 1 ? <strong key={i} style={{ color: "#c8a96e" }}>{part}</strong> : part
  );
}

function portfolioSavedDescriptionText(intake) {
  if (!intake || typeof intake !== "object") return "";
  const sd = String(intake.save_description || "").trim();
  if (sd) return sd;
  return String(intake.other_notes || "").trim();
}

function _normalizeCompositionKeys(obj) {
  if (!obj || typeof obj !== "object") return null;
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = String(k).trim().toUpperCase();
    if (!key) continue;
    const fv = typeof v === "number" ? v : parseFloat(v);
    if (!Number.isFinite(fv)) continue;
    out[key] = (out[key] || 0) + fv;
  }
  return Object.keys(out).length ? out : null;
}

function compositionsRoughlyEqual(a, b, tol = 1e-4) {
  const na = _normalizeCompositionKeys(a);
  const nb = _normalizeCompositionKeys(b);
  if (!na || !nb) return false;
  const keys = new Set([...Object.keys(na), ...Object.keys(nb)]);
  for (const k of keys) {
    const va = na[k] || 0;
    const vb = nb[k] || 0;
    if (Math.abs(va - vb) > tol) return false;
  }
  return true;
}

/** Sector/industry maps from the latest assistant artifacts that match this ticker composition. */
function sectorIndustryWeightsForSave(messages, composition) {
  let sectors = null;
  let industries = null;
  if (!composition || typeof composition !== "object") return { sectors, industries };

  const tryPortfolioPayload = (p) => {
    if (!p || typeof p !== "object") return;
    const tickers = p.tickers && typeof p.tickers === "object" ? p.tickers : p;
    if (!compositionsRoughlyEqual(tickers, composition)) return;
    if (p.sectors && typeof p.sectors === "object" && Object.keys(p.sectors).length) sectors = { ...p.sectors };
    if (p.industries && typeof p.industries === "object" && Object.keys(p.industries).length)
      industries = { ...p.industries };
  };

  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.artifacts) continue;
    const art = m.artifacts;
    if (art.portfolio_composition && compositionsRoughlyEqual(art.portfolio_composition, composition)) {
      if (art.portfolio_sectors && typeof art.portfolio_sectors === "object" && Object.keys(art.portfolio_sectors).length)
        sectors = art.portfolio_sectors;
      if (art.portfolio_industries && typeof art.portfolio_industries === "object" && Object.keys(art.portfolio_industries).length)
        industries = art.portfolio_industries;
      if (sectors || industries) return { sectors, industries };
    }
    const ap = art.all_portfolios;
    if (ap && typeof ap === "object") {
      for (const p of Object.values(ap)) {
        tryPortfolioPayload(p);
        if (sectors || industries) return { sectors, industries };
      }
    }
  }
  return { sectors, industries };
}

/** Same pattern as monthly what-if: what it does, then labeled fields (see BigSpendingUpcomingEditor). */
const WHATIF_ONE_TIME_INFLOW_HINT = "Adds cash to the portfolio once at that timing.";

/** Field-by-field help under monthly inflow/outflow rows; retirement and growth use the same clauses. */
const WHATIF_MONTHLY_ROW_FIELDS_HINT =
  "Amount: monthly dollars. Start age: your age when this line starts. End age: your age when it ends; leave blank to run through age 100. YoY % (optional): real change (inflation rate already included) (negative allowed). Label (optional): your note.";

function yoyNarrativeSuffix(yoyPct) {
  const t = String(yoyPct ?? "").trim().replace(/%/g, "").replace(/,/g, "");
  if (!t || t === "-" || t === "+") return "";
  const n = parseFloat(t);
  if (!Number.isFinite(n) || n === 0) return "";
  return ` YoY ${n}%`;
}

const INITIAL_MESSAGES = [
  {
    id: 1,
    role: "assistant",
    text: "Hi, I'm Quala — your AI assistant for researching and simulating portfolio strategies. I can help you explore growth allocations, stress-test ideas with backtests and Monte Carlo, and refine allocations step by step. Panda can help you build a retirement portfolio once you've saved a growth portfolio. First, I need a few details from you.",
    time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  },
];

const defaultFormState = {
  retirementStatus: "both_working", // self_retired | partner_retired | both_retired | both_working
  planningFor: "self", // only shown when both_working
  birthYear1: "",
  birthMonth1: "",
  birthYear2: "",
  birthMonth2: "",
  retirementTimelineSelf: "",
  retirementTimelinePartner: "",
  country: "USA",
  state: "",
  inflationAssumption: "3",
  risk: "medium",
  spending: "",
  bigSpendingRows: [{ amount: "", years: "", label: "" }],
  investmentValue: "",
  monthlyContribution: "",
  monthlyExpense: "",
  otherNotes: "",
  /** Retirement what-if: structured rows — backend: retirement_income_rows / retirement_misc_spending_rows */
  monthlyIncomeRows: [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
  miscMonthlySpendingRows: [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
  /** One-time inflows — structured like big spending; backend merges as negative cashflow entries in MC */
  windfallInflowRows: [{ amount: "", years: "", label: "" }],
  /** Growth portfolio what-if — extra invest + misc monthly outflows (same row shape as retirement misc) */
  growthMonthlyIncomeRows: [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
  growthMonthlyIncomeFreeform: "",
  growthMiscMonthlySpendingRows: [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
  growthOneTimeInflowRows: [{ amount: "", years: "", label: "" }],
  /** Retirement what-if: effective tax rate on withdrawals (percent, e.g. 20 = 20%). Default 0%. */
  retirementEffectiveTaxRate: "0",
  /** Optional: extra monthly spend each retirement year (after year 1) if prior year's total return ≥ threshold %. */
  retirementDiscretionaryMonthly: "",
  retirementDiscretionaryMinPriorReturnPct: "",
  retirementDiscretionaryStartAge: "",
  retirementDiscretionaryEndAge: "",
};

/** Inline hint when Continue is blocked by missing required (*) intake fields. */
const INTAKE_REQUIRED_FIELDS_MESSAGE = "Please fill out the required (*) fields.";

/** What-if fields: never persist to localStorage or portfolio intake; always reset on load/refresh. */
const WHAT_IF_FORM_KEYS = [
  "monthlyIncomeRows",
  "miscMonthlySpendingRows",
  "windfallInflowRows",
  "growthMonthlyIncomeRows",
  "growthMonthlyIncomeFreeform",
  "growthMiscMonthlySpendingRows",
  "growthOneTimeInflowRows",
  "retirementEffectiveTaxRate",
  "retirementDiscretionaryMonthly",
  "retirementDiscretionaryMinPriorReturnPct",
  "retirementDiscretionaryStartAge",
  "retirementDiscretionaryEndAge",
];
const WHAT_IF_INTAKE_KEYS = [
  "retirement_income_rows",
  "retirement_misc_spending_rows",
  "windfall_spending",
  "windfall_inflow_rows",
  "growth_monthly_income_rows",
  "growth_monthly_income_freeform",
  "growth_misc_spending_rows",
  "growth_misc_spending_freeform",
  "growth_one_time_inflow_freeform",
  "growth_one_time_inflow_rows",
  "retirement_effective_tax_rate",
  "retirement_discretionary_monthly",
  "retirement_discretionary_in_year",
  "retirement_discretionary_min_prior_year_return_pct",
  "retirement_discretionary_start_age",
  "retirement_discretionary_end_age",
];

function formStateWithoutWhatIf(state) {
  const out = { ...state };
  for (const k of WHAT_IF_FORM_KEYS) delete out[k];
  return out;
}

function intakeWithoutWhatIf(intake) {
  if (!intake || typeof intake !== "object") return intake;
  const out = { ...intake };
  for (const k of WHAT_IF_INTAKE_KEYS) delete out[k];
  return out;
}

/** Whether "I plan to retire in" is shown and required for the current retirement status. */
function retirementTimelineSelfRequired(state) {
  const planningFor = state.planningFor || "self";
  const rs = state.retirementStatus || "both_working";
  if (rs === "both_retired") return false;
  if (planningFor === "self") {
    if (rs === "self_retired") return false;
    return rs === "both_working";
  }
  return planningFor === "couple" && ["partner_retired", "both_working"].includes(rs);
}

/** Whether "Partner plans to retire in" is shown and required. */
function retirementTimelinePartnerRequired(state) {
  const planningFor = state.planningFor || "self";
  const rs = state.retirementStatus || "both_working";
  if (planningFor !== "couple" || rs === "both_retired") return false;
  return rs === "self_retired" || rs === "both_working";
}

/**
 * Required fields before intake Continue (anonymous or logged-in profile).
 * @returns {{ ok: true } | { ok: false, message: string }}
 */
function validateMandatoryIntakeFields(state) {
  const missing = [];
  const planningFor = state.planningFor || "self";
  const y1 = parseInt(state.birthYear1, 10);
  const m1 = parseInt(state.birthMonth1, 10);
  if (!(y1 >= 1920 && y1 <= 2010 && m1 >= 1 && m1 <= 12)) {
    missing.push("Your birth date (year and month)");
  }
  if (planningFor === "couple") {
    const y2 = parseInt(state.birthYear2, 10);
    const m2 = parseInt(state.birthMonth2, 10);
    if (!(y2 >= 1920 && y2 <= 2010 && m2 >= 1 && m2 <= 12)) {
      missing.push("Partner birth date (year and month)");
    }
  }
  if (retirementTimelineSelfRequired(state)) {
    const t = state.retirementTimelineSelf?.trim() || "";
    if (!t || parseHorizonYears(t) == null) {
      missing.push("I plan to retire in (e.g. 10 years)");
    }
  }
  if (retirementTimelinePartnerRequired(state)) {
    const t = state.retirementTimelinePartner?.trim() || "";
    if (!t || parseHorizonYears(t) == null) {
      missing.push("Partner plans to retire in (e.g. 8 years)");
    }
  }
  if (!String(state.state ?? "").trim()) {
    missing.push("State");
  }
  const invTrim = String(state.investmentValue ?? "").trim();
  if (!invTrim || parseAmount(invTrim) <= 0) {
    missing.push("Initial investment amount");
  }
  if (!String(state.monthlyExpense ?? "").trim()) {
    missing.push("Monthly expense");
  }
  if (!String(state.monthlyContribution ?? "").trim()) {
    missing.push("Monthly savings");
  }
  if (missing.length === 0) return { ok: true };
  const list = missing.map((m) => `• ${m}`).join("\n");
  return {
    ok: false,
    message: `Please fill in the following required fields:\n${list}`,
  };
}

/** One-time migration: old UI stored misc as miscSpendingRows or freeform. */
function migrateLegacyMiscSpendingRowsInFormState(parsed) {
  const next = { ...parsed };
  if (Array.isArray(next.miscSpendingRows) && next.miscSpendingRows.length) {
    next.miscMonthlySpendingRows = next.miscSpendingRows.map((r) => ({
      monthly: r?.monthly != null ? String(r.monthly) : "",
      startAge: r?.start_age != null ? String(r.start_age) : (r?.startAge != null ? String(r.startAge) : ""),
      endAge: r?.end_age != null ? String(r.end_age) : (r?.endAge != null ? String(r.endAge) : ""),
      yoyPct: r?.yoyPct != null ? String(r.yoyPct) : (r?.yoy_annual_pct != null ? String(r.yoy_annual_pct) : ""),
      label: r?.label != null ? String(r.label) : "",
    }));
  }
  delete next.miscSpendingRows;
  if (!Array.isArray(next.monthlyIncomeRows) || !next.monthlyIncomeRows.length) {
    next.monthlyIncomeRows = [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
  }
  if (!Array.isArray(next.miscMonthlySpendingRows) || !next.miscMonthlySpendingRows.length) {
    next.miscMonthlySpendingRows = [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
  }
  if (!Array.isArray(next.growthMonthlyIncomeRows) || !next.growthMonthlyIncomeRows.length) {
    next.growthMonthlyIncomeRows = [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
  }
  if (!Array.isArray(next.growthMiscMonthlySpendingRows) || !next.growthMiscMonthlySpendingRows.length) {
    next.growthMiscMonthlySpendingRows = [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
  }
  const _bsRows = Array.isArray(next.bigSpendingRows) ? next.bigSpendingRows : [];
  // Do not auto-derive structured big-spending rows from free-form `spending` text on refresh.
  // Keep explicit rows only; default to a single empty row.
  next.bigSpendingRows = _bsRows.length ? _bsRows : [{ amount: "", years: "", label: "" }];

  const _migrateInflowRows = (rowsKey, legacyTextKey) => {
    const cur = Array.isArray(next[rowsKey]) ? next[rowsKey] : [];
    const has = cur.some((r) => String(r?.amount ?? "").trim() && String(r?.years ?? "").trim());
    if (!has && next[legacyTextKey]?.trim()) {
      const exp = parseExpenses(String(next[legacyTextKey]));
      if (exp.length) {
        next[rowsKey] = exp.map((e) => ({
          amount:
            e.amount >= 1_000_000
              ? `${e.amount / 1_000_000}M`.replace(/\.0+M$/, "M")
              : e.amount >= 1000
                ? `${e.amount / 1000}K`.replace(/\.0+K$/, "K")
                : String(Math.round(e.amount)),
          years: String(e.years),
          label: "",
        }));
      } else {
        next[rowsKey] = [{ amount: "", years: "", label: "" }];
      }
    } else if (!cur.length) {
      next[rowsKey] = [{ amount: "", years: "", label: "" }];
    }
    delete next[legacyTextKey];
  };
  _migrateInflowRows("windfallInflowRows", "windfallSpending");
  _migrateInflowRows("growthOneTimeInflowRows", "growthOneTimeInflowFreeform");

  for (const mk of ["monthlyIncomeRows", "miscMonthlySpendingRows", "growthMonthlyIncomeRows", "growthMiscMonthlySpendingRows"]) {
    if (Array.isArray(next[mk])) {
      next[mk] = next[mk].map((r) => ({
        monthly: r?.monthly != null ? String(r.monthly) : "",
        startAge: r?.startAge != null ? String(r.startAge) : "",
        endAge: r?.endAge != null ? String(r.endAge) : "",
        yoyPct: r?.yoyPct != null ? String(r.yoyPct) : (r?.yoy_annual_pct != null ? String(r.yoy_annual_pct) : ""),
        label: r?.label != null ? String(r.label) : "",
      }));
    }
  }
  return next;
}

function spendingTextFromFormState(state) {
  const line = bigSpendingNarrativeFromRows(state.bigSpendingRows);
  if (line) return line;
  const s = state.spending?.trim() || "";
  if (!s) return undefined;
  if (!spendingFieldDeclaresOneTimeOutflows(s)) return undefined;
  if (parseExpenses(s).length > 10) return undefined;
  return s;
}

function _amountStrFromSavedNumber(amt) {
  if (!Number.isFinite(amt) || amt <= 0) return "";
  return amt >= 1_000_000
    ? `${parseFloat((amt / 1_000_000).toFixed(4))}M`.replace(/\.0+M$/, "M")
    : amt >= 1000
      ? `${parseFloat((amt / 1000).toFixed(4))}K`.replace(/\.0+K$/, "K")
      : String(Math.round(amt));
}

function inflowRowsFromIntakeSnapshot(intake, rowsKey, textKey) {
  const raw = intake[rowsKey];
  if (Array.isArray(raw) && raw.length) {
    return raw.map((e) => {
      if (!e || typeof e !== "object") return { amount: "", years: "", label: "" };
      const amt = Number(e.amount);
      return {
        amount: _amountStrFromSavedNumber(amt),
        years: e.years != null ? String(e.years) : "",
        label: e.label != null ? String(e.label) : "",
      };
    });
  }
  const t = intake[textKey];
  if (t && String(t).trim()) {
    const exp = parseExpenses(String(t));
    if (exp.length) {
      return exp.map((e) => ({
        amount: _amountStrFromSavedNumber(e.amount),
        years: String(e.years),
        label: "",
      }));
    }
  }
  return [{ amount: "", years: "", label: "" }];
}

/** Same long narrative as Continue on the intake form (for chat + API context). */
function buildFullIntakeNarrativeFromFormState(state) {
  const _whatIfAgeNarr = (v) => {
    const s = String(v ?? "").trim();
    if (!s) return null;
    const n = parseInt(s, 10);
    return Number.isFinite(n) && n >= 0 && n <= 120 ? n : null;
  };
  const risk = state.risk?.trim() || "medium risk tolerance";
  const retirementLabels = {
    self_retired: "I am already retired",
    partner_retired: "My partner is already retired",
    both_retired: "We are both retired",
    both_working: state.planningFor === "self" ? "I am working" : "Both are working",
  };
  const retirementStatus = retirementLabels[state.retirementStatus] || state.retirementStatus;
  const retirementTimelineSelf = state.retirementTimelineSelf?.trim() || "not specified";
  const retirementTimelinePartner = state.retirementTimelinePartner?.trim() || "not specified";
  const planningFor = state.planningFor || "self";
  const userWorking = state.retirementStatus === "both_working" || state.retirementStatus === "partner_retired";
  const partnerWorking = state.retirementStatus === "both_working" || state.retirementStatus === "self_retired";
  const timelineLines = [];
  if (userWorking) timelineLines.push(`I plan to retire in ${retirementTimelineSelf}.`);
  if (planningFor === "couple" && partnerWorking) {
    timelineLines.push(`Partner plans to retire in ${retirementTimelinePartner}.`);
  }
  if (!timelineLines.length) timelineLines.push("Both already retired.");
  const location = ["USA", state.state?.trim()].filter(Boolean).join(", ") || "USA";
  const spending = spendingTextFromFormState(state)?.trim() || "no big spending expected";
  const investmentValue = state.investmentValue?.trim() || "1000";
  const monthlyContribution = state.monthlyContribution?.trim() || "no monthly contributions";
  const otherNotes = state.otherNotes?.trim() || "none";
  const monthlyExpense = state.monthlyExpense?.trim() || "not specified";
  const inflationPct = state.inflationAssumption?.trim() || "3";
  const rwiParts = [];
  const incomeRows = (state.monthlyIncomeRows || []).filter((r) => (r?.monthly ?? "").toString().trim());
  if (incomeRows.length) {
    rwiParts.push(
      `Monthly income: ${incomeRows.map((r) => {
        const lb = (r?.label ?? "").toString().trim();
        return `$${r.monthly}/mo age ${r.startAge || "?"}-${r.endAge || "100"}${yoyNarrativeSuffix(r.yoyPct)}${lb ? ` (${lb})` : ""}`;
      }).join(", ")}`,
    );
  }
  const spendRows = (state.miscMonthlySpendingRows || []).filter((r) => (r?.monthly ?? "").toString().trim());
  if (spendRows.length) {
    rwiParts.push(
      `Misc monthly spending: ${spendRows.map((r) => {
        const lb = (r?.label ?? "").toString().trim();
        return `$${r.monthly}/mo age ${r.startAge || "?"}-${r.endAge || "100"}${yoyNarrativeSuffix(r.yoyPct)}${lb ? ` (${lb})` : ""}`;
      }).join(", ")}`,
    );
  }
  {
    const rdM = parseAmount(String(state.retirementDiscretionaryMonthly ?? "").trim());
    const rdP = parseFloat(String(state.retirementDiscretionaryMinPriorReturnPct ?? "").trim());
    const rdSa = parseInt(String(state.retirementDiscretionaryStartAge ?? "").trim(), 10);
    const rdEa = parseInt(String(state.retirementDiscretionaryEndAge ?? "").trim(), 10);
    const rdAgeWin =
      Number.isFinite(rdSa) &&
      Number.isFinite(rdEa) &&
      rdSa >= 0 &&
      rdEa <= 120 &&
      rdSa <= rdEa
        ? ` from age ${rdSa}–${rdEa}`
        : "";
    if (rdM != null && rdM > 0 && Number.isFinite(rdP)) {
      rwiParts.push(
        `Discretionary spending: up to $${rdM}/mo${rdAgeWin} in each eligible retirement year (after the first) when prior-year total portfolio return ≥ ${rdP}%`,
      );
    }
  }
  const wfNarr = bigSpendingNarrativeFromRows(state.windfallInflowRows);
  if (wfNarr) rwiParts.push(`One-time inflow (windfall): ${wfNarr}`);
  const growthIncomeRows = (state.growthMonthlyIncomeRows || []).filter((r) => (r?.monthly ?? "").toString().trim());
  if (growthIncomeRows.length) {
    rwiParts.push(
      `Growth extra monthly income: ${growthIncomeRows.map((r) => {
        const lb = (r?.label ?? "").toString().trim();
        return `$${r.monthly}/mo age ${r.startAge || "?"}-${r.endAge || "100"}${yoyNarrativeSuffix(r.yoyPct)}${lb ? ` (${lb})` : ""}`;
      }).join(", ")}`,
    );
  } else if (state.growthMonthlyIncomeFreeform?.trim()) {
    rwiParts.push(`Growth extra monthly income (to invest): ${state.growthMonthlyIncomeFreeform.trim()}`);
  }
  const growthMiscRows = (state.growthMiscMonthlySpendingRows || []).filter((r) => (r?.monthly ?? "").toString().trim());
  if (growthMiscRows.length) {
    rwiParts.push(
      `Growth misc monthly spending: ${growthMiscRows.map((r) => {
        const lb = (r?.label ?? "").toString().trim();
        return `$${r.monthly}/mo age ${r.startAge || "?"}-${r.endAge || "100"}${yoyNarrativeSuffix(r.yoyPct)}${lb ? ` (${lb})` : ""}`;
      }).join(", ")}`,
    );
  }
  const gtiNarr = bigSpendingNarrativeFromRows(state.growthOneTimeInflowRows);
  if (gtiNarr) rwiParts.push(`Growth one-time inflow: ${gtiNarr}`);
  const lines = [
    `Retirement status: ${retirementStatus}.`,
    `Planning for: ${planningFor === "couple" ? "the two of us" : "myself"}.`,
    ...timelineLines,
    `Location: ${location}.`,
    `Inflation assumption: ${inflationPct}%.`,
    `Risk appetite: ${risk}.`,
    `Big spending: ${spending}.`,
    `Current investment value: ${investmentValue}.`,
    `Monthly investment contributions: ${monthlyContribution}.`,
    `Current monthly expense: ${monthlyExpense} (used as monthly withdrawal in retirement).`,
    `Any other thing you want me to know: ${otherNotes}.`,
  ];
  if (rwiParts.length) {
    const stripDot = (s) => String(s).trim().replace(/\.\s*$/, "");
    lines.push(`Retirement what-if: ${stripDot(rwiParts[0])}.`);
    rwiParts.slice(1).forEach((p) => {
      const t = stripDot(p);
      if (t) lines.push(`${t}.`);
    });
  }
  return lines.join("\n");
}

/** Map API intake (user_intake or per-portfolio snapshot) to form fields. */
function formStateFromIntakeApi(intake) {
  if (!intake || typeof intake !== "object") return null;
  const structuredBigSpending = Array.isArray(intake.big_spending_rows)
    ? intake.big_spending_rows
        .map((r) => ({
          amount: r?.amount != null ? String(r.amount) : "",
          years: r?.years != null ? String(r.years) : "",
          label: r?.label != null ? String(r.label) : "",
        }))
        .filter((r) => r.amount.trim() || r.years.trim() || r.label.trim())
    : [];
  let legacySpending = String(intake.spending || "").trim();
  if (!spendingFieldDeclaresOneTimeOutflows(legacySpending)) {
    legacySpending = "";
  }
  let bigSpendingRowsHydrated = structuredBigSpending.length
    ? structuredBigSpending
    : [{ amount: "", years: "", label: "" }];
  if (!structuredBigSpending.length && legacySpending) {
    const exp = parseExpenses(legacySpending);
    if (exp.length > 10) {
      legacySpending = "";
    } else if (exp.length > 0) {
      bigSpendingRowsHydrated = exp.map((e) => ({
        amount:
          e.amount >= 1_000_000
            ? `${e.amount / 1_000_000}M`.replace(/\.0+M$/, "M")
            : e.amount >= 1000
              ? `${e.amount / 1000}K`.replace(/\.0+K$/, "K")
              : String(Math.round(e.amount)),
        years: String(e.years),
        label: "",
      }));
      legacySpending = "";
    }
  }
  return {
    ...defaultFormState,
    birthYear1: intake.birth_dates?.[0]?.year != null ? String(intake.birth_dates[0].year) : "",
    birthMonth1: intake.birth_dates?.[0]?.month != null ? String(intake.birth_dates[0].month) : "",
    birthYear2: intake.birth_dates?.[1]?.year != null ? String(intake.birth_dates[1].year) : "",
    birthMonth2: intake.birth_dates?.[1]?.month != null ? String(intake.birth_dates[1].month) : "",
    planningFor: intake.planning_for || "self",
    retirementStatus: intake.retirement_status || "both_working",
    retirementTimelineSelf: intake.retirement_timeline_self || "",
    retirementTimelinePartner: intake.retirement_timeline_partner || "",
    state: intake.state || "",
    inflationAssumption: String(intake.inflation_assumption ?? 3),
    risk: intake.risk?.trim() || "medium",
    spending: legacySpending,
    bigSpendingRows: bigSpendingRowsHydrated,
    investmentValue: intake.initial_value != null ? String(intake.initial_value) : "",
    monthlyContribution: intake.monthly_savings != null ? String(intake.monthly_savings) : "",
    monthlyExpense: intake.current_monthly_expense != null ? String(intake.current_monthly_expense) : "",
    otherNotes: intake.other_notes || "",
    retirementEffectiveTaxRate: (() => {
      const t = intake.retirement_effective_tax_rate;
      if (t == null || t === "") return "0";
      const n = Number(t);
      if (!Number.isFinite(n)) return "0";
      return String(n > 1 ? Math.round(n) : Math.round(n * 100));
    })(),
    retirementDiscretionaryMonthly:
      intake.retirement_discretionary_monthly != null && intake.retirement_discretionary_monthly !== ""
        ? String(intake.retirement_discretionary_monthly)
        : "",
    retirementDiscretionaryMinPriorReturnPct:
      intake.retirement_discretionary_min_prior_year_return_pct != null &&
      intake.retirement_discretionary_min_prior_year_return_pct !== ""
        ? String(intake.retirement_discretionary_min_prior_year_return_pct)
        : "",
    retirementDiscretionaryStartAge:
      intake.retirement_discretionary_start_age != null && intake.retirement_discretionary_start_age !== ""
        ? String(intake.retirement_discretionary_start_age)
        : "",
    retirementDiscretionaryEndAge:
      intake.retirement_discretionary_end_age != null && intake.retirement_discretionary_end_age !== ""
        ? String(intake.retirement_discretionary_end_age)
        : "",
    monthlyIncomeRows: (() => {
      const rows = intake.retirement_income_rows;
      if (Array.isArray(rows) && rows.length) {
        return rows.map((r) => ({
          monthly: r?.monthly != null ? String(r.monthly) : "",
          startAge: r?.start_age != null ? String(r.start_age) : "",
          endAge: r?.end_age != null && r.end_age !== "" ? String(r.end_age) : "",
          yoyPct: r?.yoy_annual_pct != null && r.yoy_annual_pct !== "" ? String(r.yoy_annual_pct) : "",
          label: r?.label != null ? String(r.label) : "",
        }));
      }
      return [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
    })(),
    miscMonthlySpendingRows: (() => {
      const rows = intake.retirement_misc_spending_rows;
      if (Array.isArray(rows) && rows.length) {
        return rows.map((r) => ({
          monthly: r?.monthly != null ? String(r.monthly) : "",
          startAge: r?.start_age != null ? String(r.start_age) : "",
          endAge: r?.end_age != null && r.end_age !== "" ? String(r.end_age) : "",
          yoyPct: r?.yoy_annual_pct != null && r.yoy_annual_pct !== "" ? String(r.yoy_annual_pct) : "",
          label: r?.label != null ? String(r.label) : "",
        }));
      }
      return [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
    })(),
    windfallInflowRows: (() => {
      let base = inflowRowsFromIntakeSnapshot(intake, "windfall_inflow_rows", "windfall_spending");
      const has = base.some((x) => String(x.amount ?? "").trim() && String(x.years ?? "").trim());
      if (!has && intake.windfall_amount != null && intake.windfall_years != null) {
        const wa = Number(intake.windfall_amount);
        const wy = intake.windfall_years;
        if (Number.isFinite(wa) && wa > 0 && wy !== "" && wy != null) {
          base = [{ amount: String(wa), years: String(wy), label: "" }];
        }
      }
      return base;
    })(),
    growthMonthlyIncomeRows: (() => {
      const rows = intake.growth_monthly_income_rows;
      if (Array.isArray(rows) && rows.length) {
        return rows.map((r) => ({
          monthly: r?.monthly != null ? String(r.monthly) : "",
          startAge: r?.start_age != null ? String(r.start_age) : "",
          endAge: r?.end_age != null && r.end_age !== "" ? String(r.end_age) : "",
          yoyPct: r?.yoy_annual_pct != null && r.yoy_annual_pct !== "" ? String(r.yoy_annual_pct) : "",
          label: r?.label != null ? String(r.label) : "",
        }));
      }
      return [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
    })(),
    growthMonthlyIncomeFreeform: intake.growth_monthly_income_freeform || "",
    growthMiscMonthlySpendingRows: (() => {
      const rows = intake.growth_misc_spending_rows;
      if (Array.isArray(rows) && rows.length) {
        return rows.map((r) => ({
          monthly: r?.monthly != null ? String(r.monthly) : "",
          startAge: r?.start_age != null ? String(r.start_age) : "",
          endAge: r?.end_age != null && r.end_age !== "" ? String(r.end_age) : "",
          yoyPct: r?.yoy_annual_pct != null && r.yoy_annual_pct !== "" ? String(r.yoy_annual_pct) : "",
          label: r?.label != null ? String(r.label) : "",
        }));
      }
      return [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }];
    })(),
    growthOneTimeInflowRows: inflowRowsFromIntakeSnapshot(
      intake,
      "growth_one_time_inflow_rows",
      "growth_one_time_inflow_freeform",
    ),
  };
}

function PricingPlansModalBody() {
  const basicPlanCardRef = useRef(null);
  const scrollToBasic = () => {
    basicPlanCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    basicPlanCardRef.current?.focus();
  };

  return (
    <div className="pricing-plans-modal">
      <div className="pricing-plans-grid">
        <div className="pricing-plan-card">
          <h4 className="pricing-plan-name">Free</h4>
          <p className="pricing-plan-tag">Get started at no cost.</p>
          <ul className="pricing-feature-list" aria-label="Free plan features">
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Create and save portfolio (one)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Net worth monitoring</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Limited AI assistance</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Multiple saved portfolios and scenarios (up to 5)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Lifeplans</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Scenario planning</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Portfolio Rebalancing suggestions</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>AI assistance</span>
            </li>
          </ul>
          <button type="button" className="login-submit-btn pricing-upgrade-btn" onClick={scrollToBasic}>
            Upgrade
          </button>
        </div>
        <div
          className="pricing-plan-card pricing-plan-card--basic"
          ref={basicPlanCardRef}
          tabIndex={-1}
        >
          <h4 className="pricing-plan-name">Basic</h4>
          <p className="pricing-plan-price">$2/month or $20/year</p>
          <ul className="pricing-feature-list" aria-label="Basic plan includes">
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Create and save portfolios and scenarios (up to 5)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Net worth monitoring</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Lifeplans</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Scenario planning</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Portfolio Rebalancing suggestions</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>AI assistance</span>
            </li>
          </ul>
        </div>
      </div>
      <p className="pricing-plans-footnote">
        Free and Basic do not connect to banks or brokerages—you add or upload holdings and keep them current.
      </p>
    </div>
  );
}

/** Static marketing copy for top-bar links (replace or extend as needed). */
const INFO_PAGES = {
  about: {
    title: "About us",
    paragraphs: [],
  },
  faq: {
    title: "FAQ",
    paragraphs: [
      "How does backtesting work? We combine your intake (savings, spending, horizon, and optional what-ifs) with historical returns and Monte Carlo paths so you can see range outcomes, not a single guess.",
      "Is this financial advice? No. This tool is for education and planning; speak with a licensed professional for advice tailored to you.",
      "Where does market data come from? Ticker history is loaded from your project’s data pipeline (e.g. monthly series per symbol). Missing symbols need to be fetched before backtests run.",
    ],
  },
  pricing: {
    title: "Pricing",
    paragraphs: [],
  },
};

/** Life-planner bundle rows use derived scenario_name; sidebar lists a short label without repeating the life name. */
function sidebarScenarioListLabel(scenarioName) {
  const n = (scenarioName || "").trim();
  if (!n) return "Scenario";
  if (/—\s*growth-/i.test(n)) return "Growth · Scenario";
  if (/—\s*retire-/i.test(n)) return "Retirement · Scenario";
  return n;
}

function stripLifePlanPrefixFromScenarioName(scenarioName, lifePlanName) {
  let s = String(scenarioName || "").trim();
  const life = String(lifePlanName || "").trim();
  if (!life || !s) return s;
  const prefixes = [`${life} — `, `${life} – `, `${life} - `, `${life}—`, `${life}–`];
  for (const p of prefixes) {
    if (s.startsWith(p)) return s.slice(p.length).trim();
  }
  return s;
}

function slugNormForLifePlanLabel(s) {
  return String(s || "")
    .trim()
    .toLowerCase()
    .replace(/[^\w-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Saved life plan page headers: no "{life} — " prefix; prefer portfolio name when it matches the stripped scenario tail (e.g. "retire 1" vs "retire-1"). */
function displayLabelForSavedLifePlanSide(scenarioName, portfolioName, lifePlanName) {
  const p = String(portfolioName || "").trim();
  const stripped = stripLifePlanPrefixFromScenarioName(scenarioName, lifePlanName);
  const rawScenario = String(scenarioName || "").trim();
  if (p && stripped && slugNormForLifePlanLabel(p) === slugNormForLifePlanLabel(stripped)) {
    return p;
  }
  if (stripped) return stripped;
  return p || rawScenario;
}

/** True when ChartContainer will render (allocation / scenario charts). */
function artifactsHaveInlineCharts(artifacts) {
  if (!artifacts || typeof artifacts !== "object") return false;
  return !!(
    (artifacts.all_portfolios && Object.keys(artifacts.all_portfolios).length) ||
    (artifacts.portfolio_composition && Object.keys(artifacts.portfolio_composition).length) ||
    (artifacts.scenarios && artifacts.scenarios.length)
  );
}

/** Footnote under assistant bubbles that include scenario / allocation charts (Quala, Panda, Ana, Emu). */
function showAdvisorOutputDisclaimer(msg) {
  if (msg.role !== "assistant" || !msg.artifacts || !artifactsHaveInlineCharts(msg.artifacts)) return false;
  const a = msg.agent;
  if (a === undefined || a === null) return true;
  return a === "Quala" || a === "Panda" || a === "Ana" || a === "Emu";
}

/** Charts first, narrative below — analyst backtests and Quala/Panda 3-option presentations. */
function chartsLeadNarrativeForMessage(msg) {
  return (
    msg.role === "assistant" &&
    msg.artifacts &&
    artifactsHaveInlineCharts(msg.artifacts) &&
    (msg.agent === "Ana" ||
      msg.agent === "Emu" ||
      msg.agent === "Quala" ||
      msg.agent === "Panda")
  );
}

/** Physical assets + linked portfolio values (saved list, else DB snapshot on asset row) − debts. */
function computeSidebarNetWorth(netWorthApiPayload, savedPortfoliosList) {
  if (!netWorthApiPayload || typeof netWorthApiPayload !== "object") return null;
  const assets = Array.isArray(netWorthApiPayload.assets) ? netWorthApiPayload.assets : [];
  const debts = Array.isArray(netWorthApiPayload.debts) ? netWorthApiPayload.debts : [];
  const linked = Array.isArray(netWorthApiPayload.linked_portfolio_ids)
    ? netWorthApiPayload.linked_portfolio_ids
    : [];
  const fromRowLinks = assets.filter((a) => a.portfolio_id).map((a) => String(a.portfolio_id));
  const mergedLinked = [...new Set([...linked.map(String), ...fromRowLinks])];

  let physicalTotal = 0;
  for (const a of assets) {
    if (a.portfolio_id) continue;
    physicalTotal += Math.max(0, Number(a.price ?? a.value ?? 0));
  }

  let investmentsTotal = 0;
  const list = savedPortfoliosList || [];
  for (const idS of mergedLinked) {
    const p = list.find((x) => x.portfolio_id === idS);
    const pv = latestPortfolioUsd(p);
    if (pv != null && Number.isFinite(Number(pv))) {
      investmentsTotal += Number(pv);
      continue;
    }
    const row = assets.find((a) => String(a.portfolio_id) === idS);
    if (row) investmentsTotal += Math.max(0, Number(row.price ?? row.value ?? 0));
  }

  const debtTotal = debts.reduce((s, d) => s + Math.max(0, Number(d.price ?? d.value ?? 0)), 0);
  return physicalTotal + investmentsTotal - debtTotal;
}

function formatSidebarUsd(n) {
  if (n == null || !Number.isFinite(n)) return "";
  const x = Math.abs(n);
  if (x >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (x >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${Math.round(n)}`;
}

/** Prefer latest mark-to-market value from valuation history; fallback to saved portfolio_value. */
function latestPortfolioUsd(p) {
  if (!p || typeof p !== "object") return null;
  const hist = Array.isArray(p.valuation_history) ? p.valuation_history : [];
  const last = hist.length ? hist[hist.length - 1] : null;
  const hv = Number(last?.value);
  if (Number.isFinite(hv)) return hv;
  const pv = Number(p.portfolio_value);
  return Number.isFinite(pv) ? pv : null;
}

const QUICK_SCAN_IMPORTANT_DISCLOSURE =
  "Important disclosure: Quala runs hypothetical portfolio simulations using delayed or third-party market data, which may contain errors or gaps. Nothing here is personalized investment, tax, or legal advice, or an offer to buy or sell securities. You are responsible for verifying all figures and assumptions before making any decisions.";

function SidebarQuickScanDisclosure({ inline = false }) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const [box, setBox] = useState({ top: 0, left: 0, width: 300 });

  const updatePos = () => {
    const el = btnRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const w = Math.min(320, Math.max(260, window.innerWidth - 24));
    let left = r.left;
    if (left + w > window.innerWidth - 12) left = window.innerWidth - w - 12;
    if (left < 12) left = 12;
    setBox({ top: Math.round(r.bottom + 8), left: Math.round(left), width: Math.round(w) });
  };

  useEffect(() => {
    if (!open) return;
    updatePos();
    const onScroll = () => updatePos();
    const onResize = () => updatePos();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    const onDoc = (e) => {
      if (popRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("mousedown", onDoc);
    };
  }, [open]);

  const pop =
    open && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popRef}
            className="quick-scan-disclosure-popover"
            style={{
              position: "fixed",
              top: box.top,
              left: box.left,
              width: box.width,
              zIndex: 10050,
            }}
            role="dialog"
            aria-label="Important disclosure"
          >
            <p className="quick-scan-disclosure-popover__title">Important disclosure</p>
            <p className="quick-scan-disclosure-popover__body">{QUICK_SCAN_IMPORTANT_DISCLOSURE}</p>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className={`quick-scan-disclosure-wrap${inline ? " quick-scan-disclosure-wrap--inline" : ""}`}>
      <button
        type="button"
        className={`quick-scan-disclosure-trigger${inline ? " quick-scan-disclosure-trigger--icon" : ""}`}
        ref={btnRef}
        aria-expanded={open}
        aria-label="Important disclosure"
        title="Important disclosure"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="quick-scan-disclosure-trigger__icon" aria-hidden>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M12 2L4 5v6c0 5.25 3.5 10.09 8 11 4.5-.91 8-5.75 8-11V5l-8-3z"
              stroke="currentColor"
              strokeWidth="1.35"
              strokeLinejoin="round"
            />
            <path d="M9 12h6M12 9v6" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" />
          </svg>
        </span>
        {!inline ? <span className="quick-scan-disclosure-trigger__label">Important disclosure</span> : null}
      </button>
      {pop}
    </div>
  );
}

export default function App() {
  const [sessionId, syncSessionFromResponse, startNewSession] = useSessionId();
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isTyping, setIsTyping] = useState(false);
  const [isMobile, setIsMobile] = useState(readIsMobileViewport);
  const [sidebarOpen, setSidebarOpen] = useState(() => !readIsMobileViewport());
  const [topbarMenuOpen, setTopbarMenuOpen] = useState(false);
  const [theme, setTheme] = useState(() =>
    typeof localStorage !== "undefined" && localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark",
  );
  const [view, setView] = useState(() => (localStorage.getItem(USER_ID_KEY) ? "loggedInOptions" : "intake")); // intake | welcomeOptions | loggedInOptions | chat | refine | auth | analyze | portfolio | compare | netWorth
  const [choiceButtons, setChoiceButtons] = useState(null);
  const [awaitingPortfolios, setAwaitingPortfolios] = useState(false);
  const [waitingForAnalyst, setWaitingForAnalyst] = useState(false);
  // Always start from a blank intake form on hard refresh.
  const [formState, setFormState] = useState(() => ({ ...defaultFormState }));
  const formStateRef = useRef(formState);
  formStateRef.current = formState;
  const [currentIntent, setCurrentIntent] = useState("intake");
  const [savedIntakeMessage, setSavedIntakeMessage] = useState("");
  /** Persists Quala vs Panda path across chat turns (growth | retirement). */
  const activePortfolioFlowRef = useRef(null);
  const portfolioFlowEpochRef = useRef(0);
  const moneyManagerAbortRef = useRef(null);
  const [userFilledIntakeForm, setUserFilledIntakeForm] = useState(false);
  const [intakeFormError, setIntakeFormError] = useState(null);
  const [lastPortfolioComposition, setLastPortfolioComposition] = useState(null);
  const [lastPortfolioSectors, setLastPortfolioSectors] = useState(null);
  const [lastPortfolioIndustries, setLastPortfolioIndustries] = useState(null);
  const [lastPortfolioValue, setLastPortfolioValue] = useState(null);
  const [savedPortfolios, setSavedPortfolios] = useState([]);
  /** Raw GET /api/user/net-worth for sidebar total (recomputed when savedPortfolios updates). */
  const [netWorthSidebarPayload, setNetWorthSidebarPayload] = useState(null);
  /** While Net worth page is open: live total from the panel (updates as rows change, before save). */
  const [netWorthLiveSidebarAmount, setNetWorthLiveSidebarAmount] = useState(null);
  const [userId, setUserId] = useState(() => localStorage.getItem(USER_ID_KEY));
  const [userEmail, setUserEmail] = useState(() => localStorage.getItem(USER_EMAIL_KEY));
  const [accountModalOpen, setAccountModalOpen] = useState(false);
  const [accountModalTab, setAccountModalTab] = useState("login");
  /** Full-page `view === "auth"` (anonymous): initial tab; Cancel returns to `authCancelView` (`intake` | `chat`). */
  const [authScreenDefaultTab, setAuthScreenDefaultTab] = useState("register");
  const [authCancelView, setAuthCancelView] = useState("chat");
  /** After full-page auth login/register (not modal): `chat` e.g. resume save-portfolio flow; else `loggedInOptions`. */
  const [authPostLoginView, setAuthPostLoginView] = useState("loggedInOptions");
  const [infoModal, setInfoModal] = useState(null);
  const [pendingLoggedInAction, setPendingLoggedInAction] = useState(null); // "growth" | "retirement" | null when intake form is shown from logged-in flow
  /** After analyze CSV backtest succeeds, hide "Analyze current portfolio" on welcome until Start over in analyze panel. */
  const [hideAnalyzeWelcomeOption, setHideAnalyzeWelcomeOption] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false); // shown only on saved-portfolio view after save/open, not on logged-in home
  const [selectedPortfolioId, setSelectedPortfolioId] = useState(null);
  const [selectedPortfolioRow, setSelectedPortfolioRow] = useState(null);
  const [portfolioViewData, setPortfolioViewData] = useState(null); // { portfolio, artifacts, agent }
  const [portfolioViewLoading, setPortfolioViewLoading] = useState(false);
  /** idle | fetch (GET saved portfolio) | backtest (POST /backtest — full MC recompute) */
  const [portfolioViewLoadingPhase, setPortfolioViewLoadingPhase] = useState("idle");
  const [showPortfolioDeleteConfirm, setShowPortfolioDeleteConfirm] = useState(false);
  /** Expand ticker/weight table to update saved portfolio composition (not scenarios). */
  const [showPortfolioUpdateComposition, setShowPortfolioUpdateComposition] = useState(false);
  const [portfolioUpdateRows, setPortfolioUpdateRows] = useState([{ ticker: "", weight: "" }]);
  const [portfolioUpdateError, setPortfolioUpdateError] = useState("");
  const [portfolioUpdateSaving, setPortfolioUpdateSaving] = useState(false);
  /** Saved portfolio page: intake frozen until user clicks What-if; then only these are editable: monthly expense, inflation, spending, initial investment. */
  const [portfolioWhatIfMode, setPortfolioWhatIfMode] = useState(false);
  const [portfolioSaveAsName, setPortfolioSaveAsName] = useState("");
  const [portfolioSaveAsSaving, setPortfolioSaveAsSaving] = useState(false);
  /** PUT /api/scenario/:id when viewing a saved scenario (e.g. "retire 1 — …") so what-if edits persist. */
  const [portfolioUpdateScenarioSaving, setPortfolioUpdateScenarioSaving] = useState(false);
  const [savedScenarios, setSavedScenarios] = useState([]);
  const [savedLifeScenarios, setSavedLifeScenarios] = useState([]);
  const [selectedLifeScenarioId, setSelectedLifeScenarioId] = useState(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState(null);
  const [selectedScenarioRow, setSelectedScenarioRow] = useState(null); // { scenario_id, scenario_name, ... } when viewing a scenario
  const [showScenarioDeleteConfirm, setShowScenarioDeleteConfirm] = useState(false);
  const [showLifeScenarioDeleteConfirm, setShowLifeScenarioDeleteConfirm] = useState(false);
  /** Compare: growth (left) + retirement (right); staged backtests with intake handoff. Selections persist per user in localStorage. */
  const [compareLeftSel, setCompareLeftSel] = useState(() => readPersistedConnect(readPersistedUid()).left);
  const [compareRightSel, setCompareRightSel] = useState(() => readPersistedConnect(readPersistedUid()).right);
  const [compareGrowthForm, setCompareGrowthForm] = useState(null);
  const [compareRetireForm, setCompareRetireForm] = useState(null);
  const [compareGrowthArtifacts, setCompareGrowthArtifacts] = useState(null);
  const [compareRetireArtifacts, setCompareRetireArtifacts] = useState(null);
  const [compareHydrating, setCompareHydrating] = useState(false);
  const [compareGrowthRunLoading, setCompareGrowthRunLoading] = useState(false);
  const [compareRetireRunLoading, setCompareRetireRunLoading] = useState(false);
  const [compareRetireSyncMessage, setCompareRetireSyncMessage] = useState(null);
  const [compareNotice, setCompareNotice] = useState(null);
  /** Life planner: single name when saving growth + retirement as one life scenario. */
  const [connectLifeScenarioNameInput, setConnectLifeScenarioNameInput] = useState("");
  const [connectPairScenarioSaving, setConnectPairScenarioSaving] = useState(false);
  const [connectPairScenarioError, setConnectPairScenarioError] = useState(null);
  const [connectPairScenarioSuccess, setConnectPairScenarioSuccess] = useState(null);
  /** True when a life plan is opened from the sidebar: read-only intake and Delete-only toolbar. Stays false after saving from open planner so you can keep editing and re-save. */
  const [connectLifePlannerFrozen, setConnectLifePlannerFrozen] = useState(false);
  const [connectLinkedGrowthScenarioId, setConnectLinkedGrowthScenarioId] = useState(null);
  const [connectLinkedRetirementScenarioId, setConnectLinkedRetirementScenarioId] = useState(null);
  /** Median growth portfolio value at retirement (MC P50) frozen when life scenario was saved; used for goal dial. */
  const [connectFrozenGrowthMedianUsd, setConnectFrozenGrowthMedianUsd] = useState(null);
  /** Same-category compare (growth vs growth or retirement vs retirement). Selections persist per user in localStorage. */
  const [sbsLeftSel, setSbsLeftSel] = useState(() => readPersistedSbs(readPersistedUid()).left);
  const [sbsRightSel, setSbsRightSel] = useState(() => readPersistedSbs(readPersistedUid()).right);
  const [sbsIntakeLeft, setSbsIntakeLeft] = useState(null);
  const [sbsIntakeRight, setSbsIntakeRight] = useState(null);
  const [sbsArtLeft, setSbsArtLeft] = useState(null);
  const [sbsArtRight, setSbsArtRight] = useState(null);
  const [sbsHydrating, setSbsHydrating] = useState(false);
  const [sbsLeftLoading, setSbsLeftLoading] = useState(false);
  const [sbsRightLoading, setSbsRightLoading] = useState(false);
  const [sbsNotice, setSbsNotice] = useState(null);
  const bottomRef = useRef(null);
  const refineChatTextareaRef = useRef(null);
  /** Text box for portfolio refinement (Quala / Panda) after user chooses Keep Refining. */
  const [refineChatOpen, setRefineChatOpen] = useState(false);
  const [refineChatInput, setRefineChatInput] = useState("");
  const [refineChatAdvisor, setRefineChatAdvisor] = useState("Quala");
  /** Monotonic ids so rapid addMessage calls (e.g. quala_handoff + Ana) never share Date.now() and collide on key={msg.id}. */
  const nextMessageIdRef = useRef(2);
  /** After Emu retirement backtest, user chose "Keep refining" — next /money-manager call forces Panda (three portfolios). */
  const retirementRefineAfterEmuRef = useRef(false);
  /** Bumps when user leaves saved-portfolio loading (e.g. opens Net worth) so stale async handlers do not apply results. */
  const portfolioViewLoadTokenRef = useRef(0);
  /** Preserves valuation_history across backtest response (which omits it). */
  const portfolioValuationCacheRef = useRef({ history: [], asOf: null });
  /** Invalidates in-flight Growth→Retirement compare backtests when selections clear or change (avoids stale right-column artifacts). */
  const compareConnectBacktestTokenRef = useRef(0);
  /** Invalidates in-flight same-category compare backtests when selections clear or change. */
  const sbsBacktestTokenRef = useRef(0);
  /** Prevents overlapping sequential backtests while hydrating charts for a frozen opened life plan. */
  const connectChartsHydrateInFlightRef = useRef(false);
  /** Caps retries when artifacts never become chart-ready (avoids a tight loop). */
  const connectChartsHydrateAttemptsRef = useRef({ lifeId: null, count: 0 });
  /** Ignore stale GET /life-scenario responses if the user opened a different plan while in flight. */
  const openLifeScenarioRequestIdRef = useRef(0);
  /** When opening a saved life plan, planner-only intakes (API) override scenario rows for connect forms. */
  const connectPlannerIntakesRef = useRef({ g: null, r: null });
  /** Last growth+retirement pair we finished hydrating (primitive key — stops duplicate intake GET loops). */
  const lastConnectHydrateKeyRef = useRef("");
  const compareLeftSelRef = useRef(compareLeftSel);
  const compareRightSelRef = useRef(compareRightSel);
  compareLeftSelRef.current = compareLeftSel;
  compareRightSelRef.current = compareRightSel;

  /** Skip redundant restore when userId is unchanged (avoids resetting compare selections every render). */
  const prevCompareUserIdRef = useRef(undefined);

  useEffect(() => {
    const prev = prevCompareUserIdRef.current;
    if (userId === prev) return;
    prevCompareUserIdRef.current = userId;
    if (!userId) {
      sbsBacktestTokenRef.current += 1;
      compareConnectBacktestTokenRef.current += 1;
      setSbsLeftSel(null);
      setSbsRightSel(null);
      setSbsIntakeLeft(null);
      setSbsIntakeRight(null);
      setSbsArtLeft(null);
      setSbsArtRight(null);
      setSbsHydrating(false);
      setSbsLeftLoading(false);
      setSbsRightLoading(false);
      setSbsNotice(null);
      setCompareLeftSel(null);
      setCompareRightSel(null);
      setCompareGrowthForm(null);
      setCompareRetireForm(null);
      setCompareGrowthArtifacts(null);
      setCompareRetireArtifacts(null);
      setCompareHydrating(false);
      setCompareGrowthRunLoading(false);
      setCompareRetireRunLoading(false);
      setCompareRetireSyncMessage(null);
      setCompareNotice(null);
      setConnectLifeScenarioNameInput("");
      setSavedLifeScenarios([]);
      setSelectedLifeScenarioId(null);
      setConnectPairScenarioSaving(false);
      setConnectPairScenarioError(null);
      setConnectPairScenarioSuccess(null);
      setConnectLifePlannerFrozen(false);
      setConnectLinkedGrowthScenarioId(null);
      setConnectLinkedRetirementScenarioId(null);
      connectChartsHydrateInFlightRef.current = false;
      connectChartsHydrateAttemptsRef.current = { lifeId: null, count: 0 };
      return;
    }
    const s = readPersistedSbs(userId);
    setSbsLeftSel(s.left);
    setSbsRightSel(s.right);
    const c = readPersistedConnect(userId);
    setCompareLeftSel(c.left);
    setCompareRightSel(c.right);
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    if (!sbsLeftSel && !sbsRightSel) {
      localStorage.removeItem(compareSbsStorageKey(userId));
      return;
    }
    localStorage.setItem(compareSbsStorageKey(userId), JSON.stringify({ left: sbsLeftSel, right: sbsRightSel }));
  }, [userId, sbsLeftSel, sbsRightSel]);

  useEffect(() => {
    if (!userId) return;
    if (!compareLeftSel && !compareRightSel) {
      localStorage.removeItem(compareConnectStorageKey(userId));
      return;
    }
    localStorage.setItem(compareConnectStorageKey(userId), JSON.stringify({ left: compareLeftSel, right: compareRightSel }));
  }, [userId, compareLeftSel, compareRightSel]);

  const attachRetirementRefineAfterEmu = (payload) => {
    if (!retirementRefineAfterEmuRef.current) return;
    retirementRefineAfterEmuRef.current = false;
    payload.retirement_refinement_after_emu = true;
  };

  useEffect(() => {
    setIntakeFormError(null);
  }, [formState]);

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH_PX}px)`);
    const onChange = () => {
      const mobile = mq.matches;
      setIsMobile(mobile);
      if (mobile) {
        setSidebarOpen(false);
      } else {
        setSidebarOpen(true);
        setTopbarMenuOpen(false);
      }
    };
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const prevViewForSidebarRef = useRef(view);
  useEffect(() => {
    if (isMobile && prevViewForSidebarRef.current !== view) {
      setSidebarOpen(false);
    }
    prevViewForSidebarRef.current = view;
  }, [view, isMobile]);

  const toggleSidebar = useCallback(() => {
    setTopbarMenuOpen(false);
    setSidebarOpen((open) => !open);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, choiceButtons, refineChatOpen]);

  useEffect(() => {
    if (!refineChatOpen) return;
    const t = setTimeout(() => refineChatTextareaRef.current?.focus(), 80);
    return () => clearTimeout(t);
  }, [refineChatOpen]);

  useEffect(() => {
    const ta = refineChatTextareaRef.current;
    if (!refineChatOpen || !ta) return;
    const scrollRefineIntoView = () => {
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }), 280);
    };
    ta.addEventListener("focus", scrollRefineIntoView);
    if (typeof window !== "undefined" && window.visualViewport) {
      window.visualViewport.addEventListener("resize", scrollRefineIntoView);
    }
    return () => {
      ta.removeEventListener("focus", scrollRefineIntoView);
      if (typeof window !== "undefined" && window.visualViewport) {
        window.visualViewport.removeEventListener("resize", scrollRefineIntoView);
      }
    };
  }, [refineChatOpen, refineChatInput]);

  useEffect(() => {
    const t = setTimeout(() => window.dispatchEvent(new Event("resize")), 320);
    return () => clearTimeout(t);
  }, [sidebarOpen, isMobile]);

  useEffect(() => {
    setPortfolioWhatIfMode(false);
    setShowPortfolioUpdateComposition(false);
  }, [selectedPortfolioId]);

  useEffect(() => {
    if (!showPortfolioUpdateComposition || !selectedPortfolioRow?.portfolio_ticker_weights) return;
    const w = selectedPortfolioRow.portfolio_ticker_weights;
    const entries = Object.entries(w).sort(([a], [b]) => a.localeCompare(b));
    setPortfolioUpdateRows(
      entries.length
        ? entries.map(([t, v]) => ({
            ticker: t,
            weight: String(typeof v === "number" && Number.isFinite(v) ? v : parseFloat(v) || ""),
          }))
        : [{ ticker: "", weight: "" }],
    );
    setPortfolioUpdateError("");
  }, [
    showPortfolioUpdateComposition,
    selectedPortfolioRow?.portfolio_id,
    selectedPortfolioRow?.updated_at,
  ]);

  useEffect(() => {
    if (view !== "portfolio") setPortfolioWhatIfMode(false);
  }, [view]);

  // Ctrl+R / Cmd+R: start a new session and reset chat (session ID updates in sidebar)
  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "r" || e.key === "R")) {
        e.preventDefault();
        startNewSession();
        nextMessageIdRef.current = 2;
        setMessages(INITIAL_MESSAGES);
        setView(userId ? "loggedInOptions" : "intake");
        setChoiceButtons(null);
        setRefineChatOpen(false);
        setRefineChatInput("");
        setAwaitingPortfolios(false);
        setSelectedPortfolioId(null);
        setSelectedPortfolioRow(null);
        setPortfolioViewData(null);
        setShowPortfolioDeleteConfirm(false);
        setShowPortfolioUpdateComposition(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [startNewSession, userId]);

  const addMessage = (role, text, artifacts = null, agent = null) => {
    const cleaned = text && typeof text === "string"
      ? text.replace(/<<<PORTFOLIOS_JSON>>>[\s\S]*?<<<END_PORTFOLIOS_JSON>>>/g, "").replace(/```\s*json\s*```/gi, "").trim()
      : text;
    setMessages((prev) => [
      ...prev,
      {
        id: nextMessageIdRef.current++,
        role,
        text: cleaned,
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        artifacts: role === "assistant" ? artifacts : undefined,
        agent: role === "assistant" ? agent : undefined,
      },
    ]);
  };
  const addMessageRef = useRef(addMessage);
  addMessageRef.current = addMessage;

  const isPortfolioChoiceMessage = (t) =>
    /^go with the (conservative|moderate|aggressive) portfolio$/i.test((t || "").trim());

  const postMoneyManager = useCallback(
    async (payload, flow) => {
      if (moneyManagerAbortRef.current) {
        moneyManagerAbortRef.current.abort();
      }
      const controller = new AbortController();
      moneyManagerAbortRef.current = controller;
      if (flow === "growth" || flow === "retirement") {
        activePortfolioFlowRef.current = flow;
      }
      const pf = flow || activePortfolioFlowRef.current;
      // Pick messages must not re-send portfolio_flow (backend would reset to retirement_planning).
      if (pf && !isPortfolioChoiceMessage(payload.message)) {
        payload.portfolio_flow = pf;
      }
      portfolioFlowEpochRef.current += 1;
      payload.flow_epoch = portfolioFlowEpochRef.current;
      appendMoneyManagerUserId(payload, userId);
      try {
        return await postJson("/api/chat/money-manager", payload, { signal: controller.signal });
      } catch (err) {
        if (err.name === "AbortError") return null;
        throw err;
      } finally {
        if (moneyManagerAbortRef.current === controller) {
          moneyManagerAbortRef.current = null;
        }
      }
    },
    [userId],
  );

  const addChatResponse = (data) => {
    if (!data) return;
    if (data?.artifacts?.quala_handoff) {
      addMessage("assistant", data.artifacts.quala_handoff, null, "Quala");
    }
    if (data?.artifacts?.panda_handoff) {
      addMessage("assistant", data.artifacts.panda_handoff, null, "Panda");
    }
    addMessage("assistant", data.reply, data.artifacts, data.agent);
    handleArtifacts(data);
    handleActions(data?.actions, data?.agent);
    if (
      currentIntent === "money_manager" ||
      data?.intent === "money_manager" ||
      data?.actions?.some((a) => a?.type === "show_portfolio_choices" || a?.type === "show_post_backtest_choices")
    ) {
      setView("chat");
    }
  };

  const handleArtifacts = (data) => {
    const a = data?.artifacts ?? data;
    if (!a) return;
    if (a.portfolio_composition) {
      setLastPortfolioComposition(a.portfolio_composition);
      const hasSec =
        a.portfolio_sectors && typeof a.portfolio_sectors === "object" && Object.keys(a.portfolio_sectors).length > 0;
      const hasInd =
        a.portfolio_industries && typeof a.portfolio_industries === "object" && Object.keys(a.portfolio_industries).length > 0;
      setLastPortfolioSectors(hasSec ? a.portfolio_sectors : null);
      setLastPortfolioIndustries(hasInd ? a.portfolio_industries : null);
    }
    const val = a?.intake?.initial_value;
    if (val != null && typeof val === "number") setLastPortfolioValue(val);
  };

  const fetchSavedPortfolios = useCallback(async (overrideUserId, refreshValuations = false) => {
    const uid = overrideUserId ?? userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) return;
    try {
      const rv = refreshValuations ? "&refresh_valuations=1" : "";
      const res = await getJson(`/api/portfolio/saved?user_id=${encodeURIComponent(uid)}${rv}`);
      setSavedPortfolios(res.portfolios || []);
    } catch {
      setSavedPortfolios([]);
    }
  }, [userId]);

  const fetchNetWorthSidebar = useCallback(async (overrideUserId) => {
    const uid = overrideUserId ?? userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) {
      setNetWorthSidebarPayload(null);
      return;
    }
    try {
      const data = await getJson(`/api/user/net-worth?user_id=${encodeURIComponent(uid)}`);
      setNetWorthSidebarPayload(data);
    } catch {
      setNetWorthSidebarPayload(null);
    }
  }, [userId]);

  /** Apply server document immediately (e.g. after PUT) so sidebar/landing net worth is not stale until a separate GET. */
  const applyNetWorthSidebarPayload = useCallback((payload) => {
    if (!payload || typeof payload !== "object") return;
    const assets = Array.isArray(payload.assets) ? payload.assets : [];
    const debts = Array.isArray(payload.debts) ? payload.debts : [];
    const linked = Array.isArray(payload.linked_portfolio_ids) ? payload.linked_portfolio_ids : [];
    setNetWorthSidebarPayload({ assets, debts, linked_portfolio_ids: linked });
  }, []);

  const sidebarNetWorthDisplay = useMemo(
    () => computeSidebarNetWorth(netWorthSidebarPayload, savedPortfolios),
    [netWorthSidebarPayload, savedPortfolios],
  );

  const sidebarNetWorthShown = useMemo(() => {
    if (
      view === "netWorth" &&
      netWorthLiveSidebarAmount != null &&
      Number.isFinite(netWorthLiveSidebarAmount)
    ) {
      return netWorthLiveSidebarAmount;
    }
    return sidebarNetWorthDisplay;
  }, [view, netWorthLiveSidebarAmount, sidebarNetWorthDisplay]);

  useEffect(() => {
    if (view !== "netWorth") setNetWorthLiveSidebarAmount(null);
  }, [view]);

  const handleNetWorthLiveSidebarChange = useCallback((n) => {
    setNetWorthLiveSidebarAmount(n);
  }, []);

  const fetchSavedScenarios = useCallback(async (overrideUserId) => {
    const uid = overrideUserId ?? userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) return;
    try {
      const res = await getJson(`/api/scenarios?user_id=${encodeURIComponent(uid)}`);
      setSavedScenarios(res.scenarios || []);
    } catch {
      setSavedScenarios([]);
    }
  }, [userId]);

  const fetchSavedLifeScenarios = useCallback(async (overrideUserId) => {
    const uid = overrideUserId ?? userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) return;
    try {
      const res = await getJson(`/api/life-scenarios?user_id=${encodeURIComponent(uid)}`);
      setSavedLifeScenarios(res.life_scenarios || []);
    } catch {
      setSavedLifeScenarios([]);
    }
  }, [userId]);

  useEffect(() => {
    if (userId) {
      fetchSavedPortfolios(undefined, true);
      fetchSavedScenarios();
      fetchSavedLifeScenarios();
      fetchNetWorthSidebar();
    } else {
      setNetWorthSidebarPayload(null);
    }
  }, [userId, fetchSavedPortfolios, fetchSavedScenarios, fetchSavedLifeScenarios, fetchNetWorthSidebar]);

  useLayoutEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  useEffect(() => {
    if (userId) setView((v) => (v === "intake" ? "loggedInOptions" : v));
    else setView((v) => (v === "loggedInOptions" ? "intake" : v));
  }, [userId]);

  // Intake refresh should never hydrate stale values from browser storage.
  useEffect(() => {
    if (view === "intake") {
      setFormState({ ...defaultFormState });
      localStorage.removeItem(FORM_STATE_KEY);
    }
  }, [view]);

  // Load profile from DB when logged-in user views loggedInOptions (e.g. on login or page refresh)
  useEffect(() => {
    if (!userId || view !== "loggedInOptions") return;
    setUserFilledIntakeForm(true);
    getJson(`/api/user/intake?user_id=${encodeURIComponent(userId)}`)
      .then((intake) => {
        if (!intake) return;
        const next = formStateFromIntakeApi(intake);
        if (!next) return;
        setFormState(next);
        saveFormStateToStorage(next);
        setProfileSaved(false);
      })
      .catch(() => {
        setProfileSaved(false);
      });
  }, [userId, view]);

  const savePortfolio = async (userId, portfolioName, portfolioValue, saveDescription, options = {}) => {
    if (!lastPortfolioComposition || typeof lastPortfolioComposition !== "object") {
      throw new Error("No portfolio to save.");
    }
    const { intakeFromFormState = false } = options;
    const name = (portfolioName || "My Portfolio").trim() || "My Portfolio";
    const uid = userId || localStorage.getItem(USER_ID_KEY);
    if (!uid) {
      throw new Error("Please sign in to save a portfolio.");
    }
    const lastAgent = messages.filter((m) => m.role === "assistant").pop()?.agent;
    const portfolio_category = (lastAgent === "Panda" || lastAgent === "Emu") ? "retirement" : "growth";
    const fromArtifacts = sectorIndustryWeightsForSave(messages, lastPortfolioComposition);
    const sectorW = fromArtifacts.sectors ?? lastPortfolioSectors;
    const industryW = fromArtifacts.industries ?? lastPortfolioIndustries;
    const savePayload = {
      user_id: uid,
      session_id: sessionId,
      portfolio_name: name,
      portfolio_value: portfolioValue ?? lastPortfolioValue ?? undefined,
      portfolio_ticker_weights: lastPortfolioComposition,
      portfolio_sector_weights: sectorW || undefined,
      portfolio_industry_weights: industryW || undefined,
      portfolio_category,
    };
    const intake = intakeFromFormState
      ? intakeWithoutWhatIf(buildIntakeFromFormState(formState))
      : getIntakeFromForm();
    const desc = (saveDescription ?? "").trim();
    if (intake) {
      savePayload.intake = desc ? { ...intake, save_description: desc } : { ...intake };
    } else if (desc) {
      savePayload.intake = { save_description: desc };
    }
    await postJson("/api/portfolio/save", savePayload);
    await postJson("/api/portfolio", {
      session_id: sessionId,
      holdings: lastPortfolioComposition,
    });
    addMessage(
      "assistant",
      `Portfolio "${name}" saved. You can now do scenario planning using the saved portfolio or create or analyze another portfolio.`,
    );
    fetchSavedPortfolios(uid);
    fetchNetWorthSidebar(uid);
  };

  const openRefineChat = (advisor) => {
    setRefineChatAdvisor(advisor === "Panda" ? "Panda" : "Quala");
    setRefineChatInput("");
    setRefineChatOpen(true);
    setView("chat");
  };

  const handleRefineChatSend = async () => {
    const text = refineChatInput.trim();
    if (!text || isTyping) return;
    setRefineChatInput("");
    await sendQuickMessage(text);
  };

  const handleRefineChatKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleRefineChatSend();
    }
  };

  const handleActions = (actions, responseAgent) => {
    if (!actions?.length) return;
    for (const action of actions) {
      if (action.type === "show_portfolio_choices") {
        setRefineChatOpen(false);
        setAwaitingPortfolios(false);
        const refineAdvisor = action.refine_advisor || "Quala";
        const refinePromptGrowth =
          "How would you like to refine? Examples: add QQQ=30%, add GLD, shift toward more bonds.";
        const refinePromptRetirement =
          "How would you like to refine? Examples: add JEPI=15%, add SCHD, tilt toward more yield or bonds.";
        setChoiceButtons({
          choices: [
            { label: "1) Conservative", value: "Go with the conservative portfolio" },
            { label: "2) Moderate", value: "Go with the moderate portfolio" },
            { label: "3) Aggressive", value: "Go with the aggressive portfolio" },
            { label: "Keep Refining", value: "__keep_refining_portfolio__" },
          ],
          onPick: (value) => {
            if (value === "__keep_refining_portfolio__") {
              setChoiceButtons(null);
              setAwaitingPortfolios(false);
              const prompt =
                refineAdvisor === "Panda" ? refinePromptRetirement : refinePromptGrowth;
              addMessage("assistant", prompt, null, refineAdvisor);
              openRefineChat(refineAdvisor);
            } else {
              sendQuickMessage(value);
            }
          },
        });
      } else if (action.type === "show_post_backtest_choices") {
        setRefineChatOpen(false);
        const refineAdvisor = action.refine_advisor || "Quala";
        const refinePromptGrowth =
          "How would you like to refine? Examples: add QQQ=30%, add GLD, shift toward more bonds.";
        const refinePromptRetirement =
          "How would you like to refine? Examples: add JEPI=15%, add SCHD, tilt toward more yield or bonds.";
        setChoiceButtons({
          choices: [
            { label: "1) Save portfolio", value: "__save_portfolio__" },
            { label: "2) Keep refining", value: "__keep_refining__" },
          ],
          onPick: (value) => {
            setChoiceButtons(null);
            if (value === "__save_portfolio__") {
              setAuthScreenDefaultTab("register");
              setAuthCancelView("chat");
              setAuthPostLoginView("chat");
              setView("auth");
            } else if (value === "__keep_refining__") {
              if (refineAdvisor === "Panda") {
                retirementRefineAfterEmuRef.current = true;
                addMessage(
                  "assistant",
                  "I'm handing you back to Panda so we can revise all three retirement portfolios—conservative, moderate, and aggressive—based on what you type next.",
                  null,
                  "Emu",
                );
              }
              const prompt =
                refineAdvisor === "Panda" ? refinePromptRetirement : refinePromptGrowth;
              addMessage("assistant", prompt, null, refineAdvisor);
              openRefineChat(refineAdvisor);
            }
          },
        });
      }
    }
  };

  const sendQuickMessage = async (text) => {
    setChoiceButtons(null);
    setView("chat");
    addMessage("user", text);
    if (isPortfolioChoiceMessage(text)) {
      setWaitingForAnalyst(true);
    }
    setIsTyping(true);
    try {
      const payload = { session_id: sessionId, message: text };
      const intake = getIntakeFromFormOrState();
      if (intake) payload.intake = intake;
      attachRetirementRefineAfterEmu(payload);
      const data = await postMoneyManager(payload);
      if (!data) return;
      syncSessionFromResponse(data.session_id);
      addChatResponse(data);
    } catch (err) {
      addMessage("error", err.message);
    } finally {
      setIsTyping(false);
      setWaitingForAnalyst(false);
      setAwaitingPortfolios(false);
    }
  };

  const buildIntakeFromFormState = (state) => {
    const planningFor = state.planningFor || "self";
    const birthYear1 = parseInt(state.birthYear1, 10);
    const birthMonth1 = parseInt(state.birthMonth1, 10);
    const birthYear2 = parseInt(state.birthYear2, 10);
    const birthMonth2 = parseInt(state.birthMonth2, 10);
    const retirementTimelineSelf = state.retirementTimelineSelf || "";
    const retirementTimelinePartner = state.retirementTimelinePartner || "";
    const investmentValue = state.investmentValue || "";
    const monthlyContribution = state.monthlyContribution || "";
    const monthlyExpense = state.monthlyExpense || "";
    const initialVal = parseAmount(investmentValue) || 1000;
    const monthlySav = parseAmount(monthlyContribution) || 0;
    const bothRetired = state.retirementStatus === "both_retired";
    const horizonYr = inferHorizonYears({
      planningFor,
      retirementStatus: state.retirementStatus,
      retirementTimelineSelf,
      retirementTimelinePartner,
    });
    const monthlyExpenseVal = parseAmount(monthlyExpense) || 0;
    const birthDates = [];
    if (birthYear1 >= 1920 && birthYear1 <= 2010) {
      birthDates.push({ year: birthYear1, month: (birthMonth1 >= 1 && birthMonth1 <= 12) ? birthMonth1 : 6 });
    }
    if (planningFor === "couple" && birthYear2 >= 1920 && birthYear2 <= 2010) {
      birthDates.push({ year: birthYear2, month: (birthMonth2 >= 1 && birthMonth2 <= 12) ? birthMonth2 : 6 });
    }
    const displayUnit = parseDisplayUnit(investmentValue) || null;
    const spendingForApi = spendingTextFromFormState(state);
    const out = {
      initial_value: initialVal,
      monthly_savings: monthlySav,
      horizon_years: bothRetired ? 0 : horizonYr ?? undefined,
      planning_for: planningFor,
      birth_dates: birthDates,
      current_monthly_expense: monthlyExpenseVal,
      display_unit: displayUnit,
      retirement_status: state.retirementStatus,
      retirement_timeline_self: retirementTimelineSelf || undefined,
      retirement_timeline_partner: retirementTimelinePartner || undefined,
      country: "USA",
      state: state.state?.trim() || undefined,
      inflation_assumption: (v => Number.isFinite(v) ? v : 3)(parseFloat(state.inflationAssumption)),
      risk: state.risk?.trim() || "medium",
      // Big / lumpy spending; use null (not omit) so merged saved-portfolio intake clears stale DB `spending`.
      spending: spendingForApi !== undefined ? spendingForApi : null,
      // Structured rows so backend merge can apply one-time outflows even if `spending` is null (parity with api.js).
      big_spending_rows: (() => {
        const rows = expensesFromBigSpendingRows(state.bigSpendingRows);
        return rows.length ? rows : null;
      })(),
      other_notes: state.otherNotes?.trim() || undefined,
    };
    // Retirement what-if: structured rows — backend uses retirement_income_rows / retirement_misc_spending_rows
    const incomeRows = (state.monthlyIncomeRows || [])
      .map((r) => {
        const m = parseAmount(String(r?.monthly ?? "").trim());
        const sa = parseInt(String(r?.startAge ?? "").trim(), 10);
        const ea = (r?.endAge ?? "").toString().trim();
        if (m == null || m <= 0) return null;
        const row = { monthly: m, start_age: sa >= 0 ? sa : 0, end_age: ea ? parseInt(ea, 10) : 100 };
        const yoyRaw = String(r?.yoyPct ?? "").trim().replace(/%/g, "").replace(/,/g, "");
        if (yoyRaw && yoyRaw !== "-" && yoyRaw !== "+") {
          const yoyN = parseFloat(yoyRaw);
          if (Number.isFinite(yoyN)) row.yoy_annual_pct = yoyN;
        }
        const ilb = String(r?.label ?? "").trim();
        if (ilb) row.label = ilb;
        return row;
      })
      .filter(Boolean);
    out.retirement_income_rows = incomeRows;
    const spendRows = (state.miscMonthlySpendingRows || [])
      .map((r) => {
        const m = parseAmount(String(r?.monthly ?? "").trim());
        const sa = parseInt(String(r?.startAge ?? "").trim(), 10);
        const ea = (r?.endAge ?? "").toString().trim();
        if (m == null || m <= 0) return null;
        const row = { monthly: m, start_age: sa >= 0 ? sa : 0, end_age: ea ? parseInt(ea, 10) : 100 };
        const yoyRaw = String(r?.yoyPct ?? "").trim().replace(/%/g, "").replace(/,/g, "");
        if (yoyRaw && yoyRaw !== "-" && yoyRaw !== "+") {
          const yoyN = parseFloat(yoyRaw);
          if (Number.isFinite(yoyN)) row.yoy_annual_pct = yoyN;
        }
        const slb = String(r?.label ?? "").trim();
        if (slb) row.label = slb;
        return row;
      })
      .filter(Boolean);
    out.retirement_misc_spending_rows = spendRows;
    const retTaxRaw = parseFloat(String(state.retirementEffectiveTaxRate ?? "").trim());
    if (Number.isFinite(retTaxRaw) && retTaxRaw >= 0) {
      out.retirement_effective_tax_rate = retTaxRaw <= 1 ? retTaxRaw * 100 : retTaxRaw;
    }
    const rdM = parseAmount(String(state.retirementDiscretionaryMonthly ?? "").trim());
    const rdP = parseFloat(String(state.retirementDiscretionaryMinPriorReturnPct ?? "").trim());
    const _parseWhatIfAge = (v) => {
      const t = String(v ?? "").trim();
      if (!t) return null;
      const n = parseInt(t, 10);
      return Number.isFinite(n) && n >= 0 && n <= 120 ? n : null;
    };
    if (rdM != null && rdM > 0 && Number.isFinite(rdP)) {
      out.retirement_discretionary_monthly = rdM;
      out.retirement_discretionary_in_year = null;
      out.retirement_discretionary_min_prior_year_return_pct = rdP;
      const rdSa = _parseWhatIfAge(state.retirementDiscretionaryStartAge);
      const rdEa = _parseWhatIfAge(state.retirementDiscretionaryEndAge);
      if (rdSa != null && rdEa != null && rdSa <= rdEa) {
        out.retirement_discretionary_start_age = rdSa;
        out.retirement_discretionary_end_age = rdEa;
      } else {
        out.retirement_discretionary_start_age = null;
        out.retirement_discretionary_end_age = null;
      }
    } else {
      out.retirement_discretionary_monthly = null;
      out.retirement_discretionary_in_year = null;
      out.retirement_discretionary_min_prior_year_return_pct = null;
      out.retirement_discretionary_start_age = null;
      out.retirement_discretionary_end_age = null;
    }
    out.growth_portfolio_start_age = null;
    out.growth_portfolio_end_age = null;
    out.retirement_portfolio_start_age = null;
    out.retirement_portfolio_end_age = null;
    const wfStructured = expensesFromBigSpendingRows(state.windfallInflowRows);
    out.windfall_inflow_rows = wfStructured;
    // Structured rows are canonical; strip legacy DB-only windfall keys so merge cannot revive them.
    out.windfall_years = null;
    out.windfall_amount = null;
    out.windfall_spending = null;
    const growthIncomeRows = (state.growthMonthlyIncomeRows || [])
      .map((r) => {
        const m = parseAmount(String(r?.monthly ?? "").trim());
        const sa = parseInt(String(r?.startAge ?? "").trim(), 10);
        const ea = (r?.endAge ?? "").toString().trim();
        if (m == null || m <= 0) return null;
        const row = { monthly: m, start_age: sa >= 0 ? sa : 0, end_age: ea ? parseInt(ea, 10) : 100 };
        const yoyRaw = String(r?.yoyPct ?? "").trim().replace(/%/g, "").replace(/,/g, "");
        if (yoyRaw && yoyRaw !== "-" && yoyRaw !== "+") {
          const yoyN = parseFloat(yoyRaw);
          if (Number.isFinite(yoyN)) row.yoy_annual_pct = yoyN;
        }
        const glb = String(r?.label ?? "").trim();
        if (glb) row.label = glb;
        return row;
      })
      .filter(Boolean);
    out.growth_monthly_income_rows = growthIncomeRows;
    out.growth_monthly_income_freeform = state.growthMonthlyIncomeFreeform?.trim() || null;
    const growthMiscSpendRows = (state.growthMiscMonthlySpendingRows || [])
      .map((r) => {
        const m = parseAmount(String(r?.monthly ?? "").trim());
        const sa = parseInt(String(r?.startAge ?? "").trim(), 10);
        const ea = (r?.endAge ?? "").toString().trim();
        if (m == null || m <= 0) return null;
        const row = { monthly: m, start_age: sa >= 0 ? sa : 0, end_age: ea ? parseInt(ea, 10) : 100 };
        const yoyRaw = String(r?.yoyPct ?? "").trim().replace(/%/g, "").replace(/,/g, "");
        if (yoyRaw && yoyRaw !== "-" && yoyRaw !== "+") {
          const yoyN = parseFloat(yoyRaw);
          if (Number.isFinite(yoyN)) row.yoy_annual_pct = yoyN;
        }
        const mlb = String(r?.label ?? "").trim();
        if (mlb) row.label = mlb;
        return row;
      })
      .filter(Boolean);
    out.growth_misc_spending_rows = growthMiscSpendRows;
    // When misc rows are empty, backend falls back to this freeform — null clears a stale portfolio snapshot.
    out.growth_misc_spending_freeform =
      (state.growthMiscSpendingFreeform != null && String(state.growthMiscSpendingFreeform).trim()) || null;
    const gtiStructured = expensesFromBigSpendingRows(state.growthOneTimeInflowRows);
    out.growth_one_time_inflow_rows = gtiStructured;
    out.growth_one_time_inflow_freeform =
      (state.growthOneTimeInflowFreeform != null && String(state.growthOneTimeInflowFreeform).trim()) || null;
    // Form only edits structured retirement rows; null clears invisible DB freeform so it cannot linger.
    out.retirement_income_freeform = null;
    out.retirement_misc_spending_freeform = null;
    return out;
  };

  /** Run MC/backtest for a saved portfolio using an explicit form snapshot (avoids stale React state after load). */
  const runPortfolioBacktestWithFormSnapshot = useCallback(
    async (portfolioId, formSnapshot, loadToken, usePortfolioMarkForInitial = true, scenarioId = null) => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid || !portfolioId) return;
      setPortfolioViewLoading(true);
      setPortfolioViewLoadingPhase("backtest");
      setPortfolioViewData(null);
      const sid = scenarioId ? String(scenarioId).trim() : "";
      console.info(
        "[portfolio] POST /backtest — recomputing MC",
        portfolioId,
        sid ? `scenario_id=${sid}` : "portfolio-level",
      );
      try {
        const intake = buildIntakeFromFormState(formSnapshot);
        const body = {
          user_id: uid,
          intake,
          use_portfolio_mark_for_initial: usePortfolioMarkForInitial,
        };
        if (sid) body.scenario_id = sid;
        const res = await postJson(`/api/portfolio/saved/${encodeURIComponent(portfolioId)}/backtest`, body);
        if (loadToken !== undefined && loadToken !== portfolioViewLoadTokenRef.current) {
          return;
        }
        const vh = portfolioValuationCacheRef.current;
        const vhFromRes = res.portfolio?.valuation_history;
        const useRes =
          Array.isArray(vhFromRes) &&
          vhFromRes.length > 0;
        const nextHist = useRes ? vhFromRes : vh.history;
        const nextAsOf = useRes ? res.portfolio?.valuation_as_of ?? null : vh.asOf;
        if (useRes) {
          portfolioValuationCacheRef.current = { history: nextHist, asOf: nextAsOf };
        }
        setPortfolioViewData({
          portfolio: {
            ...res.portfolio,
            valuation_history: nextHist,
            valuation_as_of: nextAsOf,
          },
          artifacts: res.artifacts,
          agent: res.agent,
        });
        handleArtifacts({ artifacts: res.artifacts });
      } catch (err) {
        addMessage("error", err.message || "Backtest failed");
      } finally {
        if (loadToken === undefined || loadToken === portfolioViewLoadTokenRef.current) {
          setPortfolioViewLoading(false);
          setPortfolioViewLoadingPhase("idle");
        }
      }
    },
    [userId],
  );

  const runPortfolioMonteCarloBacktest = useCallback(async () => {
    if (!selectedPortfolioId) return;
    const tok = portfolioViewLoadTokenRef.current;
    // Latest form values (avoids stale closure if a field updated in the same frame as Submit).
    const snapshot = formStateRef.current;
    const sid = selectedScenarioId ? String(selectedScenarioId).trim() : null;
    // Honor edited intake (e.g. initial investment + what-if rows); do not replace initial_value with live portfolio_value.
    await runPortfolioBacktestWithFormSnapshot(selectedPortfolioId, snapshot, tok, false, sid);
  }, [selectedPortfolioId, selectedScenarioId, runPortfolioBacktestWithFormSnapshot]);

  const openNetWorthView = useCallback(() => {
    portfolioViewLoadTokenRef.current += 1;
    setSelectedPortfolioId(null);
    setSelectedScenarioId(null);
    setSelectedScenarioRow(null);
    setSelectedPortfolioRow(null);
    setPortfolioViewData(null);
    setPortfolioViewLoading(false);
    setShowPortfolioDeleteConfirm(false);
    setShowScenarioDeleteConfirm(false);
    setShowPortfolioUpdateComposition(false);
    setPortfolioUpdateError("");
    setPortfolioWhatIfMode(false);
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (uid) fetchNetWorthSidebar(uid);
    setView("netWorth");
  }, [userId, fetchNetWorthSidebar]);

  const openSameCategoryCompare = useCallback(() => {
    portfolioViewLoadTokenRef.current += 1;
    setSelectedPortfolioId(null);
    setSelectedScenarioId(null);
    setSelectedScenarioRow(null);
    setSelectedPortfolioRow(null);
    setPortfolioViewData(null);
    setPortfolioViewLoading(false);
    setShowPortfolioDeleteConfirm(false);
    setShowScenarioDeleteConfirm(false);
    setShowPortfolioUpdateComposition(false);
    setPortfolioUpdateError("");
    setPortfolioWhatIfMode(false);
    setView("compare");
    queueMicrotask(() => {
      document.querySelector(".chat-scroll")?.scrollTo(0, 0);
    });
  }, []);

  /** Clear open planner drops/forms; used after save and when entering Open planner for a fresh session. */
  const resetOpenPlannerLanding = useCallback(() => {
    compareConnectBacktestTokenRef.current += 1;
    connectChartsHydrateInFlightRef.current = false;
    connectChartsHydrateAttemptsRef.current = { lifeId: null, count: 0 };
    setCompareLeftSel(null);
    setCompareRightSel(null);
    setCompareGrowthForm(null);
    setCompareRetireForm(null);
    setCompareGrowthArtifacts(null);
    setCompareRetireArtifacts(null);
    setCompareHydrating(false);
    setCompareGrowthRunLoading(false);
    setCompareRetireRunLoading(false);
    setSelectedLifeScenarioId(null);
    setConnectLinkedGrowthScenarioId(null);
    setConnectLinkedRetirementScenarioId(null);
    setConnectFrozenGrowthMedianUsd(null);
    setConnectLifePlannerFrozen(false);
    setConnectLifeScenarioNameInput("");
    setCompareNotice(null);
    setCompareRetireSyncMessage(null);
    connectPlannerIntakesRef.current = { g: null, r: null };
    lastConnectHydrateKeyRef.current = "";
  }, []);

  const openConnectGrowthRetireView = useCallback(() => {
    portfolioViewLoadTokenRef.current += 1;
    setSelectedPortfolioId(null);
    setSelectedScenarioId(null);
    setSelectedScenarioRow(null);
    setSelectedPortfolioRow(null);
    setPortfolioViewData(null);
    setPortfolioViewLoading(false);
    setShowPortfolioDeleteConfirm(false);
    setShowScenarioDeleteConfirm(false);
    setShowPortfolioUpdateComposition(false);
    setPortfolioUpdateError("");
    setPortfolioWhatIfMode(false);
    setConnectPairScenarioError(null);
    setConnectPairScenarioSuccess(null);
    resetOpenPlannerLanding();
    setView("connectGrowthRetire");
    queueMicrotask(() => {
      document.querySelector(".chat-scroll")?.scrollTo(0, 0);
    });
  }, [resetOpenPlannerLanding]);

  const openLifeScenarioFromSidebar = useCallback(
    async (lifeScenarioId) => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid) return;
      portfolioViewLoadTokenRef.current += 1;
      setSelectedPortfolioId(null);
      setSelectedScenarioId(null);
      setSelectedScenarioRow(null);
      setSelectedPortfolioRow(null);
      setPortfolioViewData(null);
      setPortfolioViewLoading(false);
      setShowPortfolioDeleteConfirm(false);
      setShowScenarioDeleteConfirm(false);
      setShowPortfolioUpdateComposition(false);
      setPortfolioUpdateError("");
      setPortfolioWhatIfMode(false);
      setConnectPairScenarioError(null);
      setConnectPairScenarioSuccess(null);
      const reqId = ++openLifeScenarioRequestIdRef.current;
      try {
        const row = await getJson(
          `/api/life-scenario/${encodeURIComponent(lifeScenarioId)}?user_id=${encodeURIComponent(uid)}&include_backtest=true&_=${Date.now()}`,
        );
        if (reqId !== openLifeScenarioRequestIdRef.current) return;
        const g = row.growth;
        const r = row.retirement;
        if (!g?.portfolio_id || !r?.portfolio_id || !g?.scenario_id || !r?.scenario_id) {
          addMessage("error", "Life scenario data is incomplete.");
          return;
        }
        const gp = row.growth_planner_intake;
        const rp = row.retirement_planner_intake;
        connectPlannerIntakesRef.current = {
          g: gp && typeof gp === "object" ? gp : null,
          r: rp && typeof rp === "object" ? rp : null,
        };
        compareConnectBacktestTokenRef.current += 1;
        lastConnectHydrateKeyRef.current = "";
        connectChartsHydrateInFlightRef.current = false;
        connectChartsHydrateAttemptsRef.current = { lifeId: null, count: 0 };
        const gArt = row.growth_backtest_artifacts;
        const rArt = row.retirement_backtest_artifacts;
        setCompareGrowthArtifacts(
          gArt && compareBacktestArtifactsReady(gArt) ? gArt : null,
        );
        setCompareRetireArtifacts(
          rArt && compareBacktestArtifactsReady(rArt) ? rArt : null,
        );
        const lifeNm = String(row.name || "").trim();
        setCompareLeftSel({
          kind: "growth",
          source: "scenario",
          portfolioId: g.portfolio_id,
          scenarioId: g.scenario_id,
          label: displayLabelForSavedLifePlanSide(g.scenario_name, g.portfolio_name, lifeNm) || "Growth",
        });
        setCompareRightSel({
          kind: "retirement",
          source: "scenario",
          portfolioId: r.portfolio_id,
          scenarioId: r.scenario_id,
          label: displayLabelForSavedLifePlanSide(r.scenario_name, r.portfolio_name, lifeNm) || "Retirement",
        });
        setSelectedLifeScenarioId(lifeScenarioId);
        setConnectLinkedGrowthScenarioId(row.growth_scenario_id || g.scenario_id);
        setConnectLinkedRetirementScenarioId(row.retirement_scenario_id || r.scenario_id);
        setConnectLifeScenarioNameInput(row.name || "");
        const fz = row.frozen_growth_median_at_retirement_usd;
        setConnectFrozenGrowthMedianUsd(
          fz != null && Number.isFinite(Number(fz)) && Number(fz) > 0 ? Number(fz) : null,
        );
        setConnectLifePlannerFrozen(true);
        setView("connectGrowthRetire");
        queueMicrotask(() => {
          document.querySelector(".chat-scroll")?.scrollTo(0, 0);
        });
      } catch (err) {
        addMessage("error", err.message || "Could not open life scenario");
      }
    },
    [userId, addMessage],
  );

  const handlePortfolioClick = useCallback(
    async (portfolioId) => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid) return;
      const loadToken = ++portfolioViewLoadTokenRef.current;
      setSelectedPortfolioId(portfolioId);
      setSelectedScenarioId(null);
      setSelectedScenarioRow(null);
      setPortfolioSaveAsName("");
      setSelectedPortfolioRow(null);
      setPortfolioViewData(null);
      setPortfolioViewLoading(true);
      setPortfolioViewLoadingPhase("fetch");
      setShowPortfolioDeleteConfirm(false);
      setShowPortfolioUpdateComposition(false);
      setFormState({ ...defaultFormState });
      setView("portfolio");
      try {
        const row = await getJson(
          `/api/portfolio/saved/${encodeURIComponent(portfolioId)}?refresh_mtm=true&include_backtest=true&scenario_id=&_=${Date.now()}`,
        );
        if (loadToken !== portfolioViewLoadTokenRef.current) {
          return;
        }
        setSelectedPortfolioRow(row);
        portfolioValuationCacheRef.current = {
          history: Array.isArray(row.valuation_history) ? row.valuation_history : [],
          asOf: row.valuation_as_of ?? null,
        };
        const intake = row.intake && typeof row.intake === "object" ? row.intake : null;
        const next = formStateFromIntakeApi(intake);
        const formForBacktest = next ?? { ...defaultFormState };
        if (next) {
          // Clear what-if when loading portfolio; do not persist what-if
          const cleared = { ...next };
          WHAT_IF_FORM_KEYS.forEach((k) => {
            cleared[k] = defaultFormState[k] ?? "";
          });
          setFormState(cleared);
          localStorage.setItem(FORM_STATE_KEY, JSON.stringify(formStateWithoutWhatIf(cleared)));
          setProfileSaved(false);
          setUserFilledIntakeForm(true);
        } else {
          setFormState({ ...defaultFormState });
          localStorage.setItem(FORM_STATE_KEY, JSON.stringify(defaultFormState));
          setUserFilledIntakeForm(true);
          setProfileSaved(false);
        }
        const persistedArt = normalizeBacktestArtifacts(row.backtest_artifacts);
        const loadSource = row.backtest_load_source;
        const cat = (row.portfolio_category || "growth").toLowerCase();
        if (persistedArt && compareBacktestArtifactsReady(persistedArt)) {
          console.info(
            "[portfolio] Charts from Supabase snapshot",
            portfolioId,
            loadSource,
            row.backtest_persisted_at ?? "",
          );
          setPortfolioViewLoading(false);
          setPortfolioViewLoadingPhase("idle");
          setPortfolioViewData({
            portfolio: row,
            artifacts: persistedArt,
            agent: cat === "retirement" ? "Emu" : "Ana",
          });
          handleArtifacts({ artifacts: persistedArt });
        } else {
          console.warn(
            "[portfolio] No valid persisted snapshot — will POST /backtest",
            portfolioId,
            { loadSource, hasArtifacts: !!persistedArt },
          );
          await runPortfolioBacktestWithFormSnapshot(portfolioId, formForBacktest, loadToken);
        }
        fetchSavedPortfolios(uid);
      } catch (err) {
        if (loadToken !== portfolioViewLoadTokenRef.current) {
          return;
        }
        addMessage("error", err.message || "Could not load portfolio");
        setView("loggedInOptions");
        setPortfolioViewLoading(false);
        setPortfolioViewLoadingPhase("idle");
      }
    },
    [userId, runPortfolioBacktestWithFormSnapshot, fetchSavedPortfolios, handleArtifacts],
  );

  const getIntakeFromForm = () => {
    if (!userFilledIntakeForm) return null;
    return buildIntakeFromFormState(formState);
  };

  const getIntakeFromFormOrState = () => {
    return getIntakeFromForm() ?? buildIntakeFromFormState(formState);
  };

  const handleScenarioClick = useCallback(
    async (scenarioId, portfolioId) => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid) return;
      const loadToken = ++portfolioViewLoadTokenRef.current;
      setSelectedPortfolioId(portfolioId);
      setSelectedScenarioId(scenarioId);
      setPortfolioSaveAsName("");
      setSelectedScenarioRow(null); // will be set when scenario loads
      setPortfolioViewData(null);
      setPortfolioViewLoading(true);
      setView("portfolio");
      try {
        const [scenarioRow, portfolioRow] = await Promise.all([
          getJson(`/api/scenario/${encodeURIComponent(scenarioId)}?include_backtest=true`),
          getJson(
            `/api/portfolio/saved/${encodeURIComponent(portfolioId)}?refresh_mtm=false&include_backtest=false`,
          ),
        ]);
        if (loadToken !== portfolioViewLoadTokenRef.current) {
          return;
        }
        setSelectedPortfolioRow(portfolioRow);
        setSelectedScenarioRow(scenarioRow);
        portfolioValuationCacheRef.current = {
          history: Array.isArray(portfolioRow.valuation_history) ? portfolioRow.valuation_history : [],
          asOf: portfolioRow.valuation_as_of ?? null,
        };
        const intake = scenarioRow.intake && typeof scenarioRow.intake === "object" ? scenarioRow.intake : null;
        const next = formStateFromIntakeApi(intake);
        const formForBacktest = next ?? { ...defaultFormState };
        if (next) {
          setFormState(next);
          localStorage.setItem(FORM_STATE_KEY, JSON.stringify(formStateWithoutWhatIf(next)));
          setUserFilledIntakeForm(true);
        }
        let persistedArt = scenarioRow.backtest_artifacts;
        let loadSource = scenarioRow.backtest_load_source;
        if (!compareBacktestArtifactsReady(persistedArt)) {
          const pr = await getJson(
            `/api/portfolio/saved/${encodeURIComponent(portfolioId)}?refresh_mtm=false&include_backtest=true&scenario_id=`,
          );
          if (loadToken !== portfolioViewLoadTokenRef.current) return;
          if (compareBacktestArtifactsReady(pr?.backtest_artifacts)) {
            persistedArt = pr.backtest_artifacts;
            loadSource = pr.backtest_load_source;
            console.info("[scenario] Using portfolio-level snapshot (no scenario row yet)", scenarioId);
          }
        }
        const cat = (portfolioRow.portfolio_category || "growth").toLowerCase();
        if (persistedArt && compareBacktestArtifactsReady(persistedArt)) {
          console.info(
            "[scenario] Charts from Supabase snapshot",
            scenarioId,
            loadSource,
          );
          setPortfolioViewLoading(false);
          setPortfolioViewLoadingPhase("idle");
          setPortfolioViewData({
            portfolio: portfolioRow,
            artifacts: persistedArt,
            agent: cat === "retirement" ? "Emu" : "Ana",
          });
          handleArtifacts({ artifacts: persistedArt });
        } else {
          console.warn("[scenario] No persisted snapshot — will POST /backtest", scenarioId);
          await runPortfolioBacktestWithFormSnapshot(
            portfolioId,
            formForBacktest,
            loadToken,
            false,
            scenarioId,
          );
        }
      } catch (err) {
        if (loadToken !== portfolioViewLoadTokenRef.current) {
          return;
        }
        addMessage("error", err.message || "Could not load scenario");
      } finally {
        if (loadToken === portfolioViewLoadTokenRef.current) {
          setPortfolioViewLoading(false);
        }
      }
    },
    [userId, runPortfolioBacktestWithFormSnapshot, handleArtifacts],
  );

  const applyCompareSelection = useCallback(
    (side, payload) => {
      if (connectLifePlannerFrozen) return;
      const notice = validateLifePlannerPick(side, payload);
      if (notice) {
        setCompareNotice(notice);
        return;
      }
      const sel = compareSelFromDragPayload(payload);
      if (!sel) return;
      setCompareNotice(null);
      setCompareRetireSyncMessage(null);
      setConnectPairScenarioError(null);
      setConnectPairScenarioSuccess(null);
      setSelectedLifeScenarioId(null);
      connectPlannerIntakesRef.current = { g: null, r: null };
      compareConnectBacktestTokenRef.current += 1;
      lastConnectHydrateKeyRef.current = "";
      if (side === "left") setCompareLeftSel(sel);
      else setCompareRightSel(sel);
    },
    [connectLifePlannerFrozen],
  );

  const handleCompareDrop = useCallback(
    (side, e) => {
      e.preventDefault();
      e.currentTarget.classList.remove("compare-page-drop-active");
      let payload;
      try {
        payload = JSON.parse(e.dataTransfer.getData("application/json") || "{}");
      } catch {
        return;
      }
      applyCompareSelection(side, payload);
    },
    [applyCompareSelection],
  );

  const loadFormFromCompareSel = useCallback(async (item) => {
    const portfolioRow = await getJson(`/api/portfolio/saved/${encodeURIComponent(item.portfolioId)}`);
    if (item.source === "scenario" && item.scenarioId) {
      const scenarioRow = await getJson(`/api/scenario/${encodeURIComponent(item.scenarioId)}`);
      const intake = scenarioRow.intake && typeof scenarioRow.intake === "object" ? scenarioRow.intake : null;
      const next = formStateFromIntakeApi(intake);
      return next ?? { ...defaultFormState };
    }
    const intake = portfolioRow.intake && typeof portfolioRow.intake === "object" ? portfolioRow.intake : null;
    const next = formStateFromIntakeApi(intake);
    return next ?? { ...defaultFormState };
  }, []);

  const loadIntakeObjectFromSel = useCallback(async (item) => {
    const portfolioRow = await getJson(`/api/portfolio/saved/${encodeURIComponent(item.portfolioId)}`);
    if (item.source === "scenario" && item.scenarioId) {
      const scenarioRow = await getJson(`/api/scenario/${encodeURIComponent(item.scenarioId)}`);
      return scenarioRow.intake && typeof scenarioRow.intake === "object" ? scenarioRow.intake : {};
    }
    return portfolioRow.intake && typeof portfolioRow.intake === "object" ? portfolioRow.intake : {};
  }, []);

  const applySbsSelection = useCallback(
    (side, payload) => {
      const notice = validateSameCategoryPick(side, payload, sbsLeftSel, sbsRightSel);
      if (notice) {
        setSbsNotice(notice);
        return;
      }
      const sel = compareSelFromDragPayload(payload);
      if (!sel) return;
      setSbsNotice(null);
      sbsBacktestTokenRef.current += 1;
      if (side === "left") setSbsLeftSel(sel);
      else setSbsRightSel(sel);
    },
    [sbsLeftSel, sbsRightSel],
  );

  const handleSbsDrop = useCallback(
    (side, e) => {
      e.preventDefault();
      e.currentTarget.classList.remove("compare-page-drop-active");
      let payload;
      try {
        payload = JSON.parse(e.dataTransfer.getData("application/json") || "{}");
      } catch {
        return;
      }
      applySbsSelection(side, payload);
    },
    [applySbsSelection],
  );

  const compareGrowthArtifactsRef = useRef(compareGrowthArtifacts);
  const compareRetireArtifactsRef = useRef(compareRetireArtifacts);
  const compareHydratingRef = useRef(compareHydrating);
  compareGrowthArtifactsRef.current = compareGrowthArtifacts;
  compareRetireArtifactsRef.current = compareRetireArtifacts;
  compareHydratingRef.current = compareHydrating;

  const compareConnectSelectionKey = useMemo(
    () => connectSelectionKey(compareLeftSel, compareRightSel),
    [
      compareLeftSel?.portfolioId,
      compareLeftSel?.scenarioId,
      compareLeftSel?.source,
      compareRightSel?.portfolioId,
      compareRightSel?.scenarioId,
      compareRightSel?.source,
    ],
  );

  /** Load saved intake into editable forms (no backtest) when both sides are set. */
  useEffect(() => {
    if (view !== "connectGrowthRetire") return;
    if (!compareConnectSelectionKey) {
      compareConnectBacktestTokenRef.current += 1;
      lastConnectHydrateKeyRef.current = "";
      setCompareGrowthForm(null);
      setCompareRetireForm(null);
      setCompareGrowthArtifacts(null);
      setCompareRetireArtifacts(null);
      setCompareHydrating(false);
      return;
    }
    if (compareConnectSelectionKey === lastConnectHydrateKeyRef.current) {
      return;
    }
    const leftSel = compareLeftSelRef.current;
    const rightSel = compareRightSelRef.current;
    if (!leftSel || !rightSel) return;

    let cancelled = false;
    const hydrateKey = compareConnectSelectionKey;
    (async () => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid) return;
      setCompareHydrating(true);
      const keepFrozenArtifacts =
        connectLifePlannerFrozen &&
        compareBacktestArtifactsReady(compareGrowthArtifactsRef.current) &&
        compareBacktestArtifactsReady(compareRetireArtifactsRef.current);
      if (!keepFrozenArtifacts) {
        setCompareGrowthArtifacts(null);
        setCompareRetireArtifacts(null);
      }
      setCompareRetireSyncMessage(null);
      try {
        const [gLoaded, rLoaded] = await Promise.all([
          loadFormFromCompareSel(leftSel),
          loadFormFromCompareSel(rightSel),
        ]);
        let gForm = gLoaded;
        let rForm = rLoaded;
        if (connectLifePlannerFrozen && selectedLifeScenarioId) {
          const { g, r } = connectPlannerIntakesRef.current || {};
          if (g && typeof g === "object") {
            const merged = formStateFromIntakeApi(g);
            if (merged) gForm = merged;
          }
          if (r && typeof r === "object") {
            const merged = formStateFromIntakeApi(r);
            if (merged) rForm = merged;
          }
        }
        if (!cancelled) {
          setCompareGrowthForm(gForm);
          setCompareRetireForm(rForm);
        }
        if (!cancelled && !connectLifePlannerFrozen && leftSel.portfolioId && rightSel.portfolioId) {
          const [gArt, rArt] = await Promise.all([
            fetchPersistedBacktestArtifacts(leftSel.portfolioId, uid),
            fetchPersistedBacktestArtifacts(rightSel.portfolioId, uid),
          ]);
          if (!cancelled) {
            if (gArt && compareBacktestArtifactsReady(gArt)) setCompareGrowthArtifacts(gArt);
            if (rArt && compareBacktestArtifactsReady(rArt)) setCompareRetireArtifacts(rArt);
          }
        }
        if (!cancelled && hydrateKey === connectSelectionKey(compareLeftSelRef.current, compareRightSelRef.current)) {
          lastConnectHydrateKeyRef.current = hydrateKey;
        }
      } catch (err) {
        if (!cancelled) {
          addMessageRef.current("error", err.message || "Could not load compare items");
          setCompareGrowthForm(null);
          setCompareRetireForm(null);
          lastConnectHydrateKeyRef.current = "";
        }
      } finally {
        if (!cancelled) setCompareHydrating(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [view, compareConnectSelectionKey, userId, loadFormFromCompareSel, connectLifePlannerFrozen, selectedLifeScenarioId]);

  /** Saved life plan (frozen): column titles use portfolio/scenario name without "{life} — " prefix. */
  useEffect(() => {
    if (view !== "connectGrowthRetire" || !connectLifePlannerFrozen || !Array.isArray(savedScenarios) || !savedScenarios.length) {
      return;
    }
    const lifeNm = String(connectLifeScenarioNameInput || "").trim();
    const patchSel = (sel) => {
      if (!sel || sel.source !== "scenario" || !sel.scenarioId) return null;
      const scRow = savedScenarios.find((s) => String(s.scenario_id) === String(sel.scenarioId));
      if (!scRow) return null;
      const nextLabel = displayLabelForSavedLifePlanSide(scRow.scenario_name, scRow.portfolio_name, lifeNm);
      if (!nextLabel || nextLabel === sel.label) return null;
      return { ...sel, label: nextLabel };
    };
    const nextL = patchSel(compareLeftSelRef.current);
    const nextR = patchSel(compareRightSelRef.current);
    if (nextL) {
      lastConnectHydrateKeyRef.current = "";
      setCompareLeftSel(nextL);
    }
    if (nextR) {
      lastConnectHydrateKeyRef.current = "";
      setCompareRightSel(nextR);
    }
  }, [view, connectLifePlannerFrozen, savedScenarios, connectLifeScenarioNameInput]);

  useEffect(() => {
    if (view !== "compare") return;
    if (!sbsLeftSel || !sbsRightSel || sbsLeftSel.kind !== sbsRightSel.kind) {
      sbsBacktestTokenRef.current += 1;
      setSbsIntakeLeft(null);
      setSbsIntakeRight(null);
      setSbsArtLeft(null);
      setSbsArtRight(null);
      setSbsHydrating(false);
      return;
    }
    let cancelled = false;
    (async () => {
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid) return;
      setSbsHydrating(true);
      setSbsArtLeft(null);
      setSbsArtRight(null);
      try {
        const [a, b] = await Promise.all([loadIntakeObjectFromSel(sbsLeftSel), loadIntakeObjectFromSel(sbsRightSel)]);
        if (!cancelled) {
          setSbsIntakeLeft(a);
          setSbsIntakeRight(b);
        }
      } catch (err) {
        if (!cancelled) {
          addMessage("error", err.message || "Could not load items for compare");
          setSbsIntakeLeft(null);
          setSbsIntakeRight(null);
        }
      } finally {
        if (!cancelled) setSbsHydrating(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [view, sbsLeftSel, sbsRightSel, userId, loadIntakeObjectFromSel]);

  /** Life planner: run growth MC then retirement MC in one step (retirement initial portfolio set from growth P50 at horizon). */
  const handleCompareLifePlannerContinue = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !compareLeftSel || !compareRightSel || !compareGrowthForm || !compareRetireForm) return;
    const t0 = compareConnectBacktestTokenRef.current;
    setCompareGrowthRunLoading(true);
    setCompareRetireRunLoading(false);
    setCompareRetireSyncMessage(null);
    try {
      const intakeG = buildIntakeFromFormState(compareGrowthForm);
      const res = await postJson(`/api/portfolio/saved/${encodeURIComponent(compareLeftSel.portfolioId)}/backtest`, {
        user_id: uid,
        intake: intakeG,
      });
      if (t0 !== compareConnectBacktestTokenRef.current) return;
      setCompareGrowthArtifacts(res.artifacts || null);
      const p50 = extractGrowthTerminalValueP50(res.artifacts);
      const mergePrev = compareRetireForm ?? { ...defaultFormState };
      const mergedForRetire = mergeRetirementFormAfterGrowthBacktest(compareGrowthForm, mergePrev, p50);
      setCompareRetireForm(mergedForRetire);

      setCompareGrowthRunLoading(false);
      setCompareRetireRunLoading(true);
      const intakeR = buildIntakeFromFormState(mergedForRetire);
      const resR = await postJson(`/api/portfolio/saved/${encodeURIComponent(compareRightSel.portfolioId)}/backtest`, {
        user_id: uid,
        intake: intakeR,
      });
      if (t0 !== compareConnectBacktestTokenRef.current) return;
      setCompareRetireArtifacts(resR.artifacts || null);
    } catch (err) {
      if (t0 === compareConnectBacktestTokenRef.current) {
        addMessage("error", err.message || "Backtest failed");
      }
    } finally {
      if (t0 === compareConnectBacktestTokenRef.current) {
        setCompareGrowthRunLoading(false);
        setCompareRetireRunLoading(false);
      }
    }
  }, [
    userId,
    compareLeftSel,
    compareRightSel,
    compareGrowthForm,
    compareRetireForm,
    buildIntakeFromFormState,
    addMessage,
  ]);

  /** Run growth then retirement backtest in one sequence (hydrate charts after open / after intake update). */
  const runLifePlannerSequentialBacktests = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !compareLeftSel || !compareRightSel || !compareGrowthForm || !compareRetireForm) return null;
    const t0 = compareConnectBacktestTokenRef.current;
    setCompareGrowthRunLoading(true);
    setCompareRetireRunLoading(false);
    setCompareRetireSyncMessage(null);
    try {
      const intake = buildIntakeFromFormState(compareGrowthForm);
      const res = await postJson(`/api/portfolio/saved/${encodeURIComponent(compareLeftSel.portfolioId)}/backtest`, {
        user_id: uid,
        intake,
      });
      if (t0 !== compareConnectBacktestTokenRef.current) return null;
      setCompareGrowthArtifacts(res.artifacts || null);
      const p50 = extractGrowthTerminalValueP50(res.artifacts);
      const mergePrev = compareRetireForm ?? { ...defaultFormState };
      const mergedForRetire = mergeRetirementFormAfterGrowthBacktest(compareGrowthForm, mergePrev, p50);
      setCompareRetireForm(mergedForRetire);
      setCompareRetireRunLoading(true);
      const intakeR = buildIntakeFromFormState(mergedForRetire);
      const resR = await postJson(`/api/portfolio/saved/${encodeURIComponent(compareRightSel.portfolioId)}/backtest`, {
        user_id: uid,
        intake: intakeR,
      });
      if (t0 !== compareConnectBacktestTokenRef.current) return null;
      setCompareRetireArtifacts(resR.artifacts || null);
      return { growthArtifacts: res.artifacts || null, retireArtifacts: resR.artifacts || null };
    } catch (err) {
      if (t0 === compareConnectBacktestTokenRef.current) {
        addMessage("error", err.message || "Backtest failed");
      }
      return null;
    } finally {
      if (t0 === compareConnectBacktestTokenRef.current) {
        setCompareGrowthRunLoading(false);
        setCompareRetireRunLoading(false);
      }
    }
  }, [
    userId,
    compareLeftSel,
    compareRightSel,
    compareGrowthForm,
    compareRetireForm,
    buildIntakeFromFormState,
    addMessage,
  ]);

  useEffect(() => {
    if (view !== "connectGrowthRetire" || !selectedLifeScenarioId || !connectLifePlannerFrozen) {
      return;
    }
    if (!compareConnectSelectionKey || compareHydratingRef.current) {
      return;
    }
    if (
      compareBacktestArtifactsReady(compareGrowthArtifactsRef.current) &&
      compareBacktestArtifactsReady(compareRetireArtifactsRef.current)
    ) {
      return;
    }
    if (compareGrowthRunLoading || compareRetireRunLoading) return;
    if (connectChartsHydrateInFlightRef.current) return;
    const lid = selectedLifeScenarioId;
    if (connectChartsHydrateAttemptsRef.current.lifeId !== lid) {
      connectChartsHydrateAttemptsRef.current = { lifeId: lid, count: 0 };
    }
    if (connectChartsHydrateAttemptsRef.current.count >= 5) return;
    connectChartsHydrateAttemptsRef.current.count += 1;
    connectChartsHydrateInFlightRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        await runLifePlannerSequentialBacktests();
      } finally {
        if (!cancelled) connectChartsHydrateInFlightRef.current = false;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    view,
    selectedLifeScenarioId,
    connectLifePlannerFrozen,
    compareConnectSelectionKey,
    compareGrowthRunLoading,
    compareRetireRunLoading,
    runLifePlannerSequentialBacktests,
  ]);

  const confirmDeleteLifeScenario = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !selectedLifeScenarioId) return;
    try {
      await deleteJson(
        `/api/life-scenario/${encodeURIComponent(selectedLifeScenarioId)}?user_id=${encodeURIComponent(uid)}`,
      );
      setShowLifeScenarioDeleteConfirm(false);
      fetchSavedScenarios(uid);
      fetchSavedLifeScenarios(uid);
      compareConnectBacktestTokenRef.current += 1;
      connectChartsHydrateInFlightRef.current = false;
      setSelectedLifeScenarioId(null);
      setConnectLinkedGrowthScenarioId(null);
      setConnectLinkedRetirementScenarioId(null);
      setConnectFrozenGrowthMedianUsd(null);
      setConnectLifePlannerFrozen(false);
      setCompareLeftSel(null);
      setCompareRightSel(null);
      setCompareGrowthForm(null);
      setCompareRetireForm(null);
      setCompareGrowthArtifacts(null);
      setCompareRetireArtifacts(null);
      setCompareNotice(null);
      setCompareRetireSyncMessage(null);
      setConnectPairScenarioError(null);
      setConnectPairScenarioSuccess(null);
      setConnectLifeScenarioNameInput("");
      addMessage("assistant", "Life scenario deleted.", null, null);
    } catch (err) {
      addMessage("error", err.message || "Could not delete life scenario");
    }
  }, [userId, selectedLifeScenarioId, fetchSavedScenarios, fetchSavedLifeScenarios, addMessage]);

  /** Save left + right intake as one named life scenario (two underlying saved scenarios + life_scenarios row). */
  const handleConnectSavePairScenarios = useCallback(
    async (e) => {
      e?.preventDefault();
      const uid = userId ?? localStorage.getItem(USER_ID_KEY);
      if (!uid || !compareLeftSel || !compareRightSel || !compareGrowthForm || !compareRetireForm) return;
      if (connectLifePlannerFrozen) return;
      const isUpdateExisting =
        !!(selectedLifeScenarioId && connectLinkedGrowthScenarioId && connectLinkedRetirementScenarioId);
      if (
        !isUpdateExisting &&
        Array.isArray(savedLifeScenarios) &&
        savedLifeScenarios.length >= 1
      ) {
        const msg =
          "You can only keep one life plan. Open your saved plan in Life planner and use Delete, then save this one.";
        setConnectPairScenarioError(msg);
        addMessage("error", msg);
        return;
      }
      setConnectPairScenarioSaving(true);
      setConnectPairScenarioError(null);
      setConnectPairScenarioSuccess(null);
      const lifeName = (connectLifeScenarioNameInput || "").trim() || "Life scenario";
      const intakeG = buildIntakeFromFormState(compareGrowthForm);
      const intakeR = buildIntakeFromFormState(compareRetireForm);
      const trimSid = (id) => {
        if (id == null) return undefined;
        const s = String(id).trim();
        return s || undefined;
      };
      /** Drop stale scenario ids that do not belong to the selected portfolio (avoids wrong reuse on save). */
      const scenarioIdIfBelongsToPortfolio = (sel) => {
        const sid = trimSid(sel?.scenarioId);
        if (!sid || !sel?.portfolioId) return undefined;
        const row = Array.isArray(savedScenarios)
          ? savedScenarios.find((s) => String(s.scenario_id) === String(sid))
          : null;
        if (!row || String(row.portfolio_id) !== String(sel.portfolioId)) return undefined;
        return sid;
      };
      try {
        if (selectedLifeScenarioId && connectLinkedGrowthScenarioId && connectLinkedRetirementScenarioId) {
          await putJson(`/api/life-scenario/${encodeURIComponent(selectedLifeScenarioId)}/planner-intakes`, {
            user_id: uid,
            growth_intake: intakeG,
            retirement_intake: intakeR,
            name: lifeName,
          });
          fetchSavedScenarios(uid);
          fetchSavedLifeScenarios(uid);
          setConnectPairScenarioSuccess(`Updated life scenario “${lifeName}”.`);
          addMessage("assistant", `Updated life scenario “${lifeName}”.`, null, null);
          const seq = await runLifePlannerSequentialBacktests();
          const newFrozen = extractGrowthTerminalValueP50(seq?.growthArtifacts);
          const newRetirePct = extractRetirementSuccessPercentForDial(seq?.retireArtifacts);
          if (
            (newFrozen != null && Number.isFinite(newFrozen) && newFrozen > 0) ||
            newRetirePct != null
          ) {
            await putJson(`/api/life-scenario/${encodeURIComponent(selectedLifeScenarioId)}/frozen-growth-median`, {
              user_id: uid,
              frozen_growth_median_at_retirement_usd:
                newFrozen != null && Number.isFinite(newFrozen) && newFrozen > 0 ? newFrozen : undefined,
              retirement_success_percent: newRetirePct != null ? newRetirePct : undefined,
            });
            await fetchSavedLifeScenarios(uid);
          }
          await fetchSavedPortfolios(uid, true);
          resetOpenPlannerLanding();
        } else {
          let seq = null;
          if (
            !compareBacktestArtifactsReady(compareGrowthArtifacts) ||
            !compareBacktestArtifactsReady(compareRetireArtifacts)
          ) {
            seq = await runLifePlannerSequentialBacktests();
          }
          const frozenMedian = extractGrowthTerminalValueP50(
            seq?.growthArtifacts ?? compareGrowthArtifacts,
          );
          const retireDialPct = extractRetirementSuccessPercentForDial(
            seq?.retireArtifacts ?? compareRetireArtifacts,
          );
          const res = await postJson("/api/life-scenario/save", {
            user_id: uid,
            name: lifeName,
            growth_portfolio_id: compareLeftSel.portfolioId,
            retirement_portfolio_id: compareRightSel.portfolioId,
            growth_intake: intakeG,
            retirement_intake: intakeR,
            frozen_growth_median_at_retirement_usd:
              frozenMedian != null && Number.isFinite(frozenMedian) && frozenMedian > 0 ? frozenMedian : undefined,
            retirement_success_percent: retireDialPct != null ? retireDialPct : undefined,
            // Reuse scenario rows only when id matches the dropped portfolio (scenario_id ≠ portfolio_id; both must be consistent).
            growth_scenario_id: scenarioIdIfBelongsToPortfolio(compareLeftSel),
            retirement_scenario_id: scenarioIdIfBelongsToPortfolio(compareRightSel),
          });
          resetOpenPlannerLanding();
          fetchSavedScenarios(uid);
          fetchSavedLifeScenarios(uid);
          await fetchSavedPortfolios(uid, true);
          const disp = res.name || lifeName;
          setConnectPairScenarioSuccess(`Saved life scenario “${disp}”.`);
          addMessage(
            "assistant",
            `Saved life scenario “${disp}”. Open it anytime from Life planner in the sidebar.`,
            null,
            null,
          );
        }
      } catch (err) {
        const msg = err.message || "Failed to save life scenario";
        setConnectPairScenarioError(msg);
        addMessage("error", msg);
      } finally {
        setConnectPairScenarioSaving(false);
      }
    },
    [
      userId,
      compareLeftSel,
      compareRightSel,
      compareGrowthForm,
      compareRetireForm,
      connectLifeScenarioNameInput,
      connectLifePlannerFrozen,
      selectedLifeScenarioId,
      savedLifeScenarios,
      savedScenarios,
      connectLinkedGrowthScenarioId,
      connectLinkedRetirementScenarioId,
      buildIntakeFromFormState,
      fetchSavedScenarios,
      fetchSavedLifeScenarios,
      fetchSavedPortfolios,
      runLifePlannerSequentialBacktests,
      compareGrowthArtifacts,
      resetOpenPlannerLanding,
    ],
  );

  const lifePlannerGrowthPortfolioValueUsd = useMemo(() => {
    if (!compareLeftSel?.portfolioId) return null;
    const p = savedPortfolios.find((x) => x.portfolio_id === compareLeftSel.portfolioId);
    const v = latestPortfolioUsd(p);
    return v != null && Number.isFinite(Number(v)) ? Number(v) : null;
  }, [savedPortfolios, compareLeftSel]);

  const welcomeGoalFundedPercent = useMemo(
    () => computeGoalFundedPercent(connectFrozenGrowthMedianUsd, lifePlannerGrowthPortfolioValueUsd),
    [connectFrozenGrowthMedianUsd, lifePlannerGrowthPortfolioValueUsd],
  );
  const welcomeRetirementSuccessPercentDial = useMemo(
    () => extractRetirementSuccessPercentForDial(compareRetireArtifacts),
    [compareRetireArtifacts],
  );

  /** Life row used for sidebar dials: prefer the open/selected life plan so multiple saved plans stay distinct. */
  const sidebarLifeDialSourceRow = useMemo(() => {
    if (!Array.isArray(savedLifeScenarios) || savedLifeScenarios.length === 0) return null;
    if (selectedLifeScenarioId) {
      const hit = savedLifeScenarios.find((ls) => String(ls.life_scenario_id) === String(selectedLifeScenarioId));
      if (hit) return hit;
    }
    return savedLifeScenarios[0];
  }, [savedLifeScenarios, selectedLifeScenarioId]);

  /** Per-life frozen growth median + retirement success from API (each life_scenarios row is separate). */
  const savedLifeScenarioDialSnapshot = useMemo(() => {
    const ls = sidebarLifeDialSourceRow;
    if (!ls) return { goal: null, retire: null };
    const fz = ls.frozen_growth_median_at_retirement_usd;
    const gpId = ls.growth_portfolio_id;
    const pv =
      gpId && Array.isArray(savedPortfolios) && savedPortfolios.length
        ? latestPortfolioUsd(savedPortfolios.find((p) => String(p.portfolio_id) === String(gpId)))
        : null;
    const goal = computeGoalFundedPercent(fz, pv != null && pv !== "" ? Number(pv) : null);
    const rp = ls.retirement_success_percent;
    const retire =
      rp != null && Number.isFinite(Number(rp)) ? Math.min(100, Math.max(0, Number(rp))) : null;
    return { goal, retire };
  }, [sidebarLifeDialSourceRow, savedPortfolios]);

  /** Life-owned snapshot scenarios stay in DB for intake/backtests but are hidden under Portfolio (life plan is portfolio-based in the sidebar). */
  const lifePlannerOwnedScenarioIds = useMemo(() => {
    const set = new Set();
    if (!Array.isArray(savedLifeScenarios)) return set;
    for (const ls of savedLifeScenarios) {
      if (Number(ls.life_owns_growth_scenario) === 1 && ls.growth_scenario_id) set.add(ls.growth_scenario_id);
      if (Number(ls.life_owns_retirement_scenario) === 1 && ls.retirement_scenario_id) set.add(ls.retirement_scenario_id);
    }
    return set;
  }, [savedLifeScenarios]);

  const sidebarGoalFundedPercent = welcomeGoalFundedPercent ?? savedLifeScenarioDialSnapshot.goal;
  const sidebarRetirementSuccessPercent =
    welcomeRetirementSuccessPercentDial ?? savedLifeScenarioDialSnapshot.retire;

  const handleSbsContinue = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !sbsLeftSel || !sbsRightSel) return;
    const t0 = sbsBacktestTokenRef.current;
    setSbsLeftLoading(true);
    setSbsRightLoading(true);
    try {
      const runSide = async (sel) => {
        const form = await loadFormFromCompareSel(sel);
        const intake = buildIntakeFromFormState(form);
        const res = await postJson(`/api/portfolio/saved/${encodeURIComponent(sel.portfolioId)}/backtest`, {
          user_id: uid,
          intake,
        });
        return res.artifacts || null;
      };
      const [leftOut, rightOut] = await Promise.allSettled([runSide(sbsLeftSel), runSide(sbsRightSel)]);
      if (t0 !== sbsBacktestTokenRef.current) return;
      if (leftOut.status === "fulfilled") setSbsArtLeft(leftOut.value);
      else {
        const r = leftOut.reason;
        addMessage("error", r?.message || String(r || "Left backtest failed"));
      }
      if (rightOut.status === "fulfilled") setSbsArtRight(rightOut.value);
      else {
        const r = rightOut.reason;
        addMessage("error", r?.message || String(r || "Right backtest failed"));
      }
    } finally {
      setSbsLeftLoading(false);
      setSbsRightLoading(false);
    }
  }, [userId, sbsLeftSel, sbsRightSel, loadFormFromCompareSel, buildIntakeFromFormState]);

  const confirmDeleteScenario = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    const pid = selectedPortfolioId;
    if (!uid || !selectedScenarioId) return;
    try {
      await deleteJson(
        `/api/scenario/${encodeURIComponent(selectedScenarioId)}?user_id=${encodeURIComponent(uid)}`,
      );
      setShowScenarioDeleteConfirm(false);
      setSelectedScenarioId(null);
      setSelectedScenarioRow(null);
      fetchSavedScenarios(uid);
      fetchSavedLifeScenarios(uid);
      if (pid) {
        handlePortfolioClick(pid);
      } else {
        setView("portfolio");
      }
    } catch (err) {
      addMessage("error", err.message || "Delete failed");
    }
  }, [userId, selectedScenarioId, selectedPortfolioId, handlePortfolioClick, fetchSavedScenarios, fetchSavedLifeScenarios]);

  const confirmDeleteSavedPortfolio = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !selectedPortfolioId) return;
    try {
      const data = await deleteJson(
        `/api/portfolio/saved/${encodeURIComponent(selectedPortfolioId)}?user_id=${encodeURIComponent(uid)}`,
      );
      setShowPortfolioDeleteConfirm(false);
      setSelectedPortfolioId(null);
      setSelectedPortfolioRow(null);
      setPortfolioViewData(null);
      if (data.remaining_portfolios === 0) {
        setFormState({ ...defaultFormState });
        localStorage.removeItem(FORM_STATE_KEY);
        setUserFilledIntakeForm(false);
        setProfileSaved(false);
      }
      setView("loggedInOptions");
      fetchSavedPortfolios(uid);
      fetchSavedScenarios(uid);
      fetchSavedLifeScenarios(uid);
      fetchNetWorthSidebar(uid);
    } catch (err) {
      addMessage("error", err.message || "Delete failed");
    }
  }, [userId, selectedPortfolioId, fetchSavedPortfolios, fetchSavedScenarios, fetchSavedLifeScenarios, fetchNetWorthSidebar]);

  const handleSavePortfolioComposition = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !selectedPortfolioId) return;
    const m = new Map();
    for (const r of portfolioUpdateRows) {
      const t = String(r.ticker || "").trim().toUpperCase();
      if (!t) continue;
      const w = parseFloat(String(r.weight ?? "").replace(/,/g, ""));
      if (!Number.isFinite(w) || w <= 0) {
        setPortfolioUpdateError(`Enter a positive relative weight for ${t}.`);
        return;
      }
      m.set(t, (m.get(t) || 0) + w);
    }
    if (!m.size) {
      setPortfolioUpdateError("Add at least one ticker with a positive weight.");
      return;
    }
    const raw = Array.from(m.entries());
    const total = raw.reduce((s, [, v]) => s + v, 0);
    const ticker_weights = Object.fromEntries(raw.map(([t, v]) => [t, v / total]));
    const saveTok = portfolioViewLoadTokenRef.current;
    setPortfolioUpdateSaving(true);
    setPortfolioUpdateError("");
    setPortfolioViewLoading(true);
    try {
      const intake = buildIntakeFromFormState(formState);
      const res = await putJson(
        `/api/portfolio/saved/${encodeURIComponent(selectedPortfolioId)}/composition`,
        { user_id: uid, ticker_weights, intake },
      );
      if (saveTok !== portfolioViewLoadTokenRef.current) {
        return;
      }
      setSelectedPortfolioRow(res.portfolio);
      portfolioValuationCacheRef.current = {
        history: Array.isArray(res.portfolio?.valuation_history) ? res.portfolio.valuation_history : [],
        asOf: res.portfolio?.valuation_as_of ?? null,
      };
      setPortfolioViewData({ portfolio: res.portfolio, artifacts: res.artifacts, agent: res.agent });
      handleArtifacts({ artifacts: res.artifacts });
      setShowPortfolioUpdateComposition(false);
      fetchSavedPortfolios(uid);
      fetchNetWorthSidebar(uid);
      try {
        await postJson("/api/portfolio", { session_id: sessionId, holdings: ticker_weights });
      } catch {
        /* non-fatal */
      }
    } catch (err) {
      setPortfolioUpdateError(err.message || "Could not update portfolio");
    } finally {
      setPortfolioUpdateSaving(false);
      setPortfolioViewLoading(false);
    }
  }, [
    userId,
    selectedPortfolioId,
    portfolioUpdateRows,
    formState,
    sessionId,
    fetchSavedPortfolios,
    fetchNetWorthSidebar,
  ]);

  const openWhatIfFromPortfolio = useCallback(async () => {
    const row = selectedPortfolioRow;
    const weights = row?.portfolio_ticker_weights;
    if (!weights || typeof weights !== "object") return;
    setShowPortfolioUpdateComposition(false);
    setLastPortfolioComposition(weights);
    if (row.portfolio_sector_weights) setLastPortfolioSectors(row.portfolio_sector_weights);
    if (row.portfolio_industry_weights) setLastPortfolioIndustries(row.portfolio_industry_weights);
    try {
      await postJson("/api/portfolio", { session_id: sessionId, holdings: weights });
    } catch (e) {
      console.warn(e);
    }
    setPortfolioWhatIfMode(true);
    setProfileSaved(false);
  }, [selectedPortfolioRow, sessionId]);

  const saveFormStateToStorage = (next) => {
    const toSave = next || formState;
    const withoutWhatIf = formStateWithoutWhatIf(toSave);
    localStorage.setItem(FORM_STATE_KEY, JSON.stringify(withoutWhatIf));
  };

  const handleAnalyzeComputedPortfolioTotal = (total) => {
    if (!(typeof total === "number" && Number.isFinite(total) && total > 0)) return;
    const formatted = formatAnalyzeTotalForInvestmentField(total);
    if (!formatted) return;
    setLastPortfolioValue(total);
    setFormState((s) => {
      const next = { ...s, investmentValue: formatted };
      saveFormStateToStorage(next);
      return next;
    });
  };

  const handleIntakeSubmit = async (e) => {
    e.preventDefault();
    const mand = validateMandatoryIntakeFields(formState);
    if (!mand.ok) {
      setIntakeFormError(INTAKE_REQUIRED_FIELDS_MESSAGE);
      return;
    }
    setIntakeFormError(null);
    const retirementTimelineSelf = formState.retirementTimelineSelf?.trim() || "not specified";
    const retirementTimelinePartner = formState.retirementTimelinePartner?.trim() || "not specified";
    const planningFor = formState.planningFor || "self";
    const investmentValue = formState.investmentValue?.trim() || "";
    const monthlyContribution = formState.monthlyContribution?.trim() || "";
    const otherNotes = formState.otherNotes?.trim() || "none";
    const monthlyExpense = formState.monthlyExpense?.trim() || "";
    const msg = buildFullIntakeNarrativeFromFormState(formState);
    setSavedIntakeMessage(msg);
    setUserFilledIntakeForm(true);
    addMessage("user", msg);
    saveFormStateToStorage();

    const initialVal = parseAmount(investmentValue);
    const monthlySav = parseAmount(monthlyContribution);
    const bothRetired = formState.retirementStatus === "both_retired";
    const horizonYr = inferHorizonYears({
      planningFor,
      retirementStatus: formState.retirementStatus,
      retirementTimelineSelf: retirementTimelineSelf === "not specified" ? "" : retirementTimelineSelf,
      retirementTimelinePartner:
        retirementTimelinePartner === "not specified" ? "" : retirementTimelinePartner,
    });
    const monthlyExpenseVal = parseAmount(monthlyExpense);
    const birthDates = [];
    const birthYear1 = parseInt(formState.birthYear1, 10);
    const birthMonth1 = parseInt(formState.birthMonth1, 10);
    const birthYear2 = parseInt(formState.birthYear2, 10);
    const birthMonth2 = parseInt(formState.birthMonth2, 10);
    if (birthYear1 >= 1920 && birthYear1 <= 2010) {
      birthDates.push({ year: birthYear1, month: (birthMonth1 >= 1 && birthMonth1 <= 12) ? birthMonth1 : 6 });
    }
    if (planningFor === "couple" && birthYear2 >= 1920 && birthYear2 <= 2010) {
      birthDates.push({ year: birthYear2, month: (birthMonth2 >= 1 && birthMonth2 <= 12) ? birthMonth2 : 6 });
    }
    const displayUnit = parseDisplayUnit(investmentValue) || null;
    try {
      await postJson("/api/session/intake-data", {
        session_id: sessionId,
        initial_value: initialVal,
        monthly_savings: monthlySav,
        horizon_years: bothRetired ? 0 : horizonYr ?? undefined,
        planning_for: planningFor,
        birth_dates: birthDates,
        current_monthly_expense: monthlyExpenseVal,
        display_unit: displayUnit,
        spending: spendingTextFromFormState(formState),
        retirement_status: formState.retirementStatus,
        retirement_timeline_self: retirementTimelineSelf !== "not specified" ? retirementTimelineSelf : undefined,
        retirement_timeline_partner: retirementTimelinePartner !== "not specified" ? retirementTimelinePartner : undefined,
        country: "USA",
        state: formState.state?.trim() || undefined,
      inflation_assumption: (v => Number.isFinite(v) ? v : 3)(parseFloat(formState.inflationAssumption)),
    });
    } catch (err) {
      console.warn("Failed to store intake data:", err);
    }

    const intakePayload = {
      initial_value: initialVal,
      monthly_savings: monthlySav,
      horizon_years: bothRetired ? 0 : horizonYr ?? undefined,
      planning_for: planningFor,
      birth_dates: birthDates,
      current_monthly_expense: monthlyExpenseVal,
      display_unit: displayUnit,
      retirement_status: formState.retirementStatus,
      retirement_timeline_self: retirementTimelineSelf !== "not specified" ? retirementTimelineSelf : undefined,
      retirement_timeline_partner: retirementTimelinePartner !== "not specified" ? retirementTimelinePartner : undefined,
      country: "USA",
      state: formState.state?.trim() || undefined,
      inflation_assumption: (v => Number.isFinite(v) ? v : 3)(parseFloat(formState.inflationAssumption)),
      risk: formState.risk?.trim() || "medium",
      spending: spendingTextFromFormState(formState),
      other_notes: formState.otherNotes?.trim() || undefined,
    };

    if (pendingLoggedInAction === "growth") {
      setPendingLoggedInAction(null);
      setView("chat");
      addMessage("assistant", "Let me build your growth portfolios. One moment...");
      setIsTyping(true);
      try {
        const data = await postMoneyManager(
          { session_id: sessionId, message: msg, intake: intakePayload },
          "growth",
        );
        if (data) addChatResponse(data);
      } catch (err) {
        addMessage("error", err.message);
      } finally {
        setIsTyping(false);
      }
    } else if (pendingLoggedInAction === "retirement") {
      setPendingLoggedInAction(null);
      setView("chat");
      const retirementLine = "Work on retirement portfolio.";
      const apiMessage = `${msg}\n\n${retirementLine}`;
      addMessage("assistant", "I am Panda and I will help you with your retirement planning. Let me build your retirement portfolios. One moment...", null, "Panda");
      setIsTyping(true);
      try {
        const data = await postMoneyManager(
          { session_id: sessionId, message: apiMessage, intake: intakePayload },
          "retirement",
        );
        if (data) addChatResponse(data);
      } catch (err) {
        addMessage("error", err.message);
      } finally {
        setIsTyping(false);
      }
    } else if (!userId) {
      setAuthScreenDefaultTab("login");
      setAuthCancelView("intake");
      setAuthPostLoginView("loggedInOptions");
      addMessage(
        "assistant",
        "Thanks for your details. Sign in to continue, or switch to Register if you need an account.",
      );
      setView("auth");
    } else {
      addMessage("assistant", "Great, thanks! What would you like to do?");
      setView("welcomeOptions");
    }
  };

  const handleLoggedInIntakeSave = async (e) => {
    e.preventDefault();
    if (view === "portfolio" && !portfolioWhatIfMode) return false;
    setUserFilledIntakeForm(true);
    const intake = getIntakeFromFormOrState();
    if (!intake) return false;
    try {
      await postJson("/api/session/intake-data", {
        session_id: sessionId,
        initial_value: intake.initial_value,
        monthly_savings: intake.monthly_savings,
        horizon_years: intake.horizon_years,
        planning_for: intake.planning_for,
        birth_dates: intake.birth_dates,
        current_monthly_expense: intake.current_monthly_expense,
        display_unit: intake.display_unit,
        spending: intake.spending,
        retirement_status: intake.retirement_status,
        retirement_timeline_self: intake.retirement_timeline_self,
        retirement_timeline_partner: intake.retirement_timeline_partner,
        country: intake.country,
        state: intake.state,
        inflation_assumption: intake.inflation_assumption,
      });
      if (userId) {
        if (view === "portfolio" && selectedPortfolioId) {
          // What-if Continue: do NOT persist to portfolio or localStorage.
          // Portfolio intake stays as saved; persist paired intakes only via Life planner.
          setProfileSaved(false);
        } else {
          await postJson("/api/user/intake", { user_id: userId, intake: intakeWithoutWhatIf(intake) });
          saveFormStateToStorage();
          setProfileSaved(false);
        }
      } else {
        saveFormStateToStorage();
        setProfileSaved(true);
      }
      return true;
    } catch (err) {
      addMessage("error", err.message);
      return false;
    }
  };

  /** Saved portfolio what-if: persist intake snapshot, run backtest with new parameters, then lock the form again. */
  const handlePortfolioWhatIfContinue = async (e) => {
    e.preventDefault();
    if (view === "portfolio" && !portfolioWhatIfMode) return;
    if (portfolioViewLoading) return;
    setPortfolioViewLoading(true);
    try {
      const success = await handleLoggedInIntakeSave({ preventDefault: () => {} });
      if (success) {
        await runPortfolioMonteCarloBacktest();
        setPortfolioWhatIfMode(false);
      }
    } finally {
      setPortfolioViewLoading(false);
    }
  };

  /** Save current intake (including what-if edits) as a new named scenario under this portfolio. */
  const handlePortfolioSaveAsScenario = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !selectedPortfolioId || selectedScenarioId) return;
    const base = (portfolioSaveAsName || "").trim();
    if (!base) {
      addMessage("error", "Enter a scenario name before saving.");
      return;
    }
    setPortfolioSaveAsSaving(true);
    try {
      const intake = buildIntakeFromFormState(formState);
      const res = await postJson("/api/scenario/save", {
        user_id: uid,
        portfolio_id: selectedPortfolioId,
        scenario_name: base,
        intake,
      });
      await fetchSavedScenarios(uid);
      setPortfolioSaveAsName("");
      const disp = (res && res.scenario_name) || base;
      addMessage("assistant", `Scenario saved as “${disp}”. Open it under this portfolio in the sidebar.`, null, null);
    } catch (err) {
      addMessage("error", err.message || "Could not save scenario");
    } finally {
      setPortfolioSaveAsSaving(false);
    }
  }, [
    userId,
    selectedPortfolioId,
    selectedScenarioId,
    portfolioSaveAsName,
    formState,
    buildIntakeFromFormState,
    fetchSavedScenarios,
    addMessage,
  ]);

  /** Persist intake + what-if for the scenario currently open in the sidebar (PUT existing row). */
  const handlePortfolioUpdateScenario = useCallback(async () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid || !selectedScenarioId) return;
    setPortfolioUpdateScenarioSaving(true);
    try {
      const intake = buildIntakeFromFormState(formStateRef.current);
      await putJson(`/api/scenario/${encodeURIComponent(selectedScenarioId)}`, {
        user_id: uid,
        intake,
      });
      await fetchSavedScenarios(uid);
      const refreshed = await getJson(
        `/api/scenario/${encodeURIComponent(selectedScenarioId)}?include_backtest=true`,
      );
      if (refreshed && typeof refreshed === "object") {
        setSelectedScenarioRow(refreshed);
      }
      const nm = (refreshed && refreshed.scenario_name) || selectedScenarioRow?.scenario_name || selectedScenarioId;
      addMessage("assistant", `Saved intake for scenario “${nm}”.`, null, null);
    } catch (err) {
      addMessage("error", err.message || "Could not save scenario");
    } finally {
      setPortfolioUpdateScenarioSaving(false);
    }
  }, [
    userId,
    selectedScenarioId,
    selectedScenarioRow?.scenario_name,
    fetchSavedScenarios,
    addMessage,
  ]);

  /** Logged-in profile home: same as anonymous intake → Continue → welcome options (growth / retirement / analyze). */
  const handleLoggedInProfileContinue = async () => {
    const mand = validateMandatoryIntakeFields(formState);
    if (!mand.ok) {
      setIntakeFormError(INTAKE_REQUIRED_FIELDS_MESSAGE);
      return;
    }
    setIntakeFormError(null);
    const ok = await handleLoggedInIntakeSave({ preventDefault: () => {} });
    if (ok) {
      setView("welcomeOptions");
      addMessage("assistant", "Great, thanks! What would you like to do?");
    }
  };

  const updateFormState = (updater) => {
    setProfileSaved(false);
    setFormState((prev) => (typeof updater === "function" ? updater(prev) : { ...prev, ...updater }));
  };

  /** Saved portfolio view: all fields locked until What-if; then only allowed keys are editable. */
  function portfolioIntakeFieldLocked(fieldKey) {
    if (view !== "portfolio") return false;
    if (!portfolioWhatIfMode) return true;
    const allowed = new Set(["monthlyExpense", "inflationAssumption", "bigSpendingRows", "investmentValue"]);
    if (selectedPortfolioRow?.portfolio_category === "retirement") {
      [
        "monthlyIncomeRows",
        "miscMonthlySpendingRows",
        "windfallInflowRows",
        "retirementEffectiveTaxRate",
        "retirementDiscretionaryMonthly",
        "retirementDiscretionaryMinPriorReturnPct",
        "retirementDiscretionaryStartAge",
        "retirementDiscretionaryEndAge",
      ].forEach((k) => allowed.add(k));
    }
    if (selectedPortfolioRow?.portfolio_category === "growth") {
      [
        "growthMonthlyIncomeRows",
        "growthMiscMonthlySpendingRows",
        "growthOneTimeInflowRows",
      ].forEach((k) => allowed.add(k));
    }
    return !allowed.has(fieldKey);
  }

  const handleOptionChoice = async (option) => {
    setView("chat");
    setCurrentIntent("money_manager");
    await postJson("/api/session/intent", { session_id: sessionId, intent: "general" });

    if (option === "growth" || option === "retirement") {
      setAwaitingPortfolios(true);
    }

    if (option === "growth") {
      activePortfolioFlowRef.current = "growth";
      try {
        let intake = userId ? (getIntakeFromForm() ?? (await getJson(`/api/user/intake?user_id=${encodeURIComponent(userId)}`).catch(() => null))) : getIntakeFromFormOrState();
        let msg = savedIntakeMessage?.trim() || "";
        let appendUserBubble = true;
        if (intake) {
          if (!msg) {
            const spendingNarr =
              (typeof intake.spending === "string" && intake.spending.trim()) ||
              bigSpendingNarrativeFromRows(Array.isArray(intake.big_spending_rows) ? intake.big_spending_rows : []) ||
              "no big spending expected";
            const infl = intake.inflation_assumption;
            const inflPct = infl != null && infl !== "" && Number.isFinite(Number(infl)) ? Number(infl) : 3;
            msg = [
              `Retirement status: ${intake.retirement_status || "both_working"}.`,
              `Planning for: ${intake.planning_for === "couple" ? "the two of us" : "myself"}.`,
              `Location: USA, ${intake.state || ""}.`,
              `Inflation assumption: ${inflPct}%.`,
              `Risk: ${intake.risk || "medium"}.`,
              `Big spending: ${spendingNarr}.`,
              `Initial investment amount to build growth portfolio: $${intake.initial_value || 1000}.`,
              `Monthly savings: $${intake.monthly_savings || 0}.`,
              `Monthly expense: $${intake.current_monthly_expense || 0}.`,
              `Other notes: ${intake.other_notes || "none"}.`,
              `Work on growth portfolio.`,
            ].join("\n");
            setSavedIntakeMessage(msg);
          } else {
            appendUserBubble = false;
          }
        } else if (userId) {
          setPendingLoggedInAction("growth");
          setView("intake");
          return;
        }
        if (!msg) msg = "Work on growth portfolio.";
        if (appendUserBubble) {
          addMessage("user", msg);
        }
        addMessage("assistant", "Let me build your growth portfolios. One moment...");
        setIsTyping(true);
        const data = await postMoneyManager({ session_id: sessionId, message: msg, intake }, "growth");
        if (data) addChatResponse(data);
      } catch (err) {
        setView(userId ? "loggedInOptions" : "intake");
        addMessage("error", err.message);
      } finally {
        setIsTyping(false);
        setAwaitingPortfolios(false);
      }
    } else if (option === "retirement") {
      try {
        let intake = userId ? (getIntakeFromForm() ?? (await getJson(`/api/user/intake?user_id=${encodeURIComponent(userId)}`).catch(() => null))) : getIntakeFromFormOrState();
        const retirementLine = "Work on retirement portfolio.";
        let msg = savedIntakeMessage?.trim() || "";
        if (msg) {
          msg = msg
            .replace(/\n*I want to build a growth portfolio for retirement\.?\s*$/i, "")
            .replace(/\n*Work on growth portfolio\.?\s*$/i, "")
            .trim();
        }
        activePortfolioFlowRef.current = "retirement";
        let appendUserBubble = true;
        if (intake) {
          if (!msg) {
            msg = buildFullIntakeNarrativeFromFormState(formState);
          } else {
            appendUserBubble = false;
          }
        } else if (userId) {
          setPendingLoggedInAction("retirement");
          setView("intake");
          return;
        }
        const apiMessage = msg ? `${msg}\n\n${retirementLine}` : retirementLine;
        if (appendUserBubble) {
          addMessage("user", apiMessage);
        }
        addMessage("assistant", "I am Panda and I will help you with your retirement planning. Let me build your retirement portfolios. One moment...", null, "Panda");
        setIsTyping(true);
        const data = await postMoneyManager(
          { session_id: sessionId, message: apiMessage, intake },
          "retirement",
        );
        if (data) addChatResponse(data);
      } catch (err) {
        addMessage("error", err.message);
      } finally {
        setIsTyping(false);
        setAwaitingPortfolios(false);
      }
    } else if (option === "analyze") {
      setView("analyze");
      addMessage("user", "I want to analyze my current portfolio");
      addMessage(
        "assistant",
        "Upload your portfolio as a CSV file. The CSV should have at least these columns: ticker, quantity, cost_basis."
      );
    }
  };

  const handleIntentSelection = async (intent) => {
    setCurrentIntent(intent);
    setView("chat");
    await postJson("/api/session/intent", { session_id: sessionId, intent });

    if (intent === "intake") {
      setAwaitingPortfolios(true);
      addMessage("assistant", "Let me build your portfolios. One moment...");
      setIsTyping(true);
      try {
        const payload = { session_id: sessionId, message: savedIntakeMessage };
        const intake = getIntakeFromFormOrState();
        if (intake) payload.intake = intake;
        const data = await postMoneyManager(payload, "growth");
        if (data) addChatResponse(data);
      } catch (err) {
        addMessage("error", err.message);
      } finally {
        setIsTyping(false);
        setAwaitingPortfolios(false);
      }
      return;
    }

    if (intent === "backtest") {
      addMessage("user", "Analyze existing portfolio");
      setView("analyze");
      try {
        const data = await postJson("/api/chat/intake", {
          session_id: sessionId,
          message: "assess risk on my existing portfolio",
        });
        addMessage("assistant", data.reply, data.artifacts, data.agent);
        handleArtifacts(data);
        const hasScenarios = data.artifacts?.scenarios?.length;
        if (hasScenarios) setView("chat");
      } catch (err) {
        addMessage("error", err.message);
      }
    }
  };

  const handleAuthLoginSuccess = useCallback(
    async (emailTrimmed, uid, opts = {}) => {
      const fromAccountModal = Boolean(opts?.fromAccountModal);
      const persistIntake = Boolean(opts?.persistIntake);
      localStorage.setItem(USER_ID_KEY, uid);
      localStorage.setItem(USER_EMAIL_KEY, emailTrimmed);
      setUserId(uid);
      setUserEmail(emailTrimmed);
      await fetchSavedPortfolios(uid);
      fetchNetWorthSidebar(uid);
      if (persistIntake) {
        try {
          const intake = getIntakeFromFormOrState();
          if (intake) {
            await postJson("/api/user/intake", { user_id: uid, intake: intakeWithoutWhatIf(intake) });
            saveFormStateToStorage();
          }
        } catch (e) {
          console.warn("Persist intake after registration:", e);
        }
      }
      if (fromAccountModal) {
        setAccountModalOpen(false);
        setAccountModalTab("login");
        setView("loggedInOptions");
      } else {
        setView(authPostLoginView);
      }
    },
    [fetchSavedPortfolios, fetchNetWorthSidebar, getIntakeFromFormOrState, saveFormStateToStorage, authPostLoginView],
  );

  const handleLogout = () => {
    localStorage.removeItem(USER_ID_KEY);
    localStorage.removeItem(USER_EMAIL_KEY);
    setUserId(null);
    setUserEmail(null);
    setSavedPortfolios([]);
    setProfileSaved(false);
    setPendingLoggedInAction(null);
    setSelectedPortfolioId(null);
    setSelectedPortfolioRow(null);
    setPortfolioViewData(null);
    setShowPortfolioDeleteConfirm(false);
    nextMessageIdRef.current = 2;
    setMessages(INITIAL_MESSAGES);
    setView((v) =>
      v === "compare" || v === "connectGrowthRetire" || v === "netWorth" || v === "portfolio" ? "intake" : v,
    );
  };

  const compareSessionUserId = userId ?? localStorage.getItem(USER_ID_KEY);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Mono:wght@300;400;500;600;700&display=swap');
        :root {
          --app-bg: #0a0a0a;
          --sidebar-bg: #0e0e0e;
          --surface: #111;
          --surface-input: #0e0e0e;
          --border: #1e1e1e;
          --border-soft: #1a1a1a;
          --border-top: #141414;
          --text: #e8e0d0;
          --text-muted: #888;
          --text-soft: #aaa;
          --text-dim: #666;
          --text-label: #555;
          --section: #3a3a3a;
          --scroll-track: #0a0a0a;
          --scroll-track-sidebar: #0e0e0e;
          --scroll-thumb: #2a2a2a;
          --scroll-thumb-hover: #3a3a3a;
          --fn-hover-bg: #16140e;
          --selected-bg: #1a1810;
          --avatar-user-bg: #1e1e1e;
          --avatar-user-border: #2a2a2a;
          --msg-time: #2a2a2a;
          --placeholder: #2e2e2e;
          --on-accent: #0a0a0a;
          --modal-overlay: rgba(0,0,0,0.7);
          --modal-bg: #111;
          --btn-secondary-border: #333;
          --bubble-assistant-text: #ccc;
          --user-block-border: #1a1a1a;
          --toggle-border: #1e1e1e;
          --toggle-fg: #555;
          --tick-chart: #555;
          --surface-elevated: #0a0a0a;
        }
        [data-theme="light"] {
          --app-bg: #f3f0e8;
          --sidebar-bg: #e8e4db;
          --surface: #fffcf7;
          --surface-input: #ffffff;
          --border: #cdc6b8;
          --border-soft: #c4bdb0;
          --border-top: #c4bdb0;
          --text: #1c1917;
          --text-muted: #57534e;
          --text-soft: #6b6560;
          --text-dim: #78716c;
          --text-label: #78716c;
          --section: #6b6560;
          --scroll-track: #ebe6dd;
          --scroll-track-sidebar: #e8e4db;
          --scroll-thumb: #b8b0a4;
          --scroll-thumb-hover: #9c9488;
          --fn-hover-bg: #ddd8cf;
          --selected-bg: #e5dcc8;
          --avatar-user-bg: #e7e2da;
          --avatar-user-border: #cfc8bc;
          --msg-time: #a8a29e;
          --placeholder: #a8a29e;
          --on-accent: #1c1917;
          --modal-overlay: rgba(0,0,0,0.4);
          --modal-bg: #fffcf7;
          --btn-secondary-border: #b8b0a4;
          --bubble-assistant-text: #44403c;
          --user-block-border: #c4bdb0;
          --toggle-border: #c4bdb0;
          --toggle-fg: #57534e;
          --tick-chart: #78716c;
          --surface-elevated: #ffffff;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body, #root { max-width: 100%; overflow-x: hidden; overflow-x: clip; }
        #root { min-height: 100dvh; display: flex; flex-direction: column; }
        body { font-family: 'DM Mono', monospace; background: var(--app-bg); color: var(--text); height: 100vh; height: 100dvh; overflow: hidden; width: 100%; }
        @keyframes typingBounce { 0%, 60%, 100% { transform: translateY(0); opacity: 0.4; } 30% { transform: translateY(-6px); opacity: 1; } }
        @keyframes fadeSlideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 1; } }
        .app-shell {
          display: flex;
          height: 100vh;
          height: 100dvh;
          width: 100%;
          max-width: 100%;
          overflow-x: clip;
          background: var(--app-bg);
        }
        .app-shell.is-mobile { overflow-x: clip; }
        .sidebar {
          width: 260px;
          min-width: 260px;
          height: 100vh;
          height: 100dvh;
          max-height: 100vh;
          max-height: 100dvh;
          background: var(--sidebar-bg);
          border-right: 1px solid var(--border);
          display: flex;
          flex-direction: column;
          padding: 28px 20px;
          gap: 28px;
          transition: width 0.3s ease, min-width 0.3s ease, padding 0.3s ease;
          overflow-x: hidden;
          overflow-y: auto;
          scrollbar-width: thin;
          scrollbar-color: var(--scroll-thumb) var(--scroll-track-sidebar);
        }
        .sidebar::-webkit-scrollbar { width: 6px; }
        .sidebar::-webkit-scrollbar-track { background: var(--scroll-track-sidebar); }
        .sidebar::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }
        .sidebar::-webkit-scrollbar-thumb:hover { background: var(--scroll-thumb-hover); }
        .sidebar.collapsed { width: 0; min-width: 0; padding: 0; overflow: hidden; }
        .user-block { display: flex; flex-direction: column; gap: 8px; padding-bottom: 16px; border-bottom: 1px solid var(--user-block-border); }
        .user-block__top {
          display: flex;
          flex-direction: row;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
        }
        .user-block__text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
        .user-greeting { font-family: 'Cormorant Garamond', serif; font-size: 11px; font-weight: 400; letter-spacing: 0.18em; text-transform: uppercase; color: var(--text-label); }
        .user-name { font-family: 'Cormorant Garamond', serif; font-size: 22px; font-weight: 500; color: var(--text); line-height: 1; }
        .user-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #c8a96e; background: #c8a96e18; border: 1px solid #c8a96e30; padding: 3px 8px; border-radius: 2px; width: fit-content; }
        .quick-scan-disclosure-wrap { margin-top: 2px; }
        .quick-scan-disclosure-wrap--inline { margin-top: 0; flex-shrink: 0; align-self: flex-start; }
        .quick-scan-disclosure-trigger {
          width: 100%;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 10px;
          margin-top: 10px;
          background: var(--surface);
          border: 1px solid var(--border-soft);
          border-radius: 3px;
          cursor: pointer;
          text-align: left;
          font-size: 11px;
          font-family: 'DM Mono', monospace;
          color: var(--text-soft);
          transition: border-color 0.2s, color 0.2s, background 0.2s;
        }
        .quick-scan-disclosure-trigger--icon {
          width: 34px;
          height: 34px;
          min-width: 34px;
          margin-top: 0;
          padding: 0;
          justify-content: center;
        }
        .quick-scan-disclosure-wrap--inline .quick-scan-disclosure-trigger--icon { margin-top: 0; }
        .quick-scan-disclosure-trigger:hover {
          border-color: #c8a96e44;
          color: #c8a96e;
          background: var(--fn-hover-bg);
        }
        .quick-scan-disclosure-trigger__icon {
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--text-muted);
        }
        .quick-scan-disclosure-trigger:hover .quick-scan-disclosure-trigger__icon { color: #c8a96e; }
        [data-theme="light"] .quick-scan-disclosure-trigger__icon { color: #57534e; }
        [data-theme="dark"] .quick-scan-disclosure-trigger__icon { color: #a8a29e; }
        .quick-scan-disclosure-trigger__label { flex: 1; min-width: 0; line-height: 1.35; }
        .btn-outline {
          flex: 1 1 0;
          min-width: 0;
          font-family: 'DM Mono', monospace;
          font-size: 12px;
          color: var(--text-dim);
          border: 1px solid var(--border-soft);
          background: var(--surface);
          padding: 8px 10px;
          border-radius: 3px;
          cursor: pointer;
          transition: border-color 0.2s, color 0.2s, background 0.2s;
        }
        .btn-outline:hover:not(:disabled) {
          border-color: #c8a96e40;
          color: #c8a96e;
          background: var(--fn-hover-bg);
        }
        .btn-outline:disabled { opacity: 0.45; cursor: not-allowed; }
        .btn-outline--active {
          border-color: #c8a96e55;
          background: var(--selected-bg);
          color: var(--text);
        }
        .auth-tab-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 14px; }
        .auth-google-wrap { display: flex; justify-content: center; margin: 0 0 12px; min-height: 44px; }
        .auth-or-divider { text-align: center; font-size: 11px; color: var(--text-muted); margin: 0 0 14px; letter-spacing: 0.04em; }
        .clickwrap-control {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          margin-top: 12px;
          font-size: 12px;
          line-height: 1.45;
          color: var(--text);
          cursor: pointer;
          font-family: 'DM Mono', monospace;
        }
        .clickwrap-control input[type="checkbox"] {
          width: 16px;
          height: 16px;
          min-width: 16px;
          margin-top: 2px;
          flex-shrink: 0;
          accent-color: #c8a96e;
          cursor: pointer;
          border-radius: 3px;
        }
        .clickwrap-control input:disabled { cursor: default; opacity: 0.55; }
        .quick-scan-disclosure-popover {
          background: var(--modal-bg);
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 12px 14px;
          box-shadow: 0 12px 48px rgba(0,0,0,0.4);
          font-size: 12px;
          line-height: 1.55;
          color: var(--text-soft);
        }
        [data-theme="light"] .quick-scan-disclosure-popover {
          box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
        }
        .quick-scan-disclosure-popover__title {
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--text-label);
          margin: 0 0 8px;
          font-weight: 700;
        }
        .quick-scan-disclosure-popover__body { margin: 0; color: var(--text-soft); }
        .section-label { font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--section); margin-bottom: 10px; }
        .sidebar-section { margin-bottom: 16px; }
        .sidebar-fn-list { display: flex; flex-direction: column; gap: 4px; }
        .sidebar-fn-item {
          width: 100%;
          text-align: left;
          background: var(--surface);
          border: 1px solid var(--border-soft);
          border-radius: 3px;
          padding: 8px 10px;
          font-size: 12px;
          font-family: 'DM Mono', monospace;
          color: var(--text-soft);
          cursor: pointer;
          transition: border-color 0.2s, color 0.2s, background 0.2s;
        }
        .sidebar-fn-item:hover { border-color: #c8a96e40; color: #c8a96e; background: var(--fn-hover-bg); }
        .sidebar-fn-item.selected { border-color: #c8a96e55; background: var(--selected-bg); color: var(--text); }
        .portfolio-list { display: flex; flex-direction: column; gap: 8px; }
        .portfolio-list-item {
          font-size: 12px;
          color: var(--text-soft);
          padding: 6px 10px;
          background: var(--surface);
          border: 1px solid var(--border-soft);
          border-radius: 3px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
        }
        .portfolio-list-item-label {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .portfolio-list-item.selected { border-color: #c8a96e55; background: var(--selected-bg); color: var(--text); }
        .portfolio-list-value { font-size: 10px; color: #c8a96e; }
        .portfolio-list-amount {
          font-size: 13px;
          font-weight: 700;
          color: #4ade80;
          flex-shrink: 0;
          font-variant-numeric: tabular-nums;
        }
        [data-theme="light"] .sidebar .portfolio-list-item {
          color: var(--text);
          border-color: var(--border);
        }
        [data-theme="light"] .sidebar .section-label {
          color: var(--text);
          opacity: 0.92;
        }
        [data-theme="light"] .sidebar .portfolio-list-amount {
          color: #15803d;
          font-weight: 700;
        }
        .sidebar-total { padding: 14px; background: var(--surface); border: 1px solid #c8a96e20; border-radius: 4px; }
        .sidebar-total-label { font-size: 9px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--text-label); margin-bottom: 5px; }
        .sidebar-total-value { font-family: 'Cormorant Garamond', serif; font-size: 24px; font-weight: 500; color: #c8a96e; }
        .new-session-btn { margin-top: 10px; width: 100%; padding: 8px 12px; font-size: 11px; letter-spacing: 0.05em; background: var(--border-soft); border: 1px solid #c8a96e40; border-radius: 3px; color: #c8a96e; cursor: pointer; }
        .new-session-btn:hover { background: var(--fn-hover-bg); border-color: #c8a96e60; }
        .main {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-width: 0;
          min-height: 0;
          width: 100%;
          max-width: 100%;
          background: var(--app-bg);
          position: relative;
          overflow-x: clip;
        }
        .main-body {
          flex: 1;
          min-height: 0;
          min-width: 0;
          max-width: 100%;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }
        .legal-sticky-footer {
          flex-shrink: 0;
          font-size: 9px;
          line-height: 1.2;
          color: var(--text-muted);
          padding: 4px 14px 5px;
          border-top: 1px solid var(--border-soft);
          background: rgba(10, 10, 10, 0.88);
          text-align: center;
        }
        [data-theme="light"] .legal-sticky-footer {
          background: rgba(255, 252, 247, 0.92);
        }
        .advisor-model-output-disclaimer {
          display: flex;
          align-items: flex-start;
          gap: 8px;
          margin-top: 10px;
          max-width: 68%;
          font-size: 10px;
          line-height: 1.4;
          color: var(--text-muted);
        }
        .message-row.assistant .advisor-model-output-disclaimer { align-self: flex-start; }
        .advisor-model-output-disclaimer__icon { flex-shrink: 0; margin-top: 1px; opacity: 0.75; }
        .advisor-model-output-disclaimer__text { min-width: 0; }
        .page-output-disclaimer { max-width: 100% !important; width: 100%; }
        .analyze-advisor-disclaimer { max-width: 100%; }
        .mr-brown-advisor-disclaimer { max-width: 100%; }
        .chat-scroll {
          flex: 1;
          min-height: 0;
          min-width: 0;
          max-width: 100%;
          overflow-x: clip;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
        }
        .chat-scroll::-webkit-scrollbar { width: 6px; }
        .chat-scroll::-webkit-scrollbar-track { background: var(--scroll-track); }
        .chat-scroll::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }
        .chat-scroll::-webkit-scrollbar-thumb:hover { background: var(--scroll-thumb-hover); }
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 18px 28px;
          border-bottom: 1px solid var(--border-top);
          background: var(--app-bg);
          flex-shrink: 0;
          width: 100%;
          max-width: 100%;
          box-sizing: border-box;
          overflow-x: clip;
          gap: 8px;
        }
        .topbar-left { display: flex; align-items: center; gap: 14px; min-width: 0; flex: 1 1 auto; }
        .topbar-left > div { min-width: 0; overflow: hidden; }
        .topbar-right { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; justify-content: flex-end; }
        .topbar-nav { display: flex; align-items: center; gap: 6px 16px; flex-wrap: wrap; }
        .topbar-nav-link { background: none; border: none; padding: 4px 0; font-family: 'DM Mono', monospace; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--text-muted); cursor: pointer; transition: color 0.2s; }
        .topbar-nav-link:hover { color: #c8a96e; }
        .theme-toggle-btn {
          background: transparent;
          border: 1px solid var(--toggle-border);
          border-radius: 3px;
          color: var(--text-muted);
          padding: 6px 10px;
          font-size: 11px;
          font-family: 'DM Mono', monospace;
          cursor: pointer;
          letter-spacing: 0.05em;
          transition: border-color 0.2s, color 0.2s, background 0.2s;
        }
        .theme-toggle-btn:hover { border-color: #c8a96e60; color: #c8a96e; background: var(--fn-hover-bg); }
        .login-btn { background: #c8a96e; color: var(--on-accent); border: none; padding: 8px 16px; border-radius: 4px; font-size: 12px; font-family: 'DM Mono', monospace; cursor: pointer; transition: all 0.2s; }
        .login-btn:hover { background: #d4b87e; }
        .user-menu { display: flex; align-items: center; gap: 12px; }
        .user-email-display { font-size: 11px; color: var(--text-muted); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .logout-btn { background: transparent; color: var(--text-dim); border: 1px solid var(--btn-secondary-border); padding: 6px 12px; border-radius: 3px; font-size: 11px; font-family: 'DM Mono', monospace; cursor: pointer; transition: all 0.2s; }
        .logout-btn:hover { border-color: #c8a96e55; color: var(--text-soft); }
        .modal-overlay { position: fixed; inset: 0; background: var(--modal-overlay); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .login-modal { background: var(--modal-bg); border: 1px solid var(--border); border-radius: 6px; padding: 24px; min-width: 320px; }
        .login-modal-title { font-family: 'Cormorant Garamond', serif; font-size: 18px; color: var(--text); margin-bottom: 20px; }
        .login-form label { display: block; margin-bottom: 14px; font-size: 11px; color: var(--text-muted); }
        .login-form input { width: 100%; padding: 10px 12px; background: var(--surface-input); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-family: 'DM Mono', monospace; font-size: 13px; margin-top: 4px; }
        .login-error { font-size: 11px; color: #eb5757; margin-bottom: 12px; }
        .login-modal-actions { display: flex; gap: 10px; margin-top: 18px; }
        .login-submit-btn {
          background: #c8a96e;
          color: var(--on-accent);
          border: none;
          padding: 10px 18px;
          border-radius: 4px;
          font-size: 12px;
          font-family: 'DM Mono', monospace;
          cursor: pointer;
          transition: background 0.2s, opacity 0.2s;
        }
        .login-submit-btn:hover:not(:disabled) { background: #d4b87e; }
        .login-submit-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .login-cancel-btn {
          background: transparent;
          color: var(--text-dim);
          border: 1px solid var(--btn-secondary-border);
          padding: 10px 18px;
          border-radius: 4px;
          font-size: 12px;
          font-family: 'DM Mono', monospace;
          cursor: pointer;
          transition: border-color 0.2s, color 0.2s, background 0.2s;
        }
        .login-cancel-btn:hover:not(:disabled) {
          border-color: #c8a96e55;
          color: var(--text-soft);
          background: var(--fn-hover-bg);
        }
        .toggle-btn { background: none; border: 1px solid var(--toggle-border); border-radius: 3px; color: var(--toggle-fg); width: 30px; height: 30px; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 14px; transition: all 0.2s; }
        .toggle-btn:hover { border-color: #c8a96e55; color: var(--text-soft); }
        .topbar-title { font-family: 'Cormorant Garamond', serif; font-size: 18px; font-weight: 500; color: var(--text); }
        .topbar-sub {
          margin-top: 2px;
          max-width: 36em;
          font-size: 11px;
          line-height: 1.35;
          color: var(--text-muted);
          letter-spacing: 0.02em;
          font-weight: 400;
        }
        .market-tickers { display: flex; gap: 20px; }
        .ticker { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
        .ticker-name { font-size: 9px; letter-spacing: 0.1em; color: var(--section); text-transform: uppercase; }
        .ticker-val { font-size: 12px; font-weight: 500; font-family: 'Cormorant Garamond', serif; }
        .ticker-val.pos { color: #6fcf97; } .ticker-val.neg { color: #eb5757; }
        .messages-area {
          padding: 28px;
          display: flex;
          flex-direction: column;
          gap: 24px;
          flex-shrink: 0;
          width: 100%;
          max-width: 100%;
          box-sizing: border-box;
          overflow-x: clip;
        }
        .messages-area.with-form-below { padding-bottom: 12px; }
        .message-row { display: flex; flex-direction: column; animation: fadeSlideIn 0.3s ease forwards; }
        .message-row.user { align-items: flex-end; } .message-row.assistant { align-items: flex-start; }
        .message-row.error .message-bubble { background: rgba(235, 87, 87, 0.08); border-color: rgba(235, 87, 87, 0.25); color: #eb5757; }
        .message-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
        .message-row.user .message-meta { flex-direction: row-reverse; }
        .message-avatar { width: 26px; height: 26px; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 600; flex-shrink: 0; }
        .message-row.assistant .message-avatar { background: #c8a96e18; border: 1px solid #c8a96e30; color: #c8a96e; font-family: 'Cormorant Garamond', serif; font-size: 12px; }
        .message-row.user .message-avatar { background: var(--avatar-user-bg); border: 1px solid var(--avatar-user-border); color: var(--text-muted); }
        .message-row.error .message-avatar { background: #eb575718; border: 1px solid #eb575730; color: #eb5757; }
        .message-sender { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--section); }
        .message-time { font-size: 9px; color: var(--msg-time); }
        .message-bubble {
          max-width: var(--assistant-content-max-width, 68%);
          padding: 14px 18px;
          border-radius: 4px;
          font-size: 13px;
          line-height: 1.7;
          white-space: pre-line;
          overflow-wrap: anywhere;
          word-break: break-word;
        }
        .message-row.assistant .message-bubble { background: var(--surface); border: 1px solid var(--border-soft); border-top-left-radius: 1px; color: var(--bubble-assistant-text); }
        .message-row.user .message-bubble { background: #c8a96e12; border: 1px solid #c8a96e25; border-top-right-radius: 1px; color: var(--text); }
        .typing-bubble { background: var(--surface); border: 1px solid var(--border-soft); padding: 12px 18px; border-radius: 4px; border-top-left-radius: 1px; animation: fadeSlideIn 0.3s ease forwards; }
        .choice-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
        .choice-btn { font-size: 11px; color: var(--text-dim); border: 1px solid var(--border-soft); background: var(--surface); padding: 8px 12px; border-radius: 3px; cursor: pointer; font-family: 'DM Mono', monospace; transition: border-color 0.2s, color 0.2s, background 0.2s; }
        .choice-btn:disabled { opacity: 0.45; cursor: not-allowed; pointer-events: none; }
        .choice-btn:hover { border-color: #c8a96e40; color: #c8a96e; background: #c8a96e08; }
        .choice-btn.selected { border-color: #c8a96e; color: #c8a96e; background: #c8a96e15; }
        .refine-input-bar {
          padding: 14px 28px 20px;
          flex-shrink: 0;
          border-top: 1px solid var(--border-top);
          background: var(--app-bg);
        }
        .refine-message-row { align-items: flex-start; width: 100%; }
        .refine-input-bar.refine-input-bar--inline {
          padding: 4px 0 8px;
          border-top: none;
          background: transparent;
          width: var(--assistant-content-max-width, 68%);
          max-width: var(--assistant-content-max-width, 68%);
          align-self: flex-start;
          box-sizing: border-box;
        }
        .refine-input-bar.refine-input-bar--inline .refine-input-inner {
          width: 100%;
          box-sizing: border-box;
        }
        .refine-input-inner {
          display: flex;
          align-items: flex-end;
          gap: 10px;
          background: var(--surface-input);
          border: 1px solid var(--border);
          border-radius: 5px;
          padding: 12px 14px;
          transition: border-color 0.2s;
        }
        .refine-input-inner:focus-within { border-color: #c8a96e40; }
        .refine-chat-textarea {
          flex: 1;
          background: none;
          border: none;
          outline: none;
          font-family: 'DM Mono', monospace;
          font-size: 13px;
          color: var(--text);
          resize: none;
          line-height: 1.6;
          max-height: 120px;
          overflow-y: auto;
        }
        .refine-chat-textarea::placeholder { color: var(--placeholder); }
        .refine-send-btn {
          background: #c8a96e;
          border: none;
          border-radius: 3px;
          width: 34px;
          height: 34px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          flex-shrink: 0;
          transition: all 0.2s;
          color: var(--on-accent);
        }
        .refine-send-btn:hover:not(:disabled) { background: #d4b87e; transform: scale(1.03); }
        .refine-send-btn:disabled { opacity: 0.35; cursor: default; transform: none; }
        .refine-input-hint {
          font-size: 9px;
          color: var(--msg-time);
          text-align: right;
          margin-top: 7px;
          letter-spacing: 0.05em;
        }
        .retirement-status-row {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          width: 100%;
          max-width: 100%;
          box-sizing: border-box;
        }
        .retirement-status-btn {
          flex: 1 1 auto;
          min-width: 0;
          max-width: 100%;
          box-sizing: border-box;
          font-size: 10px;
          padding: 5px 8px;
          line-height: 1.25;
          text-align: center;
          white-space: normal;
          overflow-wrap: break-word;
          word-break: break-word;
        }
        .form-panel {
          padding: 20px 28px;
          max-width: 520px;
          width: 100%;
          box-sizing: border-box;
          overflow-x: clip;
        }
        .sidebar-life-planner-dials {
          margin: 4px 0 0;
          padding: 0;
          box-sizing: border-box;
          max-width: 100%;
          width: 100%;
        }
        .sidebar-life-planner-dials .life-planner-dials--sidebar {
          width: 100%;
        }
        .form-panel.form-panel--analyze-full { max-width: none; width: 100%; align-self: stretch; box-sizing: border-box; }
        .form-panel.form-panel--analyze-full .form-panel { max-width: none; }
        .form-panel.below-messages { padding-top: 8px; }
        .form-panel label { display: block; margin-bottom: 14px; font-size: 13px; color: var(--text); }
        .form-panel input, .form-panel select, .form-panel textarea { width: 100%; padding: 10px 12px; background: var(--surface-input); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-family: 'DM Mono', monospace; font-size: 13px; margin-top: 4px; }
        .form-panel input:read-only { opacity: 0.55; cursor: default; }
        .form-panel input::placeholder, .form-panel textarea::placeholder { color: var(--placeholder); }
        .form-panel .save-portfolio-actions {
          display: flex;
          flex-direction: row;
          flex-wrap: nowrap;
          gap: 8px;
          margin-top: 12px;
          align-items: center;
        }
        .form-panel .save-portfolio-actions .form-primary-btn,
        .form-panel .save-portfolio-actions button[type="submit"] {
          margin-right: 0;
          flex: 0 1 auto;
          white-space: nowrap;
        }
        .form-panel button[type="submit"]:not(.secondary),
        .form-panel button.form-primary-btn,
        button.form-primary-btn {
          background: #c8a96e;
          color: var(--on-accent);
          border: none;
          padding: 10px 18px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 12px;
          font-family: 'DM Mono', monospace;
          transition: background 0.2s, opacity 0.2s;
        }
        .form-panel button[type="submit"]:not(.secondary) {
          margin-right: 8px;
        }
        .form-panel button.form-primary-btn:hover:not(:disabled),
        .form-panel button[type="submit"]:not(.secondary):not(:disabled):hover,
        button.form-primary-btn:hover:not(:disabled) {
          background: #d4b87e;
        }
        .form-panel button.form-primary-btn:disabled,
        button.form-primary-btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }
        .form-panel label.analyze-file-upload-btn {
          display: inline-block;
          width: auto;
          margin-bottom: 12px;
          margin-right: 8px;
          background: #c8a96e;
          color: var(--on-accent);
          border: none;
          padding: 10px 18px;
          border-radius: 4px;
          cursor: pointer;
          font-size: 12px;
          font-family: 'DM Mono', monospace;
        }
        .form-panel label.analyze-file-upload-btn input[type="file"] {
          display: none;
        }
        .form-panel label.analyze-file-upload-btn.is-disabled {
          opacity: 0.45;
          cursor: not-allowed;
          pointer-events: none;
        }
        .form-panel button.form-primary-btn.analyze-table-add-btn {
          margin-right: 0;
          padding: 6px 14px;
          font-size: 18px;
          line-height: 1;
          font-weight: 400;
        }
        .form-panel button[type="button"].secondary,
        .form-panel button[type="submit"].secondary {
          background: transparent;
          color: var(--text-dim);
          border: 1px solid var(--btn-secondary-border);
        }
        .form-panel .birth-dates-row-label { display: block; margin-bottom: 8px; }
        .form-panel .birth-dates-row { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 14px; }
        .form-panel .birth-date-group { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 140px; }
        .form-panel .birth-date-label { font-size: 10px; color: var(--text-label); text-transform: uppercase; letter-spacing: 0.08em; }
        .form-panel .birth-date-row { display: flex; gap: 8px; }
        .form-panel .birth-date-row input { flex: 1; }
        .portfolio-update-composition-panel {
          margin-bottom: 20px;
          padding: 16px;
          border: 1px solid var(--border-soft);
          border-radius: 8px;
          background: var(--surface-elevated);
          box-sizing: border-box;
        }
        .portfolio-update-composition-hint {
          font-size: 12px;
          color: var(--text-muted);
          margin: 0 0 12px;
          line-height: 1.5;
        }
        .portfolio-update-composition-error {
          color: #dc2626;
          margin-bottom: 10px;
          font-size: 13px;
        }
        [data-theme="light"] .portfolio-update-composition-error {
          color: #b91c1c;
        }
        .portfolio-update-composition-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }
        .portfolio-update-composition-table thead tr {
          border-bottom: 1px solid var(--border-soft);
        }
        .portfolio-update-composition-table th {
          padding: 8px;
          text-align: left;
          color: var(--text);
          font-weight: 600;
        }
        .portfolio-update-composition-table th.portfolio-update-composition-th-num {
          text-align: right;
        }
        .portfolio-update-composition-table tbody tr {
          border-bottom: 1px solid var(--border-soft);
        }
        .portfolio-update-composition-table td {
          padding: 8px;
        }
        .portfolio-update-composition-table td.portfolio-update-composition-td-num {
          text-align: right;
        }
        .portfolio-update-composition-input {
          width: 100%;
          min-width: 80px;
          padding: 6px 8px;
          background: var(--surface-input);
          border: 1px solid var(--border);
          border-radius: 4px;
          color: var(--text);
          font-size: 13px;
          font-family: 'DM Mono', monospace;
          box-sizing: border-box;
        }
        .portfolio-update-composition-input::placeholder {
          color: var(--placeholder);
        }
        .portfolio-update-composition-input--weight {
          width: 120px;
          max-width: 100%;
          text-align: right;
        }
        .intake-inline-input {
          font-family: 'DM Mono', monospace;
          background: var(--surface-input);
          border: 1px solid var(--border);
          border-radius: 4px;
          color: var(--text);
          box-sizing: border-box;
        }
        .intake-inline-input::placeholder { color: var(--placeholder); }
        .intake-row-cmd-btn {
          width: 28px;
          height: 28px;
          padding: 0;
          font-size: 18px;
          line-height: 1;
          border-radius: 4px;
          border: 1px solid var(--border);
          background: var(--surface);
          color: var(--text);
          box-sizing: border-box;
        }
        .intake-row-cmd-btn:not(:disabled) { cursor: pointer; }
        .intake-row-cmd-btn:disabled { cursor: default; opacity: 0.55; }
        .intake-row-cmd-btn--add { margin-top: 4px; color: #c8a96e; }
        /* Light mode: white fields, dark text (beats inline #111 on intake controls) */
        [data-theme="light"] .messages-area input[type="text"],
        [data-theme="light"] .messages-area input[type="number"],
        [data-theme="light"] .messages-area input[type="email"],
        [data-theme="light"] .messages-area input[type="password"],
        [data-theme="light"] .messages-area select,
        [data-theme="light"] .messages-area textarea,
        [data-theme="light"] .form-panel input[type="text"],
        [data-theme="light"] .form-panel input[type="number"],
        [data-theme="light"] .form-panel input[type="email"],
        [data-theme="light"] .form-panel input[type="password"],
        [data-theme="light"] .form-panel select,
        [data-theme="light"] .form-panel textarea,
        [data-theme="light"] .main .intake-inline-input,
        [data-theme="light"] .main input.intake-inline-input {
          background: #ffffff !important;
          color: #1c1917 !important;
          border-color: #c4bdb0 !important;
        }
        [data-theme="light"] .messages-area input::placeholder,
        [data-theme="light"] .messages-area textarea::placeholder,
        [data-theme="light"] .form-panel input::placeholder,
        [data-theme="light"] .form-panel textarea::placeholder,
        [data-theme="light"] .intake-inline-input::placeholder {
          color: #78716c !important;
        }
        [data-theme="light"] .refine-input-inner {
          background: #ffffff !important;
          border-color: #c4bdb0 !important;
        }
        [data-theme="light"] .refine-chat-textarea {
          color: #1c1917 !important;
        }
        [data-theme="light"] .refine-chat-textarea::placeholder {
          color: #78716c !important;
        }
        [data-theme="light"] .choice-btn,
        [data-theme="light"] .choice-btn.retirement-status-btn {
          color: #292524 !important;
          border-color: #a8a29e !important;
          background: rgba(255, 255, 255, 0.65) !important;
        }
        [data-theme="light"] .choice-btn:hover {
          color: #1c1917 !important;
          border-color: #8b7349 !important;
          background: rgba(200, 169, 110, 0.14) !important;
        }
        [data-theme="light"] .choice-btn.selected {
          color: #1c1917 !important;
          border-color: #7a6238 !important;
          background: rgba(200, 169, 110, 0.24) !important;
        }
        .portfolio-saved-description-block {
          margin-bottom: 14px;
          padding: 10px 12px;
          border-left: 3px solid #c8a96e;
          background: #f5f2eb;
          font-size: 13px;
          line-height: 1.55;
        }
        .portfolio-saved-description-label {
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: #0a0a0a;
          margin-bottom: 6px;
          font-weight: 700;
        }
        .portfolio-saved-description-body {
          white-space: pre-wrap;
          font-weight: 700;
          color: #0a0a0a;
        }
        .portfolio-view-panel .whatif-row-add-btn {
          padding: 6px 10px;
          background: #f5f2eb;
          border: 1px solid #c4bdb0;
          border-radius: 4px;
          color: #0a0a0a;
          font-size: 16px;
          font-weight: 700;
          cursor: pointer;
          font-family: 'DM Mono', monospace;
        }
        .portfolio-view-panel .whatif-row-add-btn:disabled {
          opacity: 0.55;
          cursor: default;
        }
        .portfolio-view-panel .intake-row-cmd-btn {
          background: #f5f2eb;
          border-color: #c4bdb0;
          color: #0a0a0a;
          font-weight: 700;
        }
        .portfolio-view-panel .intake-row-cmd-btn--add {
          color: #5c4d2c;
        }
        .sidebar-backdrop {
          position: fixed;
          inset: 0;
          z-index: 199;
          margin: 0;
          padding: 0;
          border: none;
          background: rgba(0, 0, 0, 0.52);
          cursor: pointer;
        }
        .topbar-menu-wrap { display: none; position: relative; }
        .topbar-menu-btn {
          background: transparent;
          border: 1px solid var(--toggle-border);
          border-radius: 3px;
          color: var(--text-muted);
          padding: 6px 10px;
          font-size: 11px;
          font-family: 'DM Mono', monospace;
          cursor: pointer;
          letter-spacing: 0.05em;
        }
        .topbar-menu-btn:hover { border-color: #c8a96e60; color: #c8a96e; }
        .topbar-menu-dropdown {
          position: absolute;
          top: calc(100% + 6px);
          right: 0;
          min-width: 160px;
          background: var(--modal-bg);
          border: 1px solid var(--border);
          border-radius: 4px;
          padding: 6px;
          z-index: 120;
          box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
        }
        .topbar-menu-dropdown button {
          display: block;
          width: 100%;
          text-align: left;
          background: none;
          border: none;
          padding: 10px 12px;
          font-family: 'DM Mono', monospace;
          font-size: 11px;
          color: var(--text-soft);
          cursor: pointer;
          border-radius: 3px;
        }
        .topbar-menu-dropdown button:hover {
          background: var(--fn-hover-bg);
          color: #c8a96e;
        }
        @media (max-width: 768px) {
          .app-shell { width: 100%; max-width: 100%; }
          .main { width: 100%; flex: 1; min-width: 0; max-width: 100%; }
          .app-shell.is-mobile .sidebar {
            flex: 0 0 0;
            width: min(280px, 85vw);
          }
          .sidebar {
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            z-index: 200;
            width: min(280px, 85vw);
            min-width: 0;
            padding-top: max(28px, env(safe-area-inset-top));
            padding-bottom: max(28px, env(safe-area-inset-bottom));
            padding-left: max(20px, env(safe-area-inset-left));
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            box-shadow: 4px 0 28px rgba(0, 0, 0, 0.4);
          }
          .sidebar.collapsed {
            width: min(280px, 85vw);
            min-width: 0;
            padding: 28px 20px;
            padding-top: max(28px, env(safe-area-inset-top));
            padding-bottom: max(28px, env(safe-area-inset-bottom));
            padding-left: max(20px, env(safe-area-inset-left));
            overflow-y: auto;
            transform: translateX(-105%);
          }
          .sidebar:not(.collapsed) {
            transform: translateX(0);
          }
          .topbar {
            padding: 12px 14px;
            padding-top: max(12px, env(safe-area-inset-top));
            gap: 8px;
          }
          .topbar-sub { display: none; }
          .topbar-nav--desktop { display: none !important; }
          .topbar-menu-wrap { display: block; }
          .user-email-display { display: none; }
          .user-menu .logout-btn { display: none; }
          .topbar-right > .login-btn { display: none; }
          .topbar-right > .user-menu { display: none; }
          .topbar-left { min-width: 0; flex: 1; }
          .topbar-title { font-size: 16px; }
          .topbar-right { gap: 8px; }
          .messages-area { padding: 16px; }
          :root { --assistant-content-max-width: 92%; }
          .message-bubble { max-width: var(--assistant-content-max-width); }
          .advisor-model-output-disclaimer { max-width: var(--assistant-content-max-width); }
          .choice-buttons {
            flex-direction: column;
            width: var(--assistant-content-max-width);
            max-width: 100%;
          }
          .choice-btn {
            width: 100%;
            min-height: 44px;
            font-size: 12px;
          }
          .refine-input-bar.refine-input-bar--inline {
            width: var(--assistant-content-max-width);
            max-width: var(--assistant-content-max-width);
          }
          .refine-chat-textarea { font-size: 16px; }
          .refine-send-btn { width: 44px; height: 44px; }
          .legal-sticky-footer {
            padding-bottom: max(5px, env(safe-area-inset-bottom));
            padding-left: max(14px, env(safe-area-inset-left));
            padding-right: max(14px, env(safe-area-inset-right));
            font-size: 8px;
          }
          .form-panel,
          .form-panel.below-messages {
            padding: 16px;
            max-width: 100%;
          }
          .form-panel .birth-dates-row {
            flex-direction: column;
            gap: 12px;
          }
          .form-panel .birth-date-group {
            width: 100%;
            min-width: 0;
            flex: 1 1 100%;
          }
          .form-panel label > div[style*="flex"] {
            flex-direction: column !important;
            align-items: stretch !important;
          }
          .form-panel label > div[style*="flex"] > span {
            min-width: 0 !important;
            width: 100% !important;
            max-width: 100% !important;
          }
          .form-panel .save-portfolio-actions {
            flex-wrap: wrap;
          }
          .retirement-status-btn {
            min-height: 44px;
          }
          .modal-overlay {
            padding: 12px;
            align-items: flex-end;
          }
          .login-modal {
            min-width: 0;
            width: 100%;
            max-width: calc(100vw - 24px);
            max-height: 90dvh;
            overflow-y: auto;
            box-sizing: border-box;
          }
          .refine-input-bar.refine-input-bar--inline {
            padding-bottom: max(8px, env(safe-area-inset-bottom));
          }
        }
        @media (max-width: 480px) {
          .login-modal {
            width: calc(100vw - 16px);
            max-width: none;
            padding: 18px 16px;
          }
        }
      `}</style>

      <div className={`app-shell${isMobile ? " is-mobile" : ""}`}>
        {isMobile && sidebarOpen ? (
          <button
            type="button"
            className="sidebar-backdrop"
            aria-label="Close menu"
            onClick={() => setSidebarOpen(false)}
          />
        ) : null}
        <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
          <div className="user-block">
            <div className="user-block__top">
              <div className="user-block__text">
                <span className="user-greeting">{userId ? "Welcome back" : "Welcome"}</span>
                <span className="user-name">{userId && userEmail ? (userEmail.includes("@") ? userEmail.split("@")[0] : userEmail) : "Portfolio Optimizer"}</span>
                <span className="user-badge">Quala AI</span>
              </div>
              {userId ? <SidebarQuickScanDisclosure inline /> : null}
            </div>
            {userId ? (
              <div className="sidebar-life-planner-dials" role="region" aria-label="Life planner snapshot">
                <LifePlannerDials
                  size="sidebar"
                  showTopBorder={false}
                  goalLabel="Funded"
                  retirementLabel="Success"
                  goalFundedPercent={sidebarGoalFundedPercent}
                  retirementSuccessPercent={sidebarRetirementSuccessPercent}
                />
              </div>
            ) : null}
          </div>

          {userId && (
            <div className="sidebar-section">
              <div className="section-label">Life planner</div>
              <div className="portfolio-list">
                <div
                  role="button"
                  tabIndex={0}
                  className={`portfolio-list-item${
                    view === "connectGrowthRetire" && !selectedLifeScenarioId ? " selected" : ""
                  }`}
                  onClick={openConnectGrowthRetireView}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openConnectGrowthRetireView();
                    }
                  }}
                  style={{ cursor: "pointer" }}
                  title="Growth and retirement planning in one workspace"
                >
                  <span className="portfolio-list-item-label">Create plan</span>
                </div>
                {savedLifeScenarios.length === 0 ? (
                  <div className="portfolio-list-empty" style={{ fontSize: 11, color: "#666", fontStyle: "italic", marginTop: 6 }}>
                    No saved life scenarios yet
                  </div>
                ) : (
                  <div style={{ marginLeft: 14, borderLeft: "2px solid #2a2a2a", paddingLeft: 8, marginTop: 6 }}>
                    {savedLifeScenarios.map((ls) => (
                      <div
                        key={ls.life_scenario_id}
                        className={`portfolio-list-item${
                          selectedLifeScenarioId === ls.life_scenario_id && view === "connectGrowthRetire" ? " selected" : ""
                        }`}
                        role="button"
                        tabIndex={0}
                        onClick={() => openLifeScenarioFromSidebar(ls.life_scenario_id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            openLifeScenarioFromSidebar(ls.life_scenario_id);
                          }
                        }}
                        style={{ cursor: "pointer", fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}
                        title="Open this life scenario"
                      >
                        <span className="portfolio-list-item-label">{ls.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="section-label" style={{ marginTop: 20 }}>Net worth</div>
              <div className="portfolio-list">
                <div
                  role="button"
                  tabIndex={0}
                  className={`portfolio-list-item${view === "netWorth" ? " selected" : ""}`}
                  onClick={openNetWorthView}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openNetWorthView();
                    }
                  }}
                  style={{ cursor: "pointer" }}
                  title="Investments + assets - debts"
                >
                  <span className="portfolio-list-item-label">Investments + assets - debts</span>
                  {sidebarNetWorthShown != null && Number.isFinite(sidebarNetWorthShown) ? (
                    <span className="portfolio-list-amount">{formatSidebarUsd(sidebarNetWorthShown)}</span>
                  ) : null}
                </div>
              </div>

              <div className="section-label" style={{ marginTop: 20 }}>Portfolio</div>
              {["growth", "retirement"].map((cat) => {
                const items = savedPortfolios.filter((p) => (p.portfolio_category || "growth") === cat);
                const label = cat === "growth" ? "Growth" : "Retirement";
                return (
                  <div key={cat} style={{ marginBottom: 16 }}>
                    <div className="section-label" style={{ marginTop: cat === "growth" ? 0 : 12 }}>
                      {label}
                    </div>
                    <div className="portfolio-list">
                      {items.length === 0 ? (
                        <div className="portfolio-list-empty" style={{ fontSize: 11, color: "#666", fontStyle: "italic" }}>None saved</div>
                      ) : items.map((p) => {
                        const portfolioScenarios = savedScenarios.filter(
                          (s) => s.portfolio_id === p.portfolio_id && !lifePlannerOwnedScenarioIds.has(s.scenario_id),
                        );
                        const portfolioSelected = selectedPortfolioId === p.portfolio_id && view === "portfolio" && !selectedScenarioId;
                        const portfolioUsd = latestPortfolioUsd(p);
                        return (
                          <div key={p.portfolio_id} style={{ marginBottom: 4 }}>
                            <div
                              className={`portfolio-list-item${portfolioSelected ? " selected" : ""}`}
                              role="button"
                              tabIndex={0}
                              draggable
                              onDragStart={(e) => {
                                e.stopPropagation();
                                const kind = p.portfolio_category === "retirement" ? "retirement" : "growth";
                                e.dataTransfer.setData(
                                  "application/json",
                                  JSON.stringify({
                                    kind,
                                    source: "portfolio",
                                    portfolioId: p.portfolio_id,
                                    scenarioId: null,
                                    label: p.portfolio_name || "Portfolio",
                                  }),
                                );
                                e.dataTransfer.effectAllowed = "copy";
                              }}
                              onClick={() => handlePortfolioClick(p.portfolio_id)}
                              onKeyDown={(e) => e.key === "Enter" && handlePortfolioClick(p.portfolio_id)}
                              style={{ cursor: "pointer" }}
                              title="Click to open · Drag or pick in Life planner / Compare"
                            >
                              <span className="portfolio-list-item-label">{p.portfolio_name || "My Portfolio"}</span>
                              {portfolioUsd != null ? (
                                <span className="portfolio-list-amount">
                                  {portfolioUsd >= 1e6
                                    ? `$${(portfolioUsd / 1e6).toFixed(1)}M`
                                    : portfolioUsd >= 1e3
                                      ? `$${(portfolioUsd / 1e3).toFixed(0)}K`
                                      : `$${Math.round(portfolioUsd)}`}
                                </span>
                              ) : null}
                            </div>
                            <div style={{ marginLeft: 14, borderLeft: "2px solid #2a2a2a", paddingLeft: 8 }}>
                              {portfolioScenarios.map((s) => {
                                const scenarioListLabel = sidebarScenarioListLabel(s.scenario_name);
                                return (
                                <div
                                  key={s.scenario_id}
                                  className={`portfolio-list-item${selectedScenarioId === s.scenario_id && view === "portfolio" ? " selected" : ""}`}
                                  role="button"
                                  tabIndex={0}
                                  draggable
                                  onDragStart={(e) => {
                                    e.stopPropagation();
                                    const kind = p.portfolio_category === "retirement" ? "retirement" : "growth";
                                    e.dataTransfer.setData(
                                      "application/json",
                                      JSON.stringify({
                                        kind,
                                        source: "scenario",
                                        portfolioId: p.portfolio_id,
                                        scenarioId: s.scenario_id,
                                        label: `${p.portfolio_name || "Portfolio"} — ${scenarioListLabel}`,
                                      }),
                                    );
                                    e.dataTransfer.effectAllowed = "copy";
                                  }}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleScenarioClick(s.scenario_id, p.portfolio_id);
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                      e.stopPropagation();
                                      handleScenarioClick(s.scenario_id, p.portfolio_id);
                                    }
                                  }}
                                  style={{ cursor: "pointer", fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}
                                  title="Click to open · Drag or pick in Life planner / Compare"
                                >
                                  <span className="portfolio-list-item-label">{scenarioListLabel}</span>
                                </div>
                              );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}

              <div className="section-label" style={{ marginTop: 20 }}>Function</div>
              <div className="sidebar-fn-list">
                <button
                  type="button"
                  className="sidebar-fn-item"
                  onClick={openSameCategoryCompare}
                  title="Compare two growth or two retirement portfolios or scenarios (same type on both sides)"
                >
                  Compare
                </button>
              </div>
            </div>
          )}
        </aside>

        <main className="main">
          <div className="topbar">
            <div className="topbar-left">
              <button
                type="button"
                className="toggle-btn"
                onClick={toggleSidebar}
                aria-label={sidebarOpen ? "Close menu" : "Open menu"}
              >
                {isMobile ? (sidebarOpen ? "✕" : "☰") : sidebarOpen ? "←" : "→"}
              </button>
              <div>
                <div className="topbar-title">Quala AI</div>
                <div className="topbar-sub">Plan your financial future, just a conversation away.</div>
              </div>
            </div>
            <div className="topbar-right">
              <button
                type="button"
                className="theme-toggle-btn"
                onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
                title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              >
                {theme === "dark" ? "Light" : "Dark"}
              </button>
              <nav className="topbar-nav topbar-nav--desktop" aria-label="Site pages">
                <button type="button" className="topbar-nav-link" onClick={() => setInfoModal("about")}>
                  About us
                </button>
                <button type="button" className="topbar-nav-link" onClick={() => setInfoModal("faq")}>
                  FAQ
                </button>
                <button type="button" className="topbar-nav-link" onClick={() => setInfoModal("pricing")}>
                  Pricing
                </button>
              </nav>
              <div className="topbar-menu-wrap">
                <button
                  type="button"
                  className="topbar-menu-btn"
                  aria-expanded={topbarMenuOpen}
                  aria-haspopup="true"
                  onClick={() => setTopbarMenuOpen((open) => !open)}
                >
                  Menu
                </button>
                {topbarMenuOpen ? (
                  <div className="topbar-menu-dropdown" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setInfoModal("about");
                        setTopbarMenuOpen(false);
                      }}
                    >
                      About us
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setInfoModal("faq");
                        setTopbarMenuOpen(false);
                      }}
                    >
                      FAQ
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setInfoModal("pricing");
                        setTopbarMenuOpen(false);
                      }}
                    >
                      Pricing
                    </button>
                    {userId ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          handleLogout();
                          setTopbarMenuOpen(false);
                        }}
                      >
                        Logout
                      </button>
                    ) : (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setAccountModalTab("login");
                          setAccountModalOpen(true);
                          setTopbarMenuOpen(false);
                        }}
                      >
                        Login
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
              {userId ? (
                <div className="user-menu">
                  <span className="user-email-display">{userEmail || "Signed in"}</span>
                  <button type="button" className="logout-btn" onClick={handleLogout}>Logout</button>
                </div>
              ) : (
                <button
                  type="button"
                  className="login-btn"
                  onClick={() => {
                    setAccountModalTab("login");
                    setAccountModalOpen(true);
                  }}
                >
                  Login
                </button>
              )}
            </div>
          </div>

          <div className="main-body">
          {showPortfolioDeleteConfirm && (
            <div className="modal-overlay" onClick={() => setShowPortfolioDeleteConfirm(false)}>
              <div className="login-modal" onClick={(e) => e.stopPropagation()}>
                <h3 className="login-modal-title">Delete portfolio</h3>
                <p style={{ fontSize: 13, color: "#aaa", marginBottom: 20, lineHeight: 1.5 }}>
                  Are you sure you want to delete this portfolio? The intake saved with this portfolio will be removed too. Your login stays active. This cannot be undone.
                </p>
                <div className="login-modal-actions">
                  <button type="button" className="login-submit-btn" onClick={confirmDeleteSavedPortfolio}>
                    Yes
                  </button>
                  <button type="button" className="login-cancel-btn" onClick={() => setShowPortfolioDeleteConfirm(false)}>
                    No
                  </button>
                </div>
              </div>
            </div>
          )}

          {showScenarioDeleteConfirm && (
            <div className="modal-overlay" onClick={() => setShowScenarioDeleteConfirm(false)}>
              <div className="login-modal" onClick={(e) => e.stopPropagation()}>
                <h3 className="login-modal-title">Delete scenario</h3>
                <p style={{ fontSize: 13, color: "#aaa", marginBottom: 20, lineHeight: 1.5 }}>
                  Are you sure you want to delete this scenario? The portfolio and other scenarios will remain. This cannot be undone.
                </p>
                <div className="login-modal-actions">
                  <button type="button" className="login-submit-btn" onClick={confirmDeleteScenario}>
                    Yes
                  </button>
                  <button type="button" className="login-cancel-btn" onClick={() => setShowScenarioDeleteConfirm(false)}>
                    No
                  </button>
                </div>
              </div>
            </div>
          )}

          {showLifeScenarioDeleteConfirm && (
            <div className="modal-overlay" onClick={() => setShowLifeScenarioDeleteConfirm(false)}>
              <div className="login-modal" onClick={(e) => e.stopPropagation()}>
                <h3 className="login-modal-title">Delete life plan</h3>
                <p style={{ fontSize: 13, color: "#aaa", marginBottom: 20, lineHeight: 1.5 }}>
                  Are you sure you want to delete this life plan? It will be removed from Life planner and the linked growth and retirement scenario rows will be deleted. This cannot be undone.
                </p>
                <div className="login-modal-actions">
                  <button type="button" className="login-submit-btn" onClick={confirmDeleteLifeScenario}>
                    Yes
                  </button>
                  <button type="button" className="login-cancel-btn" onClick={() => setShowLifeScenarioDeleteConfirm(false)}>
                    No
                  </button>
                </div>
              </div>
            </div>
          )}

          {infoModal && INFO_PAGES[infoModal] && (
            <div className="modal-overlay" onClick={() => setInfoModal(null)}>
              <div
                className="login-modal"
                style={{
                  maxWidth: infoModal === "pricing" ? 720 : infoModal === "about" ? 680 : 440,
                }}
                onClick={(e) => e.stopPropagation()}
              >
                <h3 className="login-modal-title">{INFO_PAGES[infoModal].title}</h3>
                <div style={{ marginBottom: 8 }}>
                  {infoModal === "pricing" ? (
                    <PricingPlansModalBody />
                  ) : infoModal === "about" ? (
                    <AboutUsModalBody />
                  ) : (
                    INFO_PAGES[infoModal].paragraphs.map((p, i) => (
                      <p key={i} style={{ fontSize: 13, color: "#aaa", marginBottom: 14, lineHeight: 1.65 }}>
                        {p}
                      </p>
                    ))
                  )}
                </div>
                <button type="button" className="login-cancel-btn" onClick={() => setInfoModal(null)}>
                  Close
                </button>
              </div>
            </div>
          )}

          {accountModalOpen && (
            <div
              className="modal-overlay"
              onClick={() => {
                setAccountModalOpen(false);
                setAccountModalTab("login");
              }}
            >
              <div className="login-modal" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
                <AuthForm
                  accountGate
                  defaultAuthTab={accountModalTab}
                  onLoginSuccess={handleAuthLoginSuccess}
                  onCancel={() => {
                    setAccountModalOpen(false);
                    setAccountModalTab("login");
                  }}
                />
              </div>
            </div>
          )}

          {view === "connectGrowthRetire" && compareSessionUserId && (
            <div className="chat-scroll">
              <CompareView
                mrBrownUserId={compareSessionUserId}
                compareNotice={compareNotice}
                compareLeftSel={compareLeftSel}
                compareRightSel={compareRightSel}
                growthForm={compareGrowthForm}
                setGrowthForm={setCompareGrowthForm}
                retireForm={compareRetireForm}
                setRetireForm={setCompareRetireForm}
                compareHydrating={compareHydrating}
                growthArtifacts={compareGrowthArtifacts}
                retireArtifacts={compareRetireArtifacts}
                growthRunLoading={compareGrowthRunLoading}
                retireRunLoading={compareRetireRunLoading}
                retireSyncMessage={compareRetireSyncMessage}
                handleCompareDrop={handleCompareDrop}
                onComparePick={applyCompareSelection}
                savedPortfolios={savedPortfolios}
                savedScenarios={savedScenarios}
                excludeScenarioIds={lifePlannerOwnedScenarioIds}
                onLifePlannerContinue={handleCompareLifePlannerContinue}
                intakeHints={{ monthly: WHATIF_MONTHLY_ROW_FIELDS_HINT, inflow: WHATIF_ONE_TIME_INFLOW_HINT }}
                connectLifeScenarioNameInput={connectLifeScenarioNameInput}
                setConnectLifeScenarioNameInput={setConnectLifeScenarioNameInput}
                onSavePairScenarios={handleConnectSavePairScenarios}
                connectPairScenarioSaving={connectPairScenarioSaving}
                connectPairScenarioError={connectPairScenarioError}
                connectPairScenarioSuccess={connectPairScenarioSuccess}
                intakeFrozen={connectLifePlannerFrozen}
                existingSavedLifePlanCount={savedLifeScenarios.length}
                showLifePlannerSavedBar={!!selectedLifeScenarioId && connectLifePlannerFrozen}
                onLifePlannerDelete={() => setShowLifeScenarioDeleteConfirm(true)}
                frozenGrowthMedianAtRetirementUsd={connectFrozenGrowthMedianUsd}
                currentGrowthPortfolioValueUsd={lifePlannerGrowthPortfolioValueUsd}
                theme={theme}
                onBack={() => {
                  setView("loggedInOptions");
                }}
                onClearSide={(side) => {
                  compareConnectBacktestTokenRef.current += 1;
                  lastConnectHydrateKeyRef.current = "";
                  connectChartsHydrateInFlightRef.current = false;
                  setCompareNotice(null);
                  setCompareRetireSyncMessage(null);
                  setConnectPairScenarioError(null);
                  setConnectPairScenarioSuccess(null);
                  setSelectedLifeScenarioId(null);
                  setConnectLinkedGrowthScenarioId(null);
                  setConnectLinkedRetirementScenarioId(null);
                  setConnectFrozenGrowthMedianUsd(null);
                  setConnectLifePlannerFrozen(false);
                  if (side === "left") {
                    setCompareLeftSel(null);
                    setCompareGrowthForm(null);
                    setCompareGrowthArtifacts(null);
                    setCompareGrowthRunLoading(false);
                  } else {
                    setCompareRightSel(null);
                    setCompareRetireForm(null);
                    setCompareRetireArtifacts(null);
                    setCompareRetireRunLoading(false);
                  }
                }}
              />
            </div>
          )}
          {view === "compare" && compareSessionUserId && (
            <div className="chat-scroll">
              <SameCategoryComparePanel
                notice={sbsNotice}
                leftSel={sbsLeftSel}
                rightSel={sbsRightSel}
                intakeLeft={sbsIntakeLeft}
                intakeRight={sbsIntakeRight}
                hydrating={sbsHydrating}
                leftArtifacts={sbsArtLeft}
                rightArtifacts={sbsArtRight}
                continueLoading={sbsLeftLoading || sbsRightLoading}
                onDrop={handleSbsDrop}
                onPick={applySbsSelection}
                savedPortfolios={savedPortfolios}
                savedScenarios={savedScenarios}
                excludeScenarioIds={lifePlannerOwnedScenarioIds}
                onContinue={handleSbsContinue}
                theme={theme}
                onBack={() => {
                  setView("loggedInOptions");
                }}
                onClearSide={(side) => {
                  sbsBacktestTokenRef.current += 1;
                  setSbsNotice(null);
                  if (side === "left") {
                    setSbsLeftSel(null);
                    setSbsIntakeLeft(null);
                    setSbsArtLeft(null);
                    setSbsLeftLoading(false);
                  } else {
                    setSbsRightSel(null);
                    setSbsIntakeRight(null);
                    setSbsArtRight(null);
                    setSbsRightLoading(false);
                  }
                }}
              />
            </div>
          )}
          {view === "netWorth" && (userId ?? localStorage.getItem(USER_ID_KEY)) && (
            <div className="chat-scroll">
              <NetWorthPanel
                userId={userId ?? localStorage.getItem(USER_ID_KEY)}
                savedPortfolios={savedPortfolios}
                onNetWorthSaved={applyNetWorthSidebarPayload}
                onLiveNetWorthChange={handleNetWorthLiveSidebarChange}
                onBack={() => {
                  portfolioViewLoadTokenRef.current += 1;
                  setView("loggedInOptions");
                  const uid = userId ?? localStorage.getItem(USER_ID_KEY);
                  if (uid) fetchNetWorthSidebar(uid);
                }}
              />
            </div>
          )}
          {view !== "compare" && view !== "connectGrowthRetire" && view !== "netWorth" && (
          <div className="chat-scroll">
          {view === "portfolio" && (
            <div className="messages-area" style={{ padding: "20px 28px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
                <button
                  type="button"
                  className="toggle-btn"
                  onClick={() => {
                    portfolioViewLoadTokenRef.current += 1;
                    setSelectedPortfolioId(null);
                    setSelectedScenarioId(null);
                    setSelectedScenarioRow(null);
                    setSelectedPortfolioRow(null);
                    setPortfolioViewData(null);
                    setShowPortfolioDeleteConfirm(false);
                    setShowScenarioDeleteConfirm(false);
                    setView("loggedInOptions");
                  }}
                  title="Back to home"
                >
                  ←
                </button>
                <span className="topbar-title">
                  {selectedScenarioId
                    ? (selectedScenarioRow?.scenario_name || selectedScenarioId)
                    : (selectedPortfolioRow?.portfolio_name || portfolioViewData?.portfolio?.portfolio_name || "Portfolio")}
                  {portfolioViewData?.agent && (
                    <span style={{ fontSize: 12, color: "#888", marginLeft: 8, fontWeight: 400 }}>
                      ({portfolioViewData.agent} backtest)
                    </span>
                  )}
                </span>
              </div>
              {!selectedScenarioId ? (
                <PortfolioValueHistoryChart
                  series={portfolioViewData?.portfolio?.valuation_history ?? selectedPortfolioRow?.valuation_history ?? []}
                  asOf={portfolioViewData?.portfolio?.valuation_as_of ?? selectedPortfolioRow?.valuation_as_of ?? null}
                />
              ) : null}
              <div className="form-panel below-messages portfolio-view-panel" style={{ marginBottom: 24 }}>
                <div className="choice-buttons" style={{ marginBottom: 20, flexWrap: "wrap" }}>
                  {selectedScenarioId ? (
                    <>
                      <button
                        type="button"
                        className="choice-btn"
                        onClick={openWhatIfFromPortfolio}
                        disabled={!selectedPortfolioRow?.portfolio_ticker_weights || portfolioWhatIfMode}
                      >
                        Edit what-if
                      </button>
                      <button type="button" className="choice-btn" onClick={() => setShowScenarioDeleteConfirm(true)}>
                        Delete what-if
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" className="choice-btn" onClick={() => setShowPortfolioDeleteConfirm(true)}>
                        Delete portfolio
                      </button>
                      <button
                        type="button"
                        className="choice-btn"
                        onClick={() => {
                          setShowPortfolioUpdateComposition((v) => !v);
                          setPortfolioUpdateError("");
                        }}
                        disabled={!selectedPortfolioRow?.portfolio_ticker_weights || portfolioWhatIfMode}
                      >
                        {showPortfolioUpdateComposition ? "Close update" : "Update portfolio"}
                      </button>
                      <button
                        type="button"
                        className="choice-btn"
                        onClick={openWhatIfFromPortfolio}
                        disabled={!selectedPortfolioRow?.portfolio_ticker_weights || portfolioWhatIfMode}
                      >
                        What-if analysis
                      </button>
                    </>
                  )}
                </div>
                {showPortfolioUpdateComposition && !selectedScenarioId && selectedPortfolioRow ? (
                  <div className="portfolio-update-composition-panel">
                    <p className="portfolio-update-composition-hint">
                      Add or edit tickers and <strong style={{ color: "#c8a96e" }}>relative weights</strong> (any positive
                      numbers; they are normalized when you save). Saving updates this portfolio in the database{" "}
                      <strong style={{ color: "#c8a96e" }}>(user, portfolio id, timestamp)</strong> and reruns{" "}
                      {selectedPortfolioRow.portfolio_category === "retirement" ? "retirement" : "growth"} backtesting to
                      refresh charts.
                    </p>
                    {portfolioUpdateError ? (
                      <div className="portfolio-update-composition-error">{portfolioUpdateError}</div>
                    ) : null}
                    <div className="portfolio-table-scroll">
                      <table className="portfolio-update-composition-table">
                        <thead>
                          <tr>
                            <th>Ticker</th>
                            <th className="portfolio-update-composition-th-num">Relative weight</th>
                            <th className="portfolio-update-composition-th-num" style={{ width: 44 }} aria-label="Add row" />
                          </tr>
                        </thead>
                        <tbody>
                          {portfolioUpdateRows.map((r, i) => {
                            const isLast = i === portfolioUpdateRows.length - 1;
                            return (
                              <tr key={i}>
                                <td>
                                  <input
                                    value={r.ticker}
                                    onChange={(e) => {
                                      const v = e.target.value.toUpperCase();
                                      setPortfolioUpdateRows((prev) => {
                                        const next = [...prev];
                                        next[i] = { ...next[i], ticker: v };
                                        return next;
                                      });
                                    }}
                                    placeholder="e.g. VTI"
                                    disabled={portfolioUpdateSaving}
                                    autoComplete="off"
                                    className="portfolio-update-composition-input"
                                  />
                                </td>
                                <td className="portfolio-update-composition-td-num">
                                  <input
                                    value={r.weight}
                                    onChange={(e) => {
                                      setPortfolioUpdateRows((prev) => {
                                        const next = [...prev];
                                        next[i] = { ...next[i], weight: e.target.value };
                                        return next;
                                      });
                                    }}
                                    placeholder="e.g. 40"
                                    disabled={portfolioUpdateSaving}
                                    autoComplete="off"
                                    className="portfolio-update-composition-input portfolio-update-composition-input--weight"
                                  />
                                </td>
                                <td className="portfolio-update-composition-td-num" style={{ verticalAlign: "middle" }}>
                                  {isLast ? (
                                    <button
                                      type="button"
                                      className="form-primary-btn analyze-table-add-btn"
                                      onClick={() =>
                                        setPortfolioUpdateRows((prev) => [...prev, { ticker: "", weight: "" }])
                                      }
                                      disabled={portfolioUpdateSaving}
                                      title="Add row"
                                      aria-label="Add row"
                                    >
                                      +
                                    </button>
                                  ) : null}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
                      <button
                        type="button"
                        className="form-primary-btn"
                        onClick={handleSavePortfolioComposition}
                        disabled={portfolioUpdateSaving}
                      >
                        {portfolioUpdateSaving ? "Saving & backtesting…" : "Save"}
                      </button>
                      <button
                        type="button"
                        className="choice-btn"
                        onClick={() => {
                          setShowPortfolioUpdateComposition(false);
                          setPortfolioUpdateError("");
                        }}
                        disabled={portfolioUpdateSaving}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
                <div style={{ fontSize: 13, color: "#888", marginBottom: 12 }}>
                  <strong style={{ color: "#c8a96e" }}>
                    {selectedScenarioId ? (selectedScenarioRow?.scenario_name || selectedScenarioId) : (selectedPortfolioRow?.portfolio_name || "…")}
                  </strong>
                  {" — "}intake for this {selectedScenarioId ? "scenario" : "portfolio"}.{" "}
                  {!portfolioWhatIfMode ? (
                    <span style={{ color: "#94a3b8" }}>
                      {selectedScenarioId ? (
                        <>The intake form stays read-only until you choose <strong style={{ color: "#c8a96e" }}>Edit what-if</strong> — then you can adjust and use <strong style={{ color: "#c8a96e" }}>Continue</strong> to update the charts. Use <strong style={{ color: "#c8a96e" }}>Delete what-if</strong> to remove this scenario.</>
                      ) : (
                        <>The intake form stays read-only until you choose <strong style={{ color: "#c8a96e" }}>What-if analysis</strong> — then you can adjust monthly expense, inflation, big spending, and portfolio value. Use <strong style={{ color: "#c8a96e" }}>Continue</strong> at the bottom to run backtesting with your changes and update the charts.</>
                      )}
                    </span>
                  ) : (
                    <span>
                      {selectedPortfolioRow?.portfolio_category === "retirement" ? (
                        <>
                          Edit monthly expense, inflation, big spending, initial investment amount, and the retirement what-if fields below (monthly income, misc spending, one-time windfall). Other fields stay fixed.{" "}
                        </>
                      ) : selectedPortfolioRow?.portfolio_category === "growth" ? (
                        <>
                          Edit monthly expense, inflation, big spending, initial investment amount, and the growth what-if fields below (extra monthly income to invest, misc monthly spending, one-time inflow). Other fields stay fixed.{" "}
                        </>
                      ) : (
                        <>Edit monthly expense, inflation, big spending, and initial investment amount; other fields stay fixed. </>
                      )}
                      <strong style={{ color: "#c8a96e" }}>Continue</strong> saves your scenario, runs the backtesting service with the new parameters, and updates results below.
                    </span>
                  )}
                  {!selectedPortfolioRow?.intake && selectedPortfolioRow && (
                    <span style={{ display: "block", marginTop: 8, color: "#b45309" }}>
                      No intake snapshot stored for this portfolio yet — open What-if analysis, fill the editable fields, then Continue.
                    </span>
                  )}
                </div>
                {(() => {
                  const scenarioDesc = selectedScenarioId ? String(selectedScenarioRow?.description || "").trim() : "";
                  const portfolioDesc = !selectedScenarioId ? portfolioSavedDescriptionText(selectedPortfolioRow?.intake) : "";
                  const text = scenarioDesc || portfolioDesc;
                  if (!text) return null;
                  return (
                    <div className="portfolio-saved-description-block">
                      <div className="portfolio-saved-description-label">Description</div>
                      <div className="portfolio-saved-description-body">{text}</div>
                    </div>
                  );
                })()}
                <form onSubmit={handlePortfolioWhatIfContinue} style={{ marginBottom: 0, opacity: !portfolioWhatIfMode ? 0.92 : 1 }}>
                  <label style={{ cursor: portfolioIntakeFieldLocked("planningFor") ? "default" : "pointer" }}>Who are you planning for?
                    <div className="retirement-status-row">
                      {[
                        { value: "self", label: "Just me" },
                        { value: "couple", label: "The two of us" },
                      ].map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          disabled={portfolioIntakeFieldLocked("planningFor")}
                          className={`choice-btn retirement-status-btn ${formState.planningFor === opt.value ? "selected" : ""}`}
                          onClick={() => updateFormState((s) => ({ ...s, planningFor: opt.value }))}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </label>
                  <label className="birth-dates-row-label">Birth dates (year and month) *</label>
                  <div className="birth-dates-row">
                    <div className="birth-date-group">
                      <span className="birth-date-label">Your</span>
                      <div className="birth-date-row">
                        <input readOnly={portfolioIntakeFieldLocked("birthYear1")} type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear1} onChange={(e) => updateFormState((s) => ({ ...s, birthYear1: e.target.value }))} />
                        <input readOnly={portfolioIntakeFieldLocked("birthMonth1")} type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth1} onChange={(e) => updateFormState((s) => ({ ...s, birthMonth1: e.target.value }))} />
                      </div>
                    </div>
                    {formState.planningFor === "couple" && (
                      <div className="birth-date-group">
                        <span className="birth-date-label">Partner</span>
                        <div className="birth-date-row">
                          <input readOnly={portfolioIntakeFieldLocked("birthYear2")} type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear2} onChange={(e) => updateFormState((s) => ({ ...s, birthYear2: e.target.value }))} />
                          <input readOnly={portfolioIntakeFieldLocked("birthMonth2")} type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth2} onChange={(e) => updateFormState((s) => ({ ...s, birthMonth2: e.target.value }))} />
                        </div>
                      </div>
                    )}
                  </div>
                  <label>Retirement status
                    <div className="retirement-status-row">
                      {formState.planningFor === "self" ? (
                        [
                          { value: "self_retired", label: "I am retired" },
                          { value: "both_working", label: "I am working" },
                        ].map((opt) => (
                          <button key={opt.value} type="button" disabled={portfolioIntakeFieldLocked("retirementStatus")} className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => updateFormState((s) => ({ ...s, retirementStatus: opt.value }))}>{opt.label}</button>
                        ))
                      ) : (
                        [
                          { value: "self_retired", label: "I am retired" },
                          { value: "partner_retired", label: "Partner retired" },
                          { value: "both_retired", label: "Both retired" },
                          { value: "both_working", label: "Both working" },
                        ].map((opt) => (
                          <button key={opt.value} type="button" disabled={portfolioIntakeFieldLocked("retirementStatus")} className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => updateFormState((s) => ({ ...s, retirementStatus: opt.value }))}>{opt.label}</button>
                        ))
                      )}
                    </div>
                  </label>
                  {((formState.planningFor === "self" && formState.retirementStatus === "both_working") || (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus)) || (formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus))) && (
                    <label>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                        {((formState.planningFor === "self" && formState.retirementStatus === "both_working") || (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus))) && (
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                            I plan to retire in *
                            <input readOnly={portfolioIntakeFieldLocked("retirementTimelineSelf")} type="text" placeholder="e.g. 10 years" value={formState.retirementTimelineSelf} onChange={(e) => updateFormState((s) => ({ ...s, retirementTimelineSelf: e.target.value }))} />
                          </span>
                        )}
                        {formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus) && (
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                            Partner plans to retire in *
                            <input readOnly={portfolioIntakeFieldLocked("retirementTimelinePartner")} type="text" placeholder="e.g. 8 years" value={formState.retirementTimelinePartner} onChange={(e) => updateFormState((s) => ({ ...s, retirementTimelinePartner: e.target.value }))} />
                          </span>
                        )}
                      </div>
                    </label>
                  )}
                  <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-end" }}>
                    <label>
                      Country & state *
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ color: "#888", fontSize: 12 }}>USA</span>
                        <input readOnly={portfolioIntakeFieldLocked("state")} type="text" placeholder="State" value={formState.state} onChange={(e) => updateFormState((s) => ({ ...s, state: e.target.value }))} style={{ width: 80, maxWidth: 80 }} />
                      </div>
                    </label>
                    <label>
                      Inflation rate (%)
                      <input readOnly={portfolioIntakeFieldLocked("inflationAssumption")} type="text" placeholder="3" value={formState.inflationAssumption} onChange={(e) => updateFormState((s) => ({ ...s, inflationAssumption: e.target.value }))} style={{ width: 60 }} />
                    </label>
                  </div>
                  <label>Risk appetite <input readOnly={portfolioIntakeFieldLocked("risk")} type="text" placeholder="e.g. medium risk" value={formState.risk} onChange={(e) => updateFormState((s) => ({ ...s, risk: e.target.value }))} /></label>
                  <BigSpendingUpcomingEditor
                    rows={formState.bigSpendingRows}
                    disabled={portfolioIntakeFieldLocked("bigSpendingRows")}
                    onRowsChange={(next) => updateFormState((s) => ({ ...s, bigSpendingRows: next, spending: "" }))}
                  />
                  <label>
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <span style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
                        Initial investment amount *
                        <span style={{ display: "flex", alignItems: "center", width: "100%" }}>
                          <span style={{ color: "#888", marginRight: 4 }}>$</span>
                          <input readOnly={portfolioIntakeFieldLocked("investmentValue")} type="text" placeholder="e.g. 1M or 1,000,000" value={formState.investmentValue} onChange={(e) => updateFormState((s) => ({ ...s, investmentValue: e.target.value }))} style={{ flex: 1, width: "100%" }} />
                        </span>
                      </span>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                        <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                          Monthly savings *
                          <span style={{ display: "flex", alignItems: "center" }}>
                            <span style={{ color: "#888", marginRight: 4 }}>$</span>
                            <input readOnly={portfolioIntakeFieldLocked("monthlyContribution")} type="text" placeholder="e.g. 500" value={formState.monthlyContribution} onChange={(e) => updateFormState((s) => ({ ...s, monthlyContribution: e.target.value }))} style={{ flex: 1 }} />
                          </span>
                        </span>
                        <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                          Monthly expense *
                          <span style={{ display: "flex", alignItems: "center" }}>
                            <span style={{ color: "#888", marginRight: 4 }}>$</span>
                            <input readOnly={portfolioIntakeFieldLocked("monthlyExpense")} type="text" placeholder="e.g. 3000" value={formState.monthlyExpense} onChange={(e) => updateFormState((s) => ({ ...s, monthlyExpense: e.target.value }))} style={{ flex: 1 }} />
                          </span>
                        </span>
                      </div>
                    </div>
                  </label>
                  {selectedPortfolioRow?.portfolio_category === "retirement" && (
                    <div
                      style={{
                        marginTop: 16,
                        padding: 16,
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        background: "var(--surface-elevated)",
                      }}
                    >
                      <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                        Retirement what-if
                      </div>
                      <label style={{ display: "block", marginBottom: 14 }}>
                        <span style={{ fontSize: 13, color: "var(--text)" }}>Effective tax rate on withdrawals (%)</span>
                        <input
                          readOnly={portfolioIntakeFieldLocked("retirementEffectiveTaxRate")}
                          type="text"
                          inputMode="decimal"
                          placeholder="0"
                          value={formState.retirementEffectiveTaxRate}
                          onChange={(e) => updateFormState((s) => ({ ...s, retirementEffectiveTaxRate: e.target.value }))}
                          style={{
                            display: "block",
                            marginTop: 6,
                            width: 88,
                            padding: "8px 10px",
                            fontSize: 13,
                            background: "#111",
                            border: "1px solid #2a2a2a",
                            borderRadius: 4,
                            color: "var(--text)",
                          }}
                        />
                        <span style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, display: "block", lineHeight: 1.45 }}>
                          Default 0%. Annual portfolio outflow from living expenses uses inflation-adjusted monthly expense × 12 × (1 + this rate), before misc spending and income offsets.
                        </span>
                      </label>
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 6 }}>Discretionary spending (optional)</div>
                        <span style={{ fontSize: 11, color: "var(--text-muted)", display: "block", lineHeight: 1.45, marginBottom: 8 }}>
                          In each eligible retirement year after the first, add this much extra monthly spending when the portfolio’s prior-year total return (price + yield) meets your hurdle. Optional start/end age limit when the rule applies (calendar age). Leave ages blank to allow any year. The first retirement year has no prior year in the simulation.
                        </span>
                        <div
                          style={{
                            display: "flex",
                            flexWrap: "nowrap",
                            gap: 10,
                            alignItems: "flex-end",
                            overflowX: "auto",
                            paddingBottom: 2,
                          }}
                        >
                          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
                            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Extra $/mo</span>
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ color: "#888", fontSize: 12 }}>$</span>
                              <input
                                readOnly={portfolioIntakeFieldLocked("retirementDiscretionaryMonthly")}
                                type="text"
                                placeholder="e.g. 500"
                                value={formState.retirementDiscretionaryMonthly}
                                onChange={(e) => updateFormState((s) => ({ ...s, retirementDiscretionaryMonthly: e.target.value }))}
                                style={{ width: 88, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                              />
                            </span>
                          </label>
                          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
                            <span style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.25, display: "block" }}>
                              if prior year
                              <br />
                              return ≥ (%)
                            </span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("retirementDiscretionaryMinPriorReturnPct")}
                              type="text"
                              inputMode="decimal"
                              placeholder="e.g. 5"
                              title="Total portfolio return last year (%) must be at or above this; otherwise extra spending is zero for that year"
                              value={formState.retirementDiscretionaryMinPriorReturnPct}
                              onChange={(e) => updateFormState((s) => ({ ...s, retirementDiscretionaryMinPriorReturnPct: e.target.value }))}
                              style={{ width: 72, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                          </label>
                          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
                            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Start age</span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("retirementDiscretionaryStartAge")}
                              type="number"
                              min={0}
                              max={120}
                              placeholder="optional"
                              title="Calendar age; both start and end required to limit the rule to an age window"
                              value={formState.retirementDiscretionaryStartAge}
                              onChange={(e) => updateFormState((s) => ({ ...s, retirementDiscretionaryStartAge: e.target.value }))}
                              style={{ width: 88, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                          </label>
                          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
                            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>End age</span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("retirementDiscretionaryEndAge")}
                              type="number"
                              min={0}
                              max={120}
                              placeholder="optional"
                              title="Calendar age; both start and end required to limit the rule to an age window"
                              value={formState.retirementDiscretionaryEndAge}
                              onChange={(e) => updateFormState((s) => ({ ...s, retirementDiscretionaryEndAge: e.target.value }))}
                              style={{ width: 88, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                          </label>
                        </div>
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>
                          Monthly income (Social Security, pension, rental, annuity, etc.)
                        </div>
                        {(formState.monthlyIncomeRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
                          <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ color: "#888", fontSize: 12 }}>$</span>
                              <input
                                readOnly={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                                type="text"
                                placeholder="Amount"
                                value={row.monthly}
                                onChange={(e) => updateFormState((s) => ({
                                  ...s,
                                  monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, monthly: e.target.value } : r),
                                }))}
                                style={{ width: 80, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                              />
                            </span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                              type="number"
                              placeholder="Start age"
                              min={0}
                              max={100}
                              value={row.startAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, startAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                              type="number"
                              placeholder="End age"
                              min={0}
                              max={100}
                              value={row.endAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, endAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                              type="text"
                              inputMode="decimal"
                              placeholder="YoY %"
                              title="YoY %: real change (inflation rate already included); optional; negative allowed"
                              value={row.yoyPct ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, yoyPct: e.target.value } : r),
                              }))}
                              style={{ width: 64, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                              type="text"
                              placeholder="Label (optional)"
                              value={row.label ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, label: e.target.value } : r),
                              }))}
                              style={{ flex: 1, minWidth: 100, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            {idx === (formState.monthlyIncomeRows || []).length - 1 ? (
                              <button
                                type="button"
                                className="whatif-row-add-btn"
                                onClick={() => updateFormState((s) => ({
                                  ...s,
                                  monthlyIncomeRows: [...(s.monthlyIncomeRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                                }))}
                                disabled={portfolioIntakeFieldLocked("monthlyIncomeRows")}
                              >
                                +
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
                          Reduces how much your portfolio must provide while this income is active. {WHATIF_MONTHLY_ROW_FIELDS_HINT}
                        </div>
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>
                          Misc monthly spending (Medical insurance, club fees, supplemental care, etc.)
                        </div>
                        {(formState.miscMonthlySpendingRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
                          <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ color: "#888", fontSize: 12 }}>$</span>
                              <input
                                readOnly={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                                type="text"
                                placeholder="Amount"
                                value={row.monthly}
                                onChange={(e) => updateFormState((s) => ({
                                  ...s,
                                  miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, monthly: e.target.value } : r),
                                }))}
                                style={{ width: 80, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                              />
                            </span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                              type="number"
                              placeholder="Start age"
                              min={0}
                              max={100}
                              value={row.startAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, startAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                              type="number"
                              placeholder="End age"
                              min={0}
                              max={100}
                              value={row.endAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, endAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                              type="text"
                              inputMode="decimal"
                              placeholder="YoY %"
                              title="YoY %: real change (inflation rate already included); optional; negative allowed"
                              value={row.yoyPct ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, yoyPct: e.target.value } : r),
                              }))}
                              style={{ width: 64, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                              type="text"
                              placeholder="Label (optional)"
                              value={row.label ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, label: e.target.value } : r),
                              }))}
                              style={{ flex: 1, minWidth: 100, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            {idx === (formState.miscMonthlySpendingRows || []).length - 1 ? (
                              <button
                                type="button"
                                className="whatif-row-add-btn"
                                onClick={() => updateFormState((s) => ({
                                  ...s,
                                  miscMonthlySpendingRows: [...(s.miscMonthlySpendingRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                                }))}
                                disabled={portfolioIntakeFieldLocked("miscMonthlySpendingRows")}
                              >
                                +
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
                          Adds to your monthly expense need while each line is active. {WHATIF_MONTHLY_ROW_FIELDS_HINT}
                        </div>
                      </div>
                      <div style={{ marginBottom: 0 }}>
                        <BigSpendingUpcomingEditor
                          title="One-time inflow (windfall) — e.g. sell a home, inheritance"
                          hintText={WHATIF_ONE_TIME_INFLOW_HINT}
                          rows={formState.windfallInflowRows}
                          disabled={portfolioIntakeFieldLocked("windfallInflowRows")}
                          onRowsChange={(next) => updateFormState((s) => ({ ...s, windfallInflowRows: next }))}
                        />
                      </div>
                    </div>
                  )}
                  {selectedPortfolioRow?.portfolio_category === "growth" && (
                    <div
                      style={{
                        marginTop: 16,
                        padding: 16,
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        background: "var(--surface-elevated)",
                      }}
                    >
                      <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                        Growth what-if
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>
                          Extra monthly income to invest (side income, RSU vesting, rental, etc.)
                        </div>
                        {((formState.growthMonthlyIncomeRows || []).length ? formState.growthMonthlyIncomeRows : [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
                          <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ color: "#888", fontSize: 12 }}>$</span>
                              <input
                                readOnly={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                                type="text"
                                placeholder="Amount"
                                value={row.monthly}
                                onChange={(e) => updateFormState((s) => ({
                                  ...s,
                                  growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, monthly: e.target.value } : r),
                                }))}
                                style={{ width: 80, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                              />
                            </span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                              type="number"
                              placeholder="Start age"
                              min={0}
                              max={100}
                              value={row.startAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, startAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                              type="number"
                              placeholder="End age"
                              min={0}
                              max={100}
                              value={row.endAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, endAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                              type="text"
                              inputMode="decimal"
                              placeholder="YoY %"
                              title="YoY %: real change (inflation rate already included); optional; negative allowed"
                              value={row.yoyPct ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, yoyPct: e.target.value } : r),
                              }))}
                              style={{ width: 64, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                              type="text"
                              placeholder="Label (optional)"
                              value={row.label ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => i === idx ? { ...r, label: e.target.value } : r),
                              }))}
                              style={{ flex: 1, minWidth: 100, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            {idx === ((formState.growthMonthlyIncomeRows || []).length || 1) - 1 ? (
                              <button
                                type="button"
                                className="whatif-row-add-btn"
                                onClick={() => updateFormState((s) => ({
                                  ...s,
                                  growthMonthlyIncomeRows: [...(s.growthMonthlyIncomeRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                                }))}
                                disabled={portfolioIntakeFieldLocked("growthMonthlyIncomeRows")}
                              >
                                +
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
                          Adds to your monthly savings in the Monte Carlo while each row is active. {WHATIF_MONTHLY_ROW_FIELDS_HINT}
                        </div>
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>
                          Misc monthly spending (club fees, extra insurance, tuition installment, etc.)
                        </div>
                        {(formState.growthMiscMonthlySpendingRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
                          <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ color: "#888", fontSize: 12 }}>$</span>
                              <input
                                readOnly={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                                type="text"
                                placeholder="Amount"
                                value={row.monthly}
                                onChange={(e) => updateFormState((s) => ({
                                  ...s,
                                  growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, monthly: e.target.value } : r),
                                }))}
                                style={{ width: 80, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                              />
                            </span>
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                              type="number"
                              placeholder="Start age"
                              min={0}
                              max={100}
                              value={row.startAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, startAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                              type="number"
                              placeholder="End age"
                              min={0}
                              max={100}
                              value={row.endAge}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, endAge: e.target.value } : r),
                              }))}
                              style={{ width: 75, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                              type="text"
                              inputMode="decimal"
                              placeholder="YoY %"
                              title="YoY %: real change (inflation rate already included); optional; negative allowed"
                              value={row.yoyPct ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, yoyPct: e.target.value } : r),
                              }))}
                              style={{ width: 64, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            <input
                              readOnly={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                              type="text"
                              placeholder="Label (optional)"
                              value={row.label ?? ""}
                              onChange={(e) => updateFormState((s) => ({
                                ...s,
                                growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => i === idx ? { ...r, label: e.target.value } : r),
                              }))}
                              style={{ flex: 1, minWidth: 100, padding: 6, fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
                            />
                            {idx === (formState.growthMiscMonthlySpendingRows || []).length - 1 ? (
                              <button
                                type="button"
                                className="whatif-row-add-btn"
                                onClick={() => updateFormState((s) => ({
                                  ...s,
                                  growthMiscMonthlySpendingRows: [...(s.growthMiscMonthlySpendingRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                                }))}
                                disabled={portfolioIntakeFieldLocked("growthMiscMonthlySpendingRows")}
                              >
                                +
                              </button>
                            ) : null}
                          </div>
                        ))}
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
                          Reduces the portfolio in the Monte Carlo each month while each row is active. {WHATIF_MONTHLY_ROW_FIELDS_HINT}
                        </div>
                      </div>
                      <div style={{ marginBottom: 0 }}>
                        <BigSpendingUpcomingEditor
                          title="One-time inflow (inheritance, bonus, sale proceeds)"
                          hintText={WHATIF_ONE_TIME_INFLOW_HINT}
                          rows={formState.growthOneTimeInflowRows}
                          disabled={portfolioIntakeFieldLocked("growthOneTimeInflowRows")}
                          onRowsChange={(next) => updateFormState((s) => ({ ...s, growthOneTimeInflowRows: next }))}
                        />
                      </div>
                    </div>
                  )}
                  {profileSaved && <div style={{ fontSize: 12, color: "#10b981", marginBottom: 8 }}>charts updated</div>}
                  {portfolioWhatIfMode && (
                    <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid #1e1e1e" }}>
                      <button type="submit" className="form-primary-btn" disabled={portfolioViewLoading}>
                        {portfolioViewLoadingPhase === "backtest"
                          ? "Running backtest…"
                          : portfolioViewLoading
                            ? "Loading…"
                            : "Continue"}
                      </button>
                    </div>
                  )}
                </form>
                {(userId ?? localStorage.getItem(USER_ID_KEY)) && selectedPortfolioId && !selectedScenarioId ? (
                  <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid #1e1e1e" }}>
                    <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 8, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                      Save as scenario
                    </div>
                    <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 10px", lineHeight: 1.45 }}>
                      Enter a name and save the current intake (including what-if fields) as a new scenario under this portfolio. Run <strong style={{ color: "var(--text)" }}>Continue</strong> after editing what-ifs if you want the charts to match before saving.
                    </p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
                      <label style={{ flex: "1 1 240px", minWidth: 200 }}>
                        <span style={{ fontSize: 12 }}>Scenario name</span>
                        <input
                          type="text"
                          value={portfolioSaveAsName}
                          onChange={(e) => setPortfolioSaveAsName(e.target.value)}
                          placeholder="e.g. Higher inflation"
                          autoComplete="off"
                          style={{
                            marginTop: 6,
                            width: "100%",
                            boxSizing: "border-box",
                            padding: 8,
                            fontSize: 14,
                            background: "var(--surface-elevated)",
                            border: "1px solid var(--border-soft)",
                            borderRadius: 4,
                            color: "var(--text)",
                          }}
                        />
                      </label>
                      <button
                        type="button"
                        className="form-primary-btn"
                        onClick={handlePortfolioSaveAsScenario}
                        disabled={portfolioSaveAsSaving}
                      >
                        {portfolioSaveAsSaving ? "Saving…" : "Save as"}
                      </button>
                    </div>
                  </div>
                ) : null}
                {(userId ?? localStorage.getItem(USER_ID_KEY)) && selectedPortfolioId && selectedScenarioId ? (
                  <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid #1e1e1e" }}>
                    <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 8, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                      Save this scenario
                    </div>
                    <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 10px", lineHeight: 1.45 }}>
                      Open scenario: <strong style={{ color: "var(--text)" }}>{selectedScenarioRow?.scenario_name || "…"}</strong>
                      . <strong style={{ color: "var(--text)" }}>Continue</strong> refreshes charts only; use <strong style={{ color: "var(--text)" }}>Save changes</strong> to persist the current intake and what-if fields to this saved scenario.
                    </p>
                    <button
                      type="button"
                      className="form-primary-btn"
                      onClick={handlePortfolioUpdateScenario}
                      disabled={portfolioUpdateScenarioSaving}
                    >
                      {portfolioUpdateScenarioSaving ? "Saving…" : "Save changes"}
                    </button>
                  </div>
                ) : null}
              </div>
              {portfolioViewLoading && (
                <div style={{ padding: 24, color: "#888", display: "flex", alignItems: "center", gap: 12 }}>
                  <TypingDots />
                  <span>
                    {portfolioViewLoadingPhase === "backtest"
                      ? "Running backtest…"
                      : "Loading saved portfolio…"}
                  </span>
                </div>
              )}
              {!portfolioViewLoading && portfolioViewData?.artifacts && (
                <div style={{ marginTop: 16 }}>
                  <ChartContainer artifacts={portfolioViewData.artifacts} theme={theme} />
                  {artifactsHaveInlineCharts(portfolioViewData.artifacts) ? (
                    <AdvisorModelOutputDisclaimer className="page-output-disclaimer" />
                  ) : null}
                </div>
              )}
              {(userId ?? localStorage.getItem(USER_ID_KEY)) && selectedPortfolioId ? (
                <MrBrownChat
                  userId={userId ?? localStorage.getItem(USER_ID_KEY)}
                  page="portfolio"
                  portfolioId={selectedPortfolioId}
                />
              ) : null}
            </div>
          )}
          {view !== "portfolio" && view !== "compare" && view !== "connectGrowthRetire" && view !== "netWorth" && (
          <div
            className={`messages-area${
              view === "intake" ||
              view === "welcomeOptions" ||
              view === "loggedInOptions" ||
              (view === "auth" && !userId)
                ? " with-form-below"
                : ""
            }`}
          >
            {messages.map((msg) => {
              const chartsFirst = chartsLeadNarrativeForMessage(msg);
              return (
                <div key={msg.id} className={`message-row ${msg.role}`}>
                  <div className="message-meta">
                    <div className="message-avatar">
                      {msg.role === "assistant" ? "∆" : msg.role === "error" ? "!" : "You"}
                    </div>
                    <span className="message-sender">
                      {msg.role === "assistant" ? (msg.agent || "Quala") : msg.role === "error" ? "Error" : "You"}
                    </span>
                    <span className="message-time">{msg.time}</span>
                  </div>
                  {chartsFirst ? (
                    <ChartContainer artifacts={msg.artifacts} chartsBeforeNarrative theme={theme} />
                  ) : null}
                  <div className="message-bubble">{renderText(msg.text)}</div>
                  {msg.artifacts && !chartsFirst ? <ChartContainer artifacts={msg.artifacts} theme={theme} /> : null}
                  {showAdvisorOutputDisclaimer(msg) ? <AdvisorModelOutputDisclaimer /> : null}
                </div>
              );
            })}

            {isTyping && (
              <div className="message-row assistant">
                <div className="message-meta">
                  <div className="message-avatar">∆</div>
                  <span className="message-sender">
                    {waitingForAnalyst
                      ? (messages.filter((m) => m.role === "assistant").pop()?.agent === "Panda" ? "Panda" : "Ana")
                      : (messages.filter((m) => m.role === "assistant").pop()?.agent === "Panda" ? "Panda" : "Quala")}
                  </span>
                </div>
                <div className="typing-bubble"><TypingDots /></div>
              </div>
            )}

            {choiceButtons && (
              <div className="message-row assistant">
                <div className="message-meta">
                  <div className="message-avatar">∆</div>
                  <span className="message-sender">Choose</span>
                </div>
                <div className="choice-buttons">
                  {choiceButtons.choices.map((c) => (
                    <button
                      key={c.value}
                      className="choice-btn"
                      onClick={() => choiceButtons.onPick(c.value)}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {refineChatOpen && !choiceButtons && (
              <div className="message-row assistant refine-message-row">
              <div className="refine-input-bar refine-input-bar--inline" role="region" aria-label="Refine portfolio">
                <div className="refine-input-inner">
                  <textarea
                    ref={refineChatTextareaRef}
                    className="refine-chat-textarea"
                    placeholder={
                      refineChatAdvisor === "Panda"
                        ? "Type refinements (e.g. add JEPI=15%, more bonds)…"
                        : "Type refinements (e.g. add QQQ=30%, add GLD)…"
                    }
                    value={refineChatInput}
                    onChange={(e) => {
                      setRefineChatInput(e.target.value);
                      e.target.style.height = "auto";
                      e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
                    }}
                    onKeyDown={handleRefineChatKey}
                    rows={2}
                    disabled={isTyping}
                  />
                  <button
                    type="button"
                    className="refine-send-btn"
                    onClick={handleRefineChatSend}
                    disabled={!refineChatInput.trim() || isTyping}
                    title="Send (Enter)"
                    aria-label="Send refinement"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <line x1="22" y1="2" x2="11" y2="13" />
                      <polygon points="22 2 15 22 11 13 2 9 22 2" />
                    </svg>
                  </button>
                </div>
                <div className="refine-input-hint">ENTER to send · SHIFT+ENTER for new line</div>
              </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          )}

          {(view === "intake" || view === "welcomeOptions" || view === "loggedInOptions") && !refineChatOpen && (
            <div className="form-panel below-messages">
              {view === "loggedInOptions" && (
                <>
                  <form onSubmit={(e) => e.preventDefault()} style={{ marginBottom: 24 }}>
                    <div style={{ fontSize: 13, color: "#888", marginBottom: 12 }}>Your profile (edit if needed)</div>
                    <label>Who are you planning for?
                      <div className="retirement-status-row">
                        {[
                          { value: "self", label: "Just me" },
                          { value: "couple", label: "The two of us" },
                        ].map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            className={`choice-btn retirement-status-btn ${formState.planningFor === opt.value ? "selected" : ""}`}
                            onClick={() => {
                              updateFormState((s) => {
                                const next = { ...s, planningFor: opt.value };
                                if (opt.value === "self" && ["partner_retired", "both_retired"].includes(s.retirementStatus)) {
                                  next.retirementStatus = "both_working";
                                  next.retirementTimelinePartner = "";
                                }
                                return next;
                              });
                            }}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    </label>
                    <label className="birth-dates-row-label">Birth dates (year and month) *</label>
                    <div className="birth-dates-row">
                      <div className="birth-date-group">
                        <span className="birth-date-label">Your</span>
                        <div className="birth-date-row">
                          <input type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear1} onChange={(e) => updateFormState((s) => ({ ...s, birthYear1: e.target.value }))} />
                          <input type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth1} onChange={(e) => updateFormState((s) => ({ ...s, birthMonth1: e.target.value }))} />
                        </div>
                      </div>
                      {formState.planningFor === "couple" && (
                        <div className="birth-date-group">
                          <span className="birth-date-label">Partner</span>
                          <div className="birth-date-row">
                            <input type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear2} onChange={(e) => updateFormState((s) => ({ ...s, birthYear2: e.target.value }))} />
                            <input type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth2} onChange={(e) => updateFormState((s) => ({ ...s, birthMonth2: e.target.value }))} />
                          </div>
                        </div>
                      )}
                    </div>
                    <label>Retirement status
                      <div className="retirement-status-row">
                        {formState.planningFor === "self" ? (
                          [
                            { value: "self_retired", label: "I am retired" },
                            { value: "both_working", label: "I am working" },
                          ].map((opt) => (
                            <button key={opt.value} type="button" className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => updateFormState((s) => ({ ...s, retirementStatus: opt.value }))}>{opt.label}</button>
                          ))
                        ) : (
                          [
                            { value: "self_retired", label: "I am retired" },
                            { value: "partner_retired", label: "Partner retired" },
                            { value: "both_retired", label: "Both retired" },
                            { value: "both_working", label: "Both working" },
                          ].map((opt) => (
                            <button key={opt.value} type="button" className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => updateFormState((s) => ({ ...s, retirementStatus: opt.value }))}>{opt.label}</button>
                          ))
                        )}
                      </div>
                    </label>
                    {((formState.planningFor === "self" && formState.retirementStatus === "both_working") || (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus)) || (formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus))) && (
                      <label>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                          {((formState.planningFor === "self" && formState.retirementStatus === "both_working") || (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus))) && (
                            <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                              I plan to retire in *
                              <input type="text" placeholder="e.g. 10 years" value={formState.retirementTimelineSelf} onChange={(e) => updateFormState((s) => ({ ...s, retirementTimelineSelf: e.target.value }))} />
                            </span>
                          )}
                          {formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus) && (
                            <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                              Partner plans to retire in *
                              <input type="text" placeholder="e.g. 8 years" value={formState.retirementTimelinePartner} onChange={(e) => updateFormState((s) => ({ ...s, retirementTimelinePartner: e.target.value }))} />
                            </span>
                          )}
                        </div>
                      </label>
                    )}
                    <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-end" }}>
                      <label>
                        Country & state *
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span style={{ color: "#888", fontSize: 12 }}>USA</span>
                          <input type="text" placeholder="State" value={formState.state} onChange={(e) => updateFormState((s) => ({ ...s, state: e.target.value }))} style={{ width: 80, maxWidth: 80 }} />
                        </div>
                      </label>
                      <label>
                        Inflation rate (%)
                        <input type="text" placeholder="3" value={formState.inflationAssumption} onChange={(e) => updateFormState((s) => ({ ...s, inflationAssumption: e.target.value }))} style={{ width: 60 }} />
                      </label>
                    </div>
                    <label>Risk appetite <input type="text" placeholder="e.g. medium risk" value={formState.risk} onChange={(e) => updateFormState((s) => ({ ...s, risk: e.target.value }))} /></label>
                    <BigSpendingUpcomingEditor
                      rows={formState.bigSpendingRows}
                      disabled={false}
                      onRowsChange={(next) => updateFormState((s) => ({ ...s, bigSpendingRows: next, spending: "" }))}
                    />
                    <label>
                      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                        <span style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
                          Initial investment amount to build growth/retirement portfolio *
                          <span style={{ display: "flex", alignItems: "center", width: "100%" }}>
                            <span style={{ color: "#888", marginRight: 4 }}>$</span>
                            <input type="text" placeholder="e.g. 1M or 1,000,000" value={formState.investmentValue} onChange={(e) => updateFormState((s) => ({ ...s, investmentValue: e.target.value }))} style={{ flex: 1, width: "100%" }} />
                          </span>
                        </span>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                            Monthly savings *
                            <span style={{ display: "flex", alignItems: "center" }}>
                              <span style={{ color: "#888", marginRight: 4 }}>$</span>
                              <input type="text" placeholder="e.g. 500" value={formState.monthlyContribution} onChange={(e) => updateFormState((s) => ({ ...s, monthlyContribution: e.target.value }))} style={{ flex: 1 }} />
                            </span>
                          </span>
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                            Monthly expense *
                            <span style={{ display: "flex", alignItems: "center" }}>
                              <span style={{ color: "#888", marginRight: 4 }}>$</span>
                              <input type="text" placeholder="e.g. 3000" value={formState.monthlyExpense} onChange={(e) => updateFormState((s) => ({ ...s, monthlyExpense: e.target.value }))} style={{ flex: 1 }} />
                            </span>
                          </span>
                        </div>
                      </div>
                    </label>
                    {intakeFormError ? (
                      <div
                        role="alert"
                        style={{
                          fontSize: 13,
                          color: "#e07070",
                          marginBottom: 12,
                          padding: "8px 10px",
                          background: "rgba(255,80,80,0.08)",
                          borderRadius: 6,
                          border: "1px solid rgba(255,120,120,0.35)",
                        }}
                      >
                        {intakeFormError}
                      </div>
                    ) : null}
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginTop: 12 }}>
                      <button type="button" className="form-primary-btn" onClick={handleLoggedInProfileContinue}>
                        Continue
                      </button>
                    </div>
                  </form>
                </>
              )}
              {view === "intake" && (
                <form onSubmit={handleIntakeSubmit}>
                  <label>Who are you planning for?
                    <div className="retirement-status-row">
                      {[
                        { value: "self", label: "Just me" },
                        { value: "couple", label: "The two of us" },
                      ].map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          className={`choice-btn retirement-status-btn ${formState.planningFor === opt.value ? "selected" : ""}`}
                          onClick={() => {
                            setFormState((s) => {
                              const next = { ...s, planningFor: opt.value };
                              if (opt.value === "self" && ["partner_retired", "both_retired"].includes(s.retirementStatus)) {
                                next.retirementStatus = "both_working";
                                next.retirementTimelinePartner = "";
                              }
                              return next;
                            });
                          }}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </label>
                  <label className="birth-dates-row-label">Birth dates (year and month) *</label>
                  <div className="birth-dates-row">
                    <div className="birth-date-group">
                      <span className="birth-date-label">Your</span>
                      <div className="birth-date-row">
                        <input type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear1} onChange={(e) => setFormState((s) => ({ ...s, birthYear1: e.target.value }))} />
                        <input type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth1} onChange={(e) => setFormState((s) => ({ ...s, birthMonth1: e.target.value }))} />
                      </div>
                    </div>
                    {formState.planningFor === "couple" && (
                      <div className="birth-date-group">
                        <span className="birth-date-label">Partner</span>
                        <div className="birth-date-row">
                          <input type="number" placeholder="Year" min="1920" max="2010" value={formState.birthYear2} onChange={(e) => setFormState((s) => ({ ...s, birthYear2: e.target.value }))} />
                          <input type="number" placeholder="Month" min="1" max="12" value={formState.birthMonth2} onChange={(e) => setFormState((s) => ({ ...s, birthMonth2: e.target.value }))} />
                        </div>
                      </div>
                    )}
                  </div>
                  <label>Retirement status
                    <div className="retirement-status-row">
                      {formState.planningFor === "self" ? (
                        [
                          { value: "self_retired", label: "I am retired" },
                          { value: "both_working", label: "I am working" },
                        ].map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`}
                            onClick={() => setFormState((s) => ({ ...s, retirementStatus: opt.value }))}
                          >
                            {opt.label}
                          </button>
                        ))
                      ) : (
                        [
                          { value: "self_retired", label: "I am retired" },
                          { value: "partner_retired", label: "Partner retired" },
                          { value: "both_retired", label: "Both retired" },
                          { value: "both_working", label: "Both working" },
                        ].map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            className={`choice-btn retirement-status-btn ${formState.retirementStatus === opt.value ? "selected" : ""}`}
                            onClick={() => setFormState((s) => ({ ...s, retirementStatus: opt.value }))}
                          >
                            {opt.label}
                          </button>
                        ))
                      )}
                    </div>
                  </label>
                  {((formState.planningFor === "self" && formState.retirementStatus === "both_working") ||
                    (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus)) ||
                    (formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus))) && (
                    <label>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                        {((formState.planningFor === "self" && formState.retirementStatus === "both_working") ||
                          (formState.planningFor === "couple" && ["partner_retired", "both_working"].includes(formState.retirementStatus))) && (
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                            I plan to retire in *
                            <input type="text" placeholder="e.g. 10 years" value={formState.retirementTimelineSelf} onChange={(e) => setFormState((s) => ({ ...s, retirementTimelineSelf: e.target.value }))} />
                          </span>
                        )}
                        {formState.planningFor === "couple" && ["self_retired", "both_working"].includes(formState.retirementStatus) && (
                          <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                            Partner plans to retire in *
                            <input type="text" placeholder="e.g. 8 years" value={formState.retirementTimelinePartner} onChange={(e) => setFormState((s) => ({ ...s, retirementTimelinePartner: e.target.value }))} />
                          </span>
                        )}
                      </div>
                    </label>
                  )}
                  <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-end" }}>
                    <label>
                      Country & state *
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ color: "#888", fontSize: 12 }}>USA</span>
                        <input type="text" placeholder="State" value={formState.state} onChange={(e) => setFormState((s) => ({ ...s, state: e.target.value }))} style={{ width: 80, maxWidth: 80 }} />
                      </div>
                    </label>
                    <label>
                      Inflation rate (%)
                      <input type="text" placeholder="3" value={formState.inflationAssumption} onChange={(e) => setFormState((s) => ({ ...s, inflationAssumption: e.target.value }))} style={{ width: 60 }} />
                    </label>
                  </div>
                  <label>Risk appetite <input type="text" placeholder="e.g. medium risk" value={formState.risk} onChange={(e) => setFormState((s) => ({ ...s, risk: e.target.value }))} /></label>
                  <BigSpendingUpcomingEditor
                    rows={formState.bigSpendingRows}
                    disabled={false}
                    onRowsChange={(next) => setFormState((s) => ({ ...s, bigSpendingRows: next, spending: "" }))}
                  />
                  <label>
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <span style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
                        Initial investment amount to build growth/retirement portfolio *
                        <span style={{ display: "flex", alignItems: "center", width: "100%" }}>
                          <span style={{ color: "#888", marginRight: 4 }}>$</span>
                          <input type="text" placeholder="e.g. 1M or 1,000,000" value={formState.investmentValue} onChange={(e) => setFormState((s) => ({ ...s, investmentValue: e.target.value }))} style={{ flex: 1, width: "100%" }} />
                        </span>
                      </span>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
                        <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                          Monthly savings *
                          <span style={{ display: "flex", alignItems: "center" }}>
                            <span style={{ color: "#888", marginRight: 4 }}>$</span>
                            <input type="text" placeholder="e.g. 500" value={formState.monthlyContribution} onChange={(e) => setFormState((s) => ({ ...s, monthlyContribution: e.target.value }))} style={{ flex: 1 }} />
                          </span>
                        </span>
                        <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                          Monthly expense *
                          <span style={{ display: "flex", alignItems: "center" }}>
                            <span style={{ color: "#888", marginRight: 4 }}>$</span>
                            <input type="text" placeholder="e.g. 3000" value={formState.monthlyExpense} onChange={(e) => setFormState((s) => ({ ...s, monthlyExpense: e.target.value }))} style={{ flex: 1 }} />
                          </span>
                        </span>
                      </div>
                    </div>
                  </label>
                  {intakeFormError ? (
                    <div
                      role="alert"
                      style={{
                        fontSize: 13,
                        color: "#e07070",
                        marginBottom: 12,
                        padding: "8px 10px",
                        background: "rgba(255,80,80,0.08)",
                        borderRadius: 6,
                        border: "1px solid rgba(255,120,120,0.35)",
                      }}
                    >
                      {intakeFormError}
                    </div>
                  ) : null}
                  <button type="submit" className="form-primary-btn">Continue</button>
                </form>
              )}
              {view === "welcomeOptions" && (
                <div className="choice-buttons">
                  <button className="choice-btn" onClick={() => handleOptionChoice("growth")}>
                    Work on growth portfolio
                  </button>
                  <button className="choice-btn" onClick={() => handleOptionChoice("retirement")}>
                    Work on retirement portfolio
                  </button>
                  {!hideAnalyzeWelcomeOption ? (
                    <button className="choice-btn" onClick={() => handleOptionChoice("analyze")}>
                      Analyze current portfolio
                    </button>
                  ) : null}
                </div>
              )}
            </div>
          )}

          {view === "auth" && (
            userId ? (
              <SavePortfolioForm
                onSave={async (portfolioName, saveDescription) => {
                  const pv = lastPortfolioValue ?? (formState.investmentValue ? parseAmount(formState.investmentValue) : null);
                  await savePortfolio(userId, portfolioName, pv, saveDescription);
                  setView("chat");
                }}
                onCancel={() => setView("chat")}
              />
            ) : (
              <div className="form-panel below-messages" style={{ paddingTop: 12 }}>
                <AuthForm
                  accountGate={false}
                  defaultAuthTab={authScreenDefaultTab}
                  onLoginSuccess={handleAuthLoginSuccess}
                  onCancel={() => setView(authCancelView === "intake" ? "intake" : "chat")}
                />
              </div>
            )
          )}

          {view === "analyze" && (
            <div className="messages-area" style={{ paddingBottom: 24, width: "100%", maxWidth: "100%", boxSizing: "border-box" }}>
            <AnalyzeForm
              sessionId={sessionId}
              userId={userId ?? localStorage.getItem(USER_ID_KEY)}
              retirementStatus={formState.retirementStatus}
              theme={theme}
              onAnalyzeBacktestComplete={(artifacts, agent) => {
                handleArtifacts({ artifacts });
                setHideAnalyzeWelcomeOption(true);
                addMessage(
                  "assistant",
                  agent === "Emu"
                    ? "Retirement portfolio backtest and Monte Carlo are shown below the table in this panel."
                    : "Growth portfolio backtest and Monte Carlo are shown below the table in this panel.",
                  null,
                  agent,
                );
              }}
              onAnalyzeStartOver={() => setHideAnalyzeWelcomeOption(false)}
              onComputedPortfolioTotal={handleAnalyzeComputedPortfolioTotal}
              onSaveAnalyzePortfolio={async (name, desc) => {
                const uid = userId ?? localStorage.getItem(USER_ID_KEY);
                await savePortfolio(uid, name, undefined, desc, { intakeFromFormState: true });
                setView("welcomeOptions");
              }}
              onAnalyzeSaveCancel={() => setView("welcomeOptions")}
            />
            </div>
          )}
          </div>
          )}
          <LegalStickyFooter />
          </div>
        </main>
      </div>
    </>
  );
}

function isDuplicatePortfolioNameMessage(msg) {
  return typeof msg === "string" && (msg.toLowerCase().includes("already exists") || msg.toLowerCase().includes("name already taken"));
}

function SavePortfolioForm({ onSave, onCancel, onStartOver, startOverDisabled = false }) {
  const [portfolioName, setPortfolioName] = useState("");
  const [saveDescription, setSaveDescription] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaveError("");
    if (!portfolioName?.trim()) {
      setSaveError("Please enter a portfolio name.");
      return;
    }
    setSaving(true);
    try {
      await onSave(portfolioName.trim(), saveDescription.trim());
      setPortfolioName("");
      setSaveDescription("");
    } catch (err) {
      const msg = err?.message || "Save failed.";
      setSaveError(msg);
      if (isDuplicatePortfolioNameMessage(msg)) {
        setPortfolioName("");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="form-panel">
      <h3 style={{ marginBottom: 12, color: "var(--text)" }}>Save portfolio</h3>
      <form onSubmit={handleSubmit}>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span>Portfolio name</span>
          <input
            type="text"
            placeholder="e.g. Retirement 2025"
            value={portfolioName}
            onChange={(e) => {
              setPortfolioName(e.target.value);
              setSaveError("");
            }}
            autoComplete="off"
            disabled={saving}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 12 }}>
          <span style={{ fontSize: 12, color: "#888" }}>Description <span style={{ color: "#666", fontWeight: "normal" }}>(optional)</span></span>
          <textarea
            placeholder="Short note about this portfolio"
            value={saveDescription}
            onChange={(e) => {
              setSaveDescription(e.target.value);
              setSaveError("");
            }}
            rows={2}
            disabled={saving}
            style={{
              width: "100%",
              padding: 8,
              background: "#111",
              border: "1px solid #2a2a2a",
              borderRadius: 4,
              color: "var(--text)",
              fontSize: 13,
              resize: "vertical",
            }}
          />
        </label>
        {saveError ? (
          <p className="save-portfolio-inline-error" style={{ margin: "8px 0 0", fontSize: 13, color: "#e8a0a0", lineHeight: 1.4 }}>
            {saveError}
          </p>
        ) : null}
        <div className="save-portfolio-actions">
          <button type="submit" className="form-primary-btn" disabled={saving}>
            {saving ? "Saving…" : "Save portfolio"}
          </button>
          <button type="button" className="form-primary-btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          {typeof onStartOver === "function" ? (
            <button type="button" className="form-primary-btn" onClick={onStartOver} disabled={saving || startOverDisabled}>
              Start over
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}

function AuthForm({
  accountGate = false,
  defaultAuthTab = "register",
  onLoginSuccess,
  onCancel,
}) {
  const termsScrollRef = useRef(null);
  const googleBtnRef = useRef(null);
  const googleMountCleanupRef = useRef(null);
  const [authTab, setAuthTab] = useState(defaultAuthTab === "login" ? "login" : "register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [termsScrolledToEnd, setTermsScrolledToEnd] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);

  useEffect(() => {
    setAuthTab(defaultAuthTab === "login" ? "login" : "register");
    setTermsScrolledToEnd(false);
    setTermsAccepted(false);
  }, [defaultAuthTab]);

  const bumpTermsScrollState = () => {
    const el = termsScrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 16;
    if (nearBottom) setTermsScrolledToEnd(true);
  };

  useEffect(() => {
    bumpTermsScrollState();
  }, [authTab]);

  const termsReady = termsScrolledToEnd && termsAccepted;
  const googleEnabled = isGoogleSignInConfigured();

  const handleGoogleCredential = useCallback(
    async (response) => {
      const token = response?.credential;
      if (!token) return;
      setFormError("");
      const needsTerms = authTab === "register" && !termsReady;
      if (needsTerms) {
        setFormError('Scroll through the Terms and check "I understand" before continuing with Google.');
        return;
      }
      setSaving(true);
      try {
        const res = await postJson("/api/auth/google", {
          id_token: token,
          accept_terms: authTab === "register" && termsReady,
          terms_version: QUALA_TNC_VERSION,
        });
        const emailOut = (res.email_id || "").trim();
        await onLoginSuccess(emailOut, res.user_id, {
          fromAccountModal: accountGate,
          persistIntake: authTab === "register" && !accountGate,
        });
      } catch (err) {
        setFormError(err?.message || "Google sign-in failed.");
      } finally {
        setSaving(false);
      }
    },
    [authTab, termsReady, accountGate, onLoginSuccess],
  );

  useEffect(() => {
    if (!googleEnabled || !googleBtnRef.current) return undefined;
    let cancelled = false;
    const el = googleBtnRef.current;
    el.innerHTML = "";
    (async () => {
      try {
        const cleanup = await mountGoogleSignInButton(el, (resp) => {
          if (!cancelled) handleGoogleCredential(resp);
        });
        if (cancelled) {
          cleanup();
          return;
        }
        googleMountCleanupRef.current = cleanup;
      } catch (err) {
        if (!cancelled) {
          console.warn(err);
        }
      }
    })();
    return () => {
      cancelled = true;
      if (googleMountCleanupRef.current) {
        googleMountCleanupRef.current();
        googleMountCleanupRef.current = null;
      }
    };
  }, [googleEnabled, authTab, termsReady, handleGoogleCredential]);

  const handleLoginTabSubmit = async (e) => {
    e.preventDefault();
    setFormError("");
    if (!email?.trim() || !password) {
      setFormError("Please enter email and password.");
      return;
    }
    setSaving(true);
    try {
      const res = await postJson("/api/auth/login", { email_id: email.trim(), password });
      await onLoginSuccess(email.trim(), res.user_id, { fromAccountModal: accountGate });
    } catch (err) {
      setFormError(err?.message || "Login failed.");
    } finally {
      setSaving(false);
    }
  };

  const handleRegisterGateSubmit = async (e) => {
    e.preventDefault();
    setFormError("");
    if (!termsReady) {
      setFormError('Please scroll to the end of the Terms & Conditions and check "I understand".');
      return;
    }
    if (!email?.trim() || !password) {
      setFormError("Please enter email and password.");
      return;
    }
    setSaving(true);
    try {
      const res = await postJson("/api/auth/register", {
        email_id: email.trim(),
        password,
        accept_terms: true,
        terms_version: QUALA_TNC_VERSION,
      });
      await onLoginSuccess(email.trim(), res.user_id, {
        fromAccountModal: accountGate,
        persistIntake: !accountGate,
      });
    } catch (err) {
      const raw = String(err?.message || "");
      const low = raw.toLowerCase();
      if (low.includes("already")) {
        setFormError("An account with this email already exists. Use Log in or choose a different email.");
      } else {
        setFormError(raw || "Registration failed.");
      }
    } finally {
      setSaving(false);
    }
  };

  const termsBlock = (
    <div style={{ margin: "12px 0 10px" }}>
      <div style={{ fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", color: "#c8a96e", marginBottom: 8 }}>
        Terms & Conditions
      </div>
      <div
        ref={termsScrollRef}
        onScroll={bumpTermsScrollState}
        style={{
          maxHeight: 220,
          overflowY: "auto",
          padding: "12px 14px",
          fontSize: 11,
          lineHeight: 1.5,
          color: "var(--text-soft)",
          background: "var(--surface-input)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          whiteSpace: "pre-wrap",
        }}
      >
        {QUALA_TNC_FULL_TEXT}
      </div>
      <label
        className="clickwrap-control"
        title="Scroll through all clauses above, then check this box to confirm you understand Quala.ai is an educational tool, not a financial advisor, and that you execute any trades yourself on your own brokerage platform."
      >
        <input
          type="checkbox"
          checked={termsAccepted}
          onChange={(e) => {
            setTermsAccepted(e.target.checked);
            setFormError("");
          }}
          disabled={saving}
        />
        <span>I understand</span>
      </label>
      {!termsScrolledToEnd ? (
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--text-muted)" }}>Scroll to the bottom of the terms box to continue.</p>
      ) : !termsAccepted ? (
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--text-muted)" }}>Check <strong>I understand</strong> to show email and password.</p>
      ) : null}
    </div>
  );

  return (
    <div className="form-panel">
      <h3 style={{ marginBottom: 10, color: "#e8e0d0" }}>{accountGate ? "Account" : "Sign in or create account"}</h3>
      <div className="auth-tab-row">
        <button
          type="button"
          className={`btn-outline${authTab === "login" ? " btn-outline--active" : ""}`}
          disabled={saving}
          onClick={() => {
            setAuthTab("login");
            setFormError("");
            setTermsScrolledToEnd(false);
            setTermsAccepted(false);
          }}
        >
          Login
        </button>
        <button
          type="button"
          className={`btn-outline${authTab === "register" ? " btn-outline--active" : ""}`}
          disabled={saving}
          onClick={() => {
            setAuthTab("register");
            setFormError("");
            setTermsScrolledToEnd(false);
            setTermsAccepted(false);
          }}
        >
          Create account
        </button>
        <button type="button" className="login-cancel-btn" style={{ marginLeft: "auto", padding: "8px 14px" }} disabled={saving} onClick={onCancel}>
          Cancel
        </button>
      </div>

      {formError ? (
        <p className="auth-form-inline-error" style={{ margin: "0 0 12px", fontSize: 13, color: "#e8a0a0", lineHeight: 1.4 }}>
          {formError}
        </p>
      ) : null}

      {authTab === "login" ? (
        <>
          {googleEnabled ? (
            <>
              <motion className="auth-google-wrap" ref={googleBtnRef} aria-hidden={saving} />
              <p className="auth-or-divider">or continue with email</p>
            </>
          ) : null}
        <form onSubmit={handleLoginTabSubmit} className="login-form">
          <p style={{ marginBottom: 12, fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
            Sign in with email and password. Terms are not required for returning users.
          </p>
          <label>
            Email
            <input
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setFormError("");
              }}
              required
              disabled={saving}
              autoFocus
            />
          </label>
          <label>
            Password
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setFormError("");
              }}
              required
              disabled={saving}
            />
          </label>
          <div className="login-modal-actions" style={{ marginTop: 16 }}>
            <button type="submit" className="form-primary-btn" disabled={saving}>
              {saving ? "Signing in…" : "Log in"}
            </button>
          </div>
        </form>
        </>
      ) : (
        <>
          {termsBlock}
          {termsReady && googleEnabled ? (
            <>
              <div className="auth-google-wrap" ref={googleBtnRef} aria-hidden={saving} />
              <p className="auth-or-divider">or continue with email</p>
            </>
          ) : null}
          {termsReady ? (
            <form onSubmit={handleRegisterGateSubmit} className="login-form">
              <p style={{ margin: "12px 0", fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                {accountGate
                  ? "Choose email and password for your new account."
                  : "Choose email and password. If this email is already registered, sign in with Log in instead."}
              </p>
              <label>
                Email{" "}
                <input
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setFormError("");
                  }}
                  required
                  disabled={saving}
                  autoFocus
                />
              </label>
              <label>
                Password{" "}
                <input
                  type="password"
                  placeholder="Min 6 characters"
                  minLength={6}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setFormError("");
                  }}
                  required
                  disabled={saving}
                />
              </label>
              <div className="login-modal-actions" style={{ marginTop: 16 }}>
                <button type="submit" className="form-primary-btn" disabled={saving}>
                  {saving ? "Working…" : "Create account"}
                </button>
              </div>
            </form>
          ) : (
            <p style={{ margin: "12px 0 0", fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              Scroll through the Terms above, then check <strong>I understand</strong>, to enter email and password.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function fmtAnalyzeMoney(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Format CSV-derived portfolio total for the intake "initial investment" field (parseAmount-compatible). */
function formatAnalyzeTotalForInvestmentField(total) {
  if (!(typeof total === "number" && Number.isFinite(total) && total > 0)) return "";
  return total.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function fmtAnalyzeWeight(w) {
  if (typeof w !== "number" || !Number.isFinite(w)) return "—";
  return `${(100 * w).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

const ANALYZE_WEIGHT_SIG_DIGITS = 5;

function roundToSignificantDigits(value, significantDigits = ANALYZE_WEIGHT_SIG_DIGITS) {
  if (value === 0 || !Number.isFinite(value)) return value;
  const sign = value < 0 ? -1 : 1;
  const v = Math.abs(value);
  const exp = Math.floor(Math.log10(v));
  const nd = significantDigits - 1 - exp;
  const factor = 10 ** nd;
  return sign * Math.round(v * factor) / factor;
}

/** Match backend: 5 sig figs, renormalize to sum 1, adjust largest weight for rounding. */
function computeAnalyzeRowWeightsSigDigits(rows, total, sig = ANALYZE_WEIGHT_SIG_DIGITS) {
  const arr = rows || [];
  if (!arr.length || !(total > 0)) return arr.map(() => 0);
  const raw = arr.map((r) => (Number(r.current_amount) || 0) / total);
  let vals = raw.map((p) => roundToSignificantDigits(p, sig));
  let s = vals.reduce((a, b) => a + b, 0);
  if (s <= 0) return raw;
  vals = vals.map((v) => roundToSignificantDigits(v / s, sig));
  const mi = vals.reduce((bestI, v, i, a) => (v > a[bestI] ? i : bestI), 0);
  const rest = vals.reduce((a, v, i) => (i === mi ? a : a + v), 0);
  vals = [...vals];
  vals[mi] = roundToSignificantDigits(1 - rest, sig);
  if (vals[mi] < 0) {
    vals[mi] = 0;
    const s2 = vals.reduce((a, b) => a + b, 0);
    if (s2 > 0) vals = vals.map((v) => roundToSignificantDigits(v / s2, sig));
  }
  return vals;
}

function AnalyzeForm({
  sessionId,
  userId,
  retirementStatus,
  theme = "dark",
  onAnalyzeBacktestComplete,
  onAnalyzeStartOver,
  onComputedPortfolioTotal,
  onSaveAnalyzePortfolio,
  onAnalyzeSaveCancel,
}) {
  const [sources, setSources] = useState([]);
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [computingValues, setComputingValues] = useState(false);
  const [error, setError] = useState("");
  const [editable, setEditable] = useState(false);
  const [weightsCalculated, setWeightsCalculated] = useState(false);
  const [showPortfolioKindChoice, setShowPortfolioKindChoice] = useState(false);
  const [analyzeBacktestLoading, setAnalyzeBacktestLoading] = useState(false);
  const [analyzeBacktestArtifacts, setAnalyzeBacktestArtifacts] = useState(null);
  const [analyzeAgentReply, setAnalyzeAgentReply] = useState("");
  const fileInputRef = useRef(null);
  /** DB row from last successful agent-backtest (`/api/analyze-portfolio/agent-backtest`). */
  const analyzedPortfolioIdRef = useRef(null);
  const lastSyncedAnalyzeTotalRef = useRef(null);
  const onComputedPortfolioTotalRef = useRef(onComputedPortfolioTotal);
  onComputedPortfolioTotalRef.current = onComputedPortfolioTotal;

  useEffect(() => {
    if (rows == null) {
      lastSyncedAnalyzeTotalRef.current = null;
      return;
    }
    if (computingValues) return;
    const total = (rows || []).reduce((s, r) => s + (Number(r.current_amount) || 0), 0);
    if (!(total > 0)) return;
    if (lastSyncedAnalyzeTotalRef.current === total) return;
    lastSyncedAnalyzeTotalRef.current = total;
    onComputedPortfolioTotalRef.current?.(total);
  }, [rows, computingValues]);

  const resetUpload = async () => {
    const pid = analyzedPortfolioIdRef.current;
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (pid && uid) {
      try {
        await deleteJson(
          `/api/analyze-portfolio/${encodeURIComponent(pid)}?user_id=${encodeURIComponent(uid)}`,
        );
      } catch {
        /* non-fatal: UI reset still proceeds */
      }
    }
    analyzedPortfolioIdRef.current = null;
    setSources([]);
    setRows(null);
    setEditable(false);
    setWeightsCalculated(false);
    setShowPortfolioKindChoice(false);
    setAnalyzeBacktestArtifacts(null);
    setAnalyzeAgentReply("");
    setError("");
    onAnalyzeStartOver?.();
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const parseErrMsg = (body) => {
    const d = body?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x?.msg || x).join("; ");
    return "Request failed";
  };

  const handleFilesSelect = async (e) => {
    const list = Array.from(e.target.files || []);
    if (!list.length) return;
    setError("");
    setRows(null);
    setEditable(false);
    setWeightsCalculated(false);
    setLoading(true);
    try {
      const parsed = await Promise.all(
        list.map(async (file) => {
          const form = new FormData();
          form.append("file", file);
          const res = await fetch(resolveApiUrl("/api/analyze-portfolio/parse"), { method: "POST", body: form });
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(`${file.name}: ${parseErrMsg(body)}`);
          }
          const data = await res.json();
          return {
            id: crypto.randomUUID(),
            fileName: file.name,
            columns: data.columns || [],
            preview_rows: data.preview_rows || [],
            tickerCol: "",
            qtyCol: "",
            locked: false,
          };
        })
      );
      setSources((prev) => [...prev, ...parsed]);
    } catch (err) {
      setError(err.message || "Failed to read CSV");
    } finally {
      setLoading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const updateSource = (id, updates) => {
    setSources((prev) => prev.map((s) => (s.id === id ? { ...s, ...updates } : s)));
  };

  const confirmSourceMapping = (id) => {
    const s = sources.find((x) => x.id === id);
    if (!s) return;
    const t = s.tickerCol.trim();
    const q = s.qtyCol.trim();
    if (!t || !q) {
      setError("For each file, enter the ticker column and quantity column (header names).");
      return;
    }
    setError("");
    updateSource(id, { locked: true });
  };

  const removeSource = (id) => {
    setSources((prev) => prev.filter((s) => s.id !== id));
    setRows(null);
    setEditable(false);
    setWeightsCalculated(false);
    setShowPortfolioKindChoice(false);
    setAnalyzeBacktestArtifacts(null);
    setAnalyzeAgentReply("");
  };

  const allSourcesLocked = sources.length > 0 && sources.every((s) => s.locked);

  useEffect(() => {
    if (!allSourcesLocked || rows != null || sources.length === 0) return;

    let cancelled = false;
    setComputingValues(true);
    setError("");
    postJson("/api/analyze-portfolio/values", {
      batches: sources.map((s) => ({
        preview_rows: s.preview_rows,
        ticker_column: s.tickerCol.trim(),
        quantity_column: s.qtyCol.trim(),
        columns: s.columns,
      })),
    })
      .then((data) => {
        if (!cancelled) {
          setRows(data.rows || []);
          setEditable(false);
          setWeightsCalculated(false);
          setShowPortfolioKindChoice(false);
          setAnalyzeBacktestArtifacts(null);
          setAnalyzeAgentReply("");
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Could not build combined portfolio");
      })
      .finally(() => {
        if (!cancelled) setComputingValues(false);
      });

    return () => {
      cancelled = true;
    };
  }, [allSourcesLocked, rows, sources]);

  const updateRow = (index, field, value) => {
    setRows((prev) => {
      const next = [...prev];
      if (!next[index]) return prev;
      let row = { ...next[index], [field]: value };
      if (field === "quantity") {
        const qty = typeof value === "number" ? value : parseFloat(value) || 0;
        const close = Number(row.close) || 0;
        row.quantity = qty;
        row.current_amount = qty * close;
      }
      if (field === "ticker") {
        row.close = 0;
        row.current_amount = 0;
      }
      next[index] = row;
      return next;
    });
  };

  const addAnalyzeRow = () => {
    setRows((prev) => [...(prev || []), { ticker: "", quantity: 0, close: 0, current_amount: 0 }]);
  };

  const handleAddAnalyzeRowClick = () => {
    setError("");
    if (!editable) setEditable(true);
    addAnalyzeRow();
  };

  const removeAnalyzeRow = (index) => {
    setRows((prev) => {
      const arr = prev || [];
      if (index < 0 || index >= arr.length) return arr;
      return arr.filter((_, i) => i !== index);
    });
  };

  const refreshAnalyzeTableFromHoldings = async () => {
    const holdings = (rows || []).map((r) => ({
      ticker: String(r.ticker || "").trim().toUpperCase(),
      quantity: Number(r.quantity) || 0,
    }));
    const nonEmpty = holdings.filter((h) => h.ticker);
    if (!nonEmpty.some((h) => h.quantity !== 0)) {
      setError("Each row needs a ticker and a non-zero quantity (remove empty rows or finish filling them).");
      return;
    }
    setComputingValues(true);
    setError("");
    try {
      const data = await postJson("/api/analyze-portfolio/enrich", { holdings: nonEmpty });
      setRows(data.rows || []);
      setEditable(false);
      setWeightsCalculated(false);
      setShowPortfolioKindChoice(false);
      setAnalyzeBacktestArtifacts(null);
      setAnalyzeAgentReply("");
    } catch (err) {
      setError(err.message || "Could not refresh prices");
    } finally {
      setComputingValues(false);
    }
  };

  const toggleAnalyzeEditTable = async () => {
    if (!editable) {
      setEditable(true);
      setWeightsCalculated(false);
      setShowPortfolioKindChoice(false);
      setAnalyzeBacktestArtifacts(null);
      setAnalyzeAgentReply("");
      setRows((prev) => (prev || []).map(({ weight: _w, ...r }) => r));
      setError("");
      return;
    }
    await refreshAnalyzeTableFromHoldings();
  };

  const analyzeTotalCurrent = (rows || []).reduce((s, r) => s + (Number(r.current_amount) || 0), 0);

  const handleCalculateRelativeWeights = () => {
    if (!rows?.length) return;
    const total = analyzeTotalCurrent;
    if (!(total > 0)) {
      setError("Total current amount must be greater than zero to compute weights.");
      return;
    }
    setError("");
    setRows((prev) => {
      const arr = prev || [];
      const wArr = computeAnalyzeRowWeightsSigDigits(arr, total, ANALYZE_WEIGHT_SIG_DIGITS);
      return arr.map((r, i) => ({
        ...r,
        weight: wArr[i],
      }));
    });
    setWeightsCalculated(true);
  };

  const openAnalyzePortfolioKindChoice = () => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) {
      setError("Please log in to analyze your portfolio.");
      return;
    }
    if (!rows?.length || !weightsCalculated) return;
    setError("");
    const rs = String(retirementStatus || "").trim();
    if (rs === "both_working") {
      runAnalyzePortfolioBacktest("growth");
      return;
    }
    if (rs === "both_retired") {
      runAnalyzePortfolioBacktest("income");
      return;
    }
    setShowPortfolioKindChoice(true);
  };

  const runAnalyzePortfolioBacktest = async (portfolioKind) => {
    const uid = userId ?? localStorage.getItem(USER_ID_KEY);
    if (!uid) {
      setError("Please log in to analyze your portfolio.");
      return;
    }
    const weightsByTicker = {};
    (rows || []).forEach((r) => {
      const t = String(r.ticker || "").trim().toUpperCase();
      if (t && typeof r.weight === "number" && Number.isFinite(r.weight)) {
        weightsByTicker[t] = r.weight;
      }
    });
    if (!Object.keys(weightsByTicker).length) {
      setError("Weights are missing. Calculate relative weights first.");
      return;
    }
    setAnalyzeBacktestLoading(true);
    setError("");
    try {
      const payload = {
        session_id: sessionId || undefined,
        user_id: uid,
        weights_by_ticker: weightsByTicker,
      };
      if (portfolioKind) payload.portfolio_kind = portfolioKind;
      const res = await postJson("/api/analyze-portfolio/agent-backtest", {
        ...payload,
      });
      const newId =
        res.analyzed_portfolio_id != null && String(res.analyzed_portfolio_id).trim()
          ? String(res.analyzed_portfolio_id).trim()
          : "";
      const prev = analyzedPortfolioIdRef.current;
      if (prev && newId && prev !== newId && uid) {
        deleteJson(`/api/analyze-portfolio/${encodeURIComponent(prev)}?user_id=${encodeURIComponent(uid)}`).catch(
          () => {},
        );
      }
      analyzedPortfolioIdRef.current = newId || null;
      setAnalyzeBacktestArtifacts(res.artifacts || null);
      setAnalyzeAgentReply(typeof res.reply === "string" ? res.reply : "");
      setShowPortfolioKindChoice(false);
      onAnalyzeBacktestComplete?.(res.artifacts, res.agent);
    } catch (err) {
      setError(err.message || "Backtest failed");
    } finally {
      setAnalyzeBacktestLoading(false);
    }
  };

  const analyzeUid = userId ?? localStorage.getItem(USER_ID_KEY);
  const showPostAnalyzeSave =
    !analyzeBacktestLoading && !!analyzeBacktestArtifacts && !!onSaveAnalyzePortfolio && !!analyzeUid;

  const isLightTheme = theme === "light";
  const csvSrcCard = isLightTheme
    ? {
        cardBg: "#ffffff",
        cardBorder: "1px solid var(--border)",
        titleColor: "var(--text)",
        metaColor: "var(--text-muted)",
        codeBg: "#f4f4f4",
        previewBorder: "1px solid var(--border)",
        thBg: "#f4f4f4",
        thBorderBottom: "1px solid var(--border)",
        trBorder: "1px solid var(--border-soft)",
        inputBg: "var(--surface-input)",
        inputBorder: "1px solid var(--border)",
      }
    : {
        cardBg: "#121212",
        cardBorder: "1px solid #2a2a2a",
        titleColor: "#e8e0d0",
        metaColor: "#888",
        codeBg: "#1a1a1a",
        previewBorder: "1px solid #1f1f1f",
        thBg: "#151515",
        thBorderBottom: "1px solid #2a2a2a",
        trBorder: "1px solid #1a1a1a",
        inputBg: "#111",
        inputBorder: "1px solid #2a2a2a",
      };

  const analyzeThemeText = isLightTheme
    ? { heading: "var(--text)", totalLabel: "var(--text-muted)", totalValue: "var(--text)" }
    : { heading: "#e8e0d0", totalLabel: "#888", totalValue: "#e8e0d0" };

  const analyzeReplyBox = isLightTheme
    ? { background: "#ffffff", border: "1px solid var(--border)" }
    : { background: "#141414", border: "1px solid #2a2a2a" };

  return (
    <div className="form-panel form-panel--analyze-full">
      <h3 style={{ marginBottom: 12, color: analyzeThemeText.heading }}>Portfolio Analysis</h3>
      <p style={{ fontSize: 12, color: "#888", marginBottom: 16 }}>
        Upload one or more CSVs. For each file, map the ticker and quantity columns. Multiple files merge; duplicate
        tickers have quantities summed. Then confirm weights and run growth or income analysis. To type tickers and
        weights directly, open a saved portfolio and use <strong style={{ color: "#c8a96e" }}>Update portfolio</strong>.
      </p>

      {!rows ? (
        <>
          <label
            className={`analyze-file-upload-btn${loading || computingValues ? " is-disabled" : ""}`}
            style={{ marginBottom: 12 }}
          >
            <span>Add CSV file(s)</span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              multiple
              onChange={handleFilesSelect}
              disabled={loading || computingValues}
            />
          </label>
          {loading && (
            <div style={{ marginBottom: 8, color: "#888", fontSize: 13 }}>Reading file(s)…</div>
          )}

          {sources.map((src) => (
            <div
              key={src.id}
              style={{
                marginBottom: 20,
                padding: 12,
                border: csvSrcCard.cardBorder,
                borderRadius: 8,
                background: csvSrcCard.cardBg,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontSize: 14, color: csvSrcCard.titleColor, fontWeight: 600 }}>{src.fileName}</div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  {src.locked ? (
                    <span style={{ fontSize: 12, color: "#6a9a6a" }}>Mapping confirmed</span>
                  ) : null}
                  <button
                    type="button"
                    className="form-primary-btn"
                    onClick={() => removeSource(src.id)}
                    disabled={computingValues}
                  >
                    Remove file
                  </button>
                </div>
              </div>

              {!src.locked ? (
                <>
                  <div style={{ fontSize: 11, color: csvSrcCard.metaColor, marginBottom: 8 }}>
                    Columns:{" "}
                    {src.columns.map((c) => (
                      <code key={c} style={{ marginRight: 6, background: csvSrcCard.codeBg, padding: "1px 5px" }}>
                        {c}
                      </code>
                    ))}
                  </div>

                  <div
                    style={{
                      overflowX: "auto",
                      maxHeight: 180,
                      marginBottom: 12,
                      border: csvSrcCard.previewBorder,
                      borderRadius: 4,
                    }}
                  >
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr style={{ borderBottom: csvSrcCard.thBorderBottom, background: csvSrcCard.thBg }}>
                          {src.columns.map((c) => (
                            <th key={c} style={{ padding: 6, textAlign: "left", whiteSpace: "nowrap" }}>
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {src.preview_rows.map((rec, i) => (
                          <tr key={i} style={{ borderBottom: csvSrcCard.trBorder }}>
                            {src.columns.map((c) => (
                              <td key={c} style={{ padding: 6 }}>
                                {rec[c] != null && rec[c] !== "" ? String(rec[c]) : "—"}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 12 }}>Ticker (e.g. MSFT) column name</span>
                      <input
                        type="text"
                        value={src.tickerCol}
                        onChange={(e) => {
                          updateSource(src.id, { tickerCol: e.target.value });
                          setError("");
                        }}
                        placeholder="e.g. Symbol"
                        disabled={computingValues}
                        autoComplete="off"
                        style={{
                          padding: 8,
                          background: csvSrcCard.inputBg,
                          border: csvSrcCard.inputBorder,
                          color: "var(--text)",
                        }}
                      />
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 12 }}>Quantity column</span>
                      <input
                        type="text"
                        value={src.qtyCol}
                        onChange={(e) => {
                          updateSource(src.id, { qtyCol: e.target.value });
                          setError("");
                        }}
                        placeholder="e.g. Shares"
                        disabled={computingValues}
                        autoComplete="off"
                        style={{
                          padding: 8,
                          background: csvSrcCard.inputBg,
                          border: csvSrcCard.inputBorder,
                          color: "var(--text)",
                        }}
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    className="form-primary-btn"
                    onClick={() => confirmSourceMapping(src.id)}
                    disabled={computingValues}
                  >
                    Confirm mapping for this file
                  </button>
                </>
              ) : null}
            </div>
          ))}

          {allSourcesLocked && computingValues ? (
            <div style={{ marginTop: 12, fontSize: 13, color: "#888" }}>Building combined table and loading prices…</div>
          ) : null}
          {sources.length > 0 && !allSourcesLocked ? (
            <p style={{ marginTop: 10, fontSize: 12, color: "#777" }}>
              Confirm mapping on each file above. When every file is confirmed, the merged table appears automatically.
              Duplicate tickers across files are merged with summed quantity.
            </p>
          ) : null}
        </>
      ) : (
        <>
          <div style={{ overflowX: "auto", marginBottom: 0 }}>
            {computingValues ? (
              <div style={{ marginBottom: 10, fontSize: 13, color: "#888" }}>Updating prices…</div>
            ) : null}
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #2a2a2a" }}>
                  <th style={{ padding: 8, textAlign: "left" }}>Ticker</th>
                  <th style={{ padding: 8, textAlign: "right" }}>Quantity</th>
                  <th style={{ padding: 8, textAlign: "right" }}>Current amount</th>
                  {weightsCalculated ? (
                    <th style={{ padding: 8, textAlign: "right" }}>Weights</th>
                  ) : null}
                  <th style={{ padding: 8, width: 44, textAlign: "right" }} aria-label="Add row" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isLast = i === rows.length - 1;
                  const addCell = isLast ? (
                    <button
                      type="button"
                      className="form-primary-btn analyze-table-add-btn"
                      onClick={handleAddAnalyzeRowClick}
                      disabled={analyzeBacktestLoading || computingValues}
                      title="Add row"
                      aria-label="Add row"
                    >
                      +
                    </button>
                  ) : null;
                  const removeCell = editable ? (
                    <button
                      type="button"
                      className="form-primary-btn analyze-table-add-btn"
                      onClick={() => removeAnalyzeRow(i)}
                      disabled={analyzeBacktestLoading || computingValues}
                      title="Delete row"
                      aria-label="Delete row"
                    >
                      -
                    </button>
                  ) : null;
                  return (
                    <tr key={i} style={{ borderBottom: "1px solid #1a1a1a" }}>
                      <td style={{ padding: 8 }}>
                        {editable ? (
                          <input
                            value={r.ticker}
                            onChange={(e) => updateRow(i, "ticker", e.target.value.toUpperCase())}
                            disabled={computingValues}
                            placeholder="e.g. VTI"
                            style={{
                              width: "100%",
                              padding: 6,
                              background: isLightTheme ? "#ffffff" : "#111",
                              border: isLightTheme ? "1px solid var(--border)" : "1px solid #2a2a2a",
                              borderRadius: 4,
                              color: "var(--text)",
                              fontSize: 13,
                            }}
                          />
                        ) : (
                          r.ticker
                        )}
                      </td>
                      <td style={{ padding: 8, textAlign: "right" }}>
                        {editable ? (
                          <input
                            type="number"
                            value={r.quantity}
                            onChange={(e) => updateRow(i, "quantity", parseFloat(e.target.value) || 0)}
                            disabled={computingValues}
                            style={{
                              width: 80,
                              padding: 6,
                              background: "#111",
                              border: "1px solid #2a2a2a",
                              borderRadius: 4,
                              color: "var(--text)",
                              fontSize: 13,
                            }}
                          />
                        ) : (
                          r.quantity
                        )}
                      </td>
                      <td style={{ padding: 8, textAlign: "right" }}>{fmtAnalyzeMoney(r.current_amount)}</td>
                      {weightsCalculated ? (
                        <td style={{ padding: 8, textAlign: "right" }}>{fmtAnalyzeWeight(r.weight)}</td>
                      ) : null}
                      <td style={{ padding: 8, textAlign: "right", verticalAlign: "middle" }}>
                        <span style={{ display: "inline-flex", gap: 6 }}>
                          {removeCell}
                          {addCell}
                        </span>
                      </td>
                    </tr>
                  );
                })}
                {rows.length === 0 ? (
                  <tr style={{ borderBottom: "1px solid #1a1a1a" }}>
                    <td
                      colSpan={weightsCalculated ? 4 : 3}
                      style={{ padding: 8, color: "#666", fontSize: 12 }}
                    >
                      No holdings in table.
                    </td>
                    <td style={{ padding: 8, textAlign: "right", verticalAlign: "middle" }}>
                      <button
                        type="button"
                        className="form-primary-btn analyze-table-add-btn"
                        onClick={handleAddAnalyzeRowClick}
                        disabled={analyzeBacktestLoading || computingValues}
                        title="Add row"
                        aria-label="Add row"
                      >
                        +
                      </button>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
            <div style={{ marginTop: 10, fontSize: 13, color: analyzeThemeText.totalLabel }}>
              Total current amount:{" "}
              <strong style={{ color: analyzeThemeText.totalValue }}>
                {fmtAnalyzeMoney(rows.reduce((s, r) => s + (Number(r.current_amount) || 0), 0))}
              </strong>
            </div>
            <p style={{ marginTop: 8, fontSize: 11, color: "#666" }}>One row per ticker after merging files.</p>
          </div>

          <div
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              marginTop: 4,
              marginBottom: 16,
            }}
          >
            {weightsCalculated && showPortfolioKindChoice ? (
              <>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={() => runAnalyzePortfolioBacktest("growth")}
                  disabled={analyzeBacktestLoading || computingValues || !rows?.length}
                >
                  1) Growth portfolio
                </button>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={() => runAnalyzePortfolioBacktest("income")}
                  disabled={analyzeBacktestLoading || computingValues || !rows?.length}
                >
                  2) Retirement portfolio
                </button>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={() => setShowPortfolioKindChoice(false)}
                  disabled={analyzeBacktestLoading}
                >
                  Back
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={weightsCalculated ? openAnalyzePortfolioKindChoice : handleCalculateRelativeWeights}
                  disabled={
                    analyzeBacktestLoading ||
                    computingValues ||
                    !rows?.length ||
                    (!weightsCalculated && !(analyzeTotalCurrent > 0))
                  }
                >
                  {weightsCalculated ? "Analyze portfolio" : "Calculate relative weights"}
                </button>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={toggleAnalyzeEditTable}
                  disabled={analyzeBacktestLoading || computingValues}
                >
                  {editable ? "Done editing" : "Edit table"}
                </button>
                <button
                  type="button"
                  className="form-primary-btn"
                  onClick={resetUpload}
                  disabled={analyzeBacktestLoading || computingValues}
                >
                  Start over
                </button>
              </>
            )}
          </div>

          {analyzeBacktestLoading ? (
            <div style={{ marginBottom: 12, fontSize: 13, color: "#888" }}>Running backtest and Monte Carlo…</div>
          ) : null}
          {!analyzeBacktestLoading && analyzeAgentReply ? (
            <div
              style={{
                marginBottom: 16,
                padding: "14px 16px",
                borderRadius: 8,
                border: analyzeReplyBox.border,
                background: analyzeReplyBox.background,
                fontSize: 14,
                lineHeight: 1.55,
                color: "var(--text)",
                whiteSpace: "pre-wrap",
              }}
            >
              {analyzeAgentReply}
            </div>
          ) : null}
          {!analyzeBacktestLoading && analyzeBacktestArtifacts ? (
            <div style={{ marginBottom: 16, width: "100%" }}>
              <ChartContainer artifacts={analyzeBacktestArtifacts} fullWidth theme={theme} />
              {artifactsHaveInlineCharts(analyzeBacktestArtifacts) ? (
                <AdvisorModelOutputDisclaimer className="analyze-advisor-disclaimer" />
              ) : null}
            </div>
          ) : null}
          {showPostAnalyzeSave ? (
            <div style={{ maxWidth: 480 }}>
              <p style={{ fontSize: 13, color: "#94a3b8", marginBottom: 12, lineHeight: 1.5 }}>
                Save this portfolio to your account with a name and optional description (same as after building a portfolio in chat).
              </p>
              <SavePortfolioForm
                onSave={onSaveAnalyzePortfolio}
                onCancel={onAnalyzeSaveCancel}
                onStartOver={resetUpload}
                startOverDisabled={analyzeBacktestLoading || computingValues}
              />
            </div>
          ) : null}
        </>
      )}

      {error && (
        <div style={{ marginTop: 12, fontSize: 13, color: "#ef4444" }}>{error}</div>
      )}
    </div>
  );
}
