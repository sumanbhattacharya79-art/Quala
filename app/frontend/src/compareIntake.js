import { parseAmount } from "./api.js";

/** Flatten nested intake objects into path → string value — compare table uses form field order. */

/**
 * Intake top-level key order (matches form / backtest payload flow).
 * Rows are grouped by first path segment, then subpaths sorted alphabetically.
 */
export const INTAKE_COMPARE_BASE_ORDER = [
  "planning_for",
  "retirement_status",
  "retirement_timeline_self",
  "retirement_timeline_partner",
  "birth_dates",
  "state",
  "country",
  "inflation_assumption",
  "risk",
  "initial_value",
  "monthly_savings",
  "horizon_years",
  "longevity_years",
  "current_monthly_expense",
  "spending",
  "other_notes",
  "retirement_effective_tax_rate",
  "retirement_discretionary_monthly",
  "retirement_discretionary_in_year",
  "retirement_discretionary_min_prior_year_return_pct",
  "retirement_discretionary_start_age",
  "retirement_discretionary_end_age",
  "retirement_income_rows",
  "retirement_misc_spending_rows",
  "windfall_inflow_rows",
  "growth_monthly_income_rows",
  "growth_monthly_income_freeform",
  "growth_misc_spending_rows",
  "growth_misc_spending_freeform",
  "growth_one_time_inflow_rows",
  "display_unit",
];

/** Full segment keys (last path segment or each dot part) → display label */
const INTAKE_PATH_SEGMENT_LABELS = {
  growth_monthly_income_rows: "Monthly income",
  growth_monthly_income_freeform: "Monthly income (freeform)",
  growth_misc_spending_rows: "Growth misc spending",
  growth_one_time_inflow_rows: "Growth one-time inflow",
  retirement_income_rows: "Retirement monthly income",
  retirement_misc_spending_rows: "Retirement misc spending",
  windfall_inflow_rows: "Windfall inflow",
};

function _snakeToSentence(s) {
  if (!s) return s;
  const spaced = String(s).replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/**
 * Human-readable field path for compare table (no snake_case segments).
 * @param {string} path
 * @returns {string}
 */
export function prettyIntakeFieldPath(path) {
  if (!path) return "";
  return path
    .split(".")
    .map((seg) => {
      if (INTAKE_PATH_SEGMENT_LABELS[seg]) return INTAKE_PATH_SEGMENT_LABELS[seg];
      if (/^\d+$/.test(seg)) return `Item ${Number(seg) + 1}`;
      return _snakeToSentence(seg);
    })
    .join(" › ");
}

function _fmtMoneyCompact(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return String(n);
  const abs = Math.abs(x);
  const sign = x < 0 ? "-" : "";
  // Trim only trailing decimals (e.g. $2.0K); never use /.?0+K$/ — it turns $10K into $1K.
  if (abs >= 1_000_000) return `${sign}$${parseFloat((abs / 1_000_000).toFixed(4))}M`.replace(/\.0+M$/, "M");
  if (abs >= 1000) return `${sign}$${parseFloat((abs / 1000).toFixed(4))}K`.replace(/\.0+K$/, "K");
  return `${sign}$${Math.round(abs)}`;
}

function _getNum(obj, ...keys) {
  for (const k of keys) {
    if (obj[k] != null && obj[k] !== "") {
      const n = Number(obj[k]);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

/** Dollar amounts may be stored as "20K", "1.5M" strings from LLM or DB — Number() alone yields NaN. */
function _getMoneyNum(obj, ...keys) {
  for (const k of keys) {
    const raw = obj[k];
    if (raw == null || raw === "") continue;
    if (typeof raw === "number" && Number.isFinite(raw) && raw !== 0) return raw;
    const s = String(raw).trim();
    if (!s) continue;
    const n = Number(s);
    if (Number.isFinite(n) && n > 0) return n;
    const p = parseAmount(s);
    if (typeof p === "number" && p > 0) return p;
  }
  return null;
}

function _isMonthlyIncomeStyleRow(o) {
  if (!o || typeof o !== "object" || Array.isArray(o)) return false;
  const m = _getMoneyNum(o, "monthly");
  if (m == null || m <= 0) return false;
  const sa = _getNum(o, "start_age", "startAge");
  return sa != null;
}

/** Path leaf key for intake arrays → phrasing (misc vs income must not both say "monthly income"). */
function _recurringMonthlyRowLabelForPath(path) {
  if (!path) return "Monthly amount";
  const p = path.includes(".") ? path.slice(path.lastIndexOf(".") + 1) : path;
  if (p === "growth_misc_spending_rows") return "Misc monthly spending";
  if (p === "retirement_misc_spending_rows") return "Retirement misc spending";
  if (p === "growth_monthly_income_rows") return "Extra monthly income to invest";
  if (p === "retirement_income_rows") return "Retirement monthly income";
  return "Monthly amount";
}

function _formatMonthlyAgeWindowRowsHuman(arr, path) {
  const phrase = _recurringMonthlyRowLabelForPath(path);
  const parts = [];
  for (const o of arr) {
    if (!_isMonthlyIncomeStyleRow(o)) return null;
    const m = _getMoneyNum(o, "monthly");
    const sa = _getNum(o, "start_age", "startAge");
    const eaRaw = o.end_age != null && o.end_age !== "" ? o.end_age : o.endAge;
    const ea = eaRaw != null && eaRaw !== "" ? Number(eaRaw) : null;
    const label = o.label != null && String(o.label).trim() ? String(o.label).trim() : "";
    const yoyRaw = o.yoy_annual_pct != null && o.yoy_annual_pct !== "" ? Number(o.yoy_annual_pct) : NaN;
    let s = `${phrase} ${_fmtMoneyCompact(m)} start age ${sa}`;
    // 100 is the API default for "no end age" from an empty form end-age field
    if (ea != null && Number.isFinite(ea) && ea !== 100) s += ` end age ${ea}`;
    if (Number.isFinite(yoyRaw) && yoyRaw !== 0) s += ` YoY ${yoyRaw}%`;
    if (label) s += ` label: ${label}`;
    parts.push(s);
  }
  return parts.join("; ");
}

function _isExpenseYearStyleRow(o) {
  if (!o || typeof o !== "object" || Array.isArray(o)) return false;
  const y = _getNum(o, "years", "year");
  const amt = _getMoneyNum(o, "amount");
  return y != null && amt != null;
}

function _formatExpenseYearRowsHuman(arr) {
  const norm = [];
  for (const o of arr) {
    if (!_isExpenseYearStyleRow(o)) return null;
    const years = Math.round(_getNum(o, "years", "year"));
    const amount = _getMoneyNum(o, "amount");
    const label = o.label != null && String(o.label).trim() ? String(o.label).trim() : "";
    norm.push({ years, amount, label });
  }
  norm.sort((a, b) => a.years - b.years || a.amount - b.amount || a.label.localeCompare(b.label));

  const groups = [];
  for (const row of norm) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.amount === row.amount &&
      last.label === row.label &&
      row.years === last.yMax + 1
    ) {
      last.yMax = row.years;
    } else {
      groups.push({ yMin: row.years, yMax: row.years, amount: row.amount, label: row.label });
    }
  }

  return groups
    .map(({ yMin, yMax, amount, label }) => {
      const amtStr = _fmtMoneyCompact(amount);
      let yearPhrase =
        yMin === yMax ? `in ${yMin} years` : `in years ${yMin}–${yMax}`;
      let s = `${amtStr} ${yearPhrase}`;
      if (label) s += ` (${label})`;
      return s;
    })
    .join(", ");
}

function _prettyObjectOneLine(obj) {
  const keys = Object.keys(obj).sort();
  return keys
    .map((k) => {
      if (k === "display_unit") return null;
      const label = INTAKE_PATH_SEGMENT_LABELS[k] || _snakeToSentence(k);
      return `${label}=${_csvQuoteField(obj[k])}`;
    })
    .filter(Boolean)
    .join(", ");
}

function _csvQuoteField(v) {
  if (v == null) return "";
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  if (/[",;\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** Arrays of objects → one line: `col1,col2; r1c1,r1c2; r2c1,r2c2`. Arrays of primitives → comma-separated. */
function _arrayToOneLineCsv(arr, path) {
  if (!Array.isArray(arr) || arr.length === 0) return "—";
  const allPlainObjects = arr.every(
    (x) => x !== null && typeof x === "object" && !Array.isArray(x),
  );
  if (allPlainObjects) {
    if (arr.every(_isMonthlyIncomeStyleRow)) {
      const human = _formatMonthlyAgeWindowRowsHuman(arr, path);
      if (human) return human;
    }
    if (arr.every(_isExpenseYearStyleRow)) {
      const human = _formatExpenseYearRowsHuman(arr);
      if (human) return human;
    }
    const keySet = new Set();
    arr.forEach((obj) => Object.keys(obj).forEach((k) => keySet.add(k)));
    const firstKeys = Object.keys(arr[0] || {}).filter((k) => k !== "display_unit");
    const rest = [...keySet].filter((k) => !firstKeys.includes(k) && k !== "display_unit").sort();
    const keys = [...firstKeys, ...rest];
    const header = keys.map((k) => INTAKE_PATH_SEGMENT_LABELS[k] || _snakeToSentence(k)).join(", ");
    const rows = arr.map((obj) => keys.map((k) => _csvQuoteField(obj[k])).join(", "));
    return `${header}; ${rows.join("; ")}`;
  }
  const allScalarish = arr.every(
    (x) => x === null || x === undefined || (typeof x !== "object" && typeof x !== "function"),
  );
  if (allScalarish) {
    return arr.map((x) => _csvQuoteField(x)).join(", ");
  }
  return JSON.stringify(arr);
}

/**
 * Turn a stored cell string (JSON.stringify output) into readable one-line text.
 * Arrays (esp. arrays of objects) use CSV-style `header; row1; row2` on one line — not indented JSON.
 * @param {string} [fieldPath] - intake leaf path (e.g. growth_misc_spending_rows) for correct misc vs income wording.
 */
export function prettyIntakeCellValue(s, fieldPath) {
  if (s == null || s === "") return "—";
  if (s === "—") return "—";
  const str = String(s).trim();
  if (str === "") return "—";

  try {
    const parsed = JSON.parse(str);
    if (parsed === null) return "—";
    if (typeof parsed === "string") return parsed;
    if (typeof parsed === "number" || typeof parsed === "boolean") return String(parsed);
    if (Array.isArray(parsed)) return _arrayToOneLineCsv(parsed, fieldPath);
    if (typeof parsed === "object") return _prettyObjectOneLine(parsed);
    return str;
  } catch {
    return str;
  }
}

function flattenIntakeLeaves(obj, prefix = "") {
  const rows = [];
  if (obj === null || obj === undefined) {
    rows.push({ path: prefix || "(root)", value: String(obj) });
    return rows;
  }
  if (typeof obj !== "object") {
    rows.push({ path: prefix || "(root)", value: JSON.stringify(obj) });
    return rows;
  }
  if (Array.isArray(obj)) {
    rows.push({ path: prefix || "[]", value: JSON.stringify(obj) });
    return rows;
  }
  for (const k of Object.keys(obj).sort()) {
    if (k === "display_unit") continue;
    const p = prefix ? `${prefix}.${k}` : k;
    const v = obj[k];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      rows.push(...flattenIntakeLeaves(v, p));
    } else {
      rows.push({ path: p, value: v === undefined ? "" : JSON.stringify(v) });
    }
  }
  return rows;
}

function _comparePathSortKey(path) {
  const base = path.includes(".") ? path.slice(0, path.indexOf(".")) : path;
  const idx = INTAKE_COMPARE_BASE_ORDER.indexOf(base);
  const primary = idx === -1 ? 1000 : idx;
  return [primary, path];
}

/** One row per leaf path; form-like field order; marks diff when values differ (including missing on one side). */
export function mergedIntakeDiffRows(intakeA, intakeB) {
  const fa = flattenIntakeLeaves(intakeA && typeof intakeA === "object" ? intakeA : {});
  const fb = flattenIntakeLeaves(intakeB && typeof intakeB === "object" ? intakeB : {});
  const mapA = new Map(fa.map((r) => [r.path, r.value]));
  const mapB = new Map(fb.map((r) => [r.path, r.value]));
  const paths = [...new Set([...mapA.keys(), ...mapB.keys()])].sort((a, b) => {
    const [ia, pa] = _comparePathSortKey(a);
    const [ib, pb] = _comparePathSortKey(b);
    if (ia !== ib) return ia - ib;
    return pa.localeCompare(pb);
  });
  return paths.map((path) => {
    const a = mapA.has(path) ? mapA.get(path) : null;
    const b = mapB.has(path) ? mapB.get(path) : null;
    return {
      path,
      left: a === null ? "—" : a,
      right: b === null ? "—" : b,
      diff: a !== b,
    };
  });
}
