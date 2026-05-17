/** Production (e.g. Vercel): set `VITE_API_BASE_URL` to your FastAPI origin, no trailing slash. Local dev: leave unset so Vite proxies `/api`. */
export function resolveApiUrl(url) {
  if (typeof url !== "string") return url;
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  const base = String(import.meta.env.VITE_API_BASE_URL ?? "")
    .trim()
    .replace(/\/$/, "");
  if (!base) return url;
  const path = url.startsWith("/") ? url : `/${url}`;
  return `${base}${path}`;
}

export async function getJson(url, options = {}) {
  const res = await fetch(resolveApiUrl(url), {
    cache: options.cache ?? "no-store",
    signal: options.signal,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = typeof detail.detail === "string" ? detail.detail : "Request failed";
    throw new Error(msg);
  }
  return res.json();
}

export async function postJson(url, payload, options = {}) {
  const res = await fetch(resolveApiUrl(url), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg =
      typeof detail.detail === "string"
        ? detail.detail
        : Array.isArray(detail.detail)
          ? detail.detail.map((d) => d.msg || d).join("; ")
          : "Request failed";
    throw new Error(msg);
  }
  return res.json();
}

export async function putJson(url, payload) {
  const res = await fetch(resolveApiUrl(url), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg =
      typeof detail.detail === "string"
        ? detail.detail
        : Array.isArray(detail.detail)
          ? detail.detail.map((d) => d.msg || d).join("; ")
          : "Request failed";
    throw new Error(msg);
  }
  return res.json();
}

export async function deleteJson(url) {
  const res = await fetch(resolveApiUrl(url), { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = typeof detail.detail === "string" ? detail.detail : "Request failed";
    throw new Error(msg);
  }
  return res.json().catch(() => ({}));
}

/** Load persisted backtest/MC JSON for a saved portfolio or scenario (404 → null). */
export async function fetchPersistedBacktestArtifacts(portfolioId, userId, scenarioId = null) {
  if (!portfolioId || !userId) return null;
  const q = new URLSearchParams({ user_id: userId });
  if (scenarioId) q.set("scenario_id", scenarioId);
  const url = resolveApiUrl(
    `/api/portfolio/saved/${encodeURIComponent(portfolioId)}/backtest-artifacts?${q}`,
  );
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = typeof detail.detail === "string" ? detail.detail : "Request failed";
    throw new Error(msg);
  }
  const data = await res.json();
  return data?.artifacts ?? null;
}

export const SESSION_KEY = "portfolio-optimizer-session";
export const USER_ID_KEY = "portfolio-optimizer-user-id";
export const USER_EMAIL_KEY = "portfolio-optimizer-user-email";
export const FORM_STATE_KEY = "portfolio-optimizer-form-state";

export function getSessionId() {
  const id = localStorage.getItem(SESSION_KEY) || crypto.randomUUID();
  localStorage.setItem(SESSION_KEY, id);
  return id;
}

/** Persist backend-assigned session id so future requests use it and LLM sees a unique session. */
export function setSessionId(id) {
  if (id && typeof id === "string") {
    localStorage.setItem(SESSION_KEY, id);
  }
}

export function parseAmount(s) {
  if (!s || typeof s !== "string") return 0;
  s = s.trim().toUpperCase().replace(/[$€£¥,]/g, "").replace(/\s+/g, "");
  let mult = 1;
  if (s.endsWith("M") || s.endsWith("MIL") || s.endsWith("MILLION")) {
    mult = 1e6;
    s = s.replace(/(M|MIL|MILLION)$/, "");
  } else if (s.endsWith("K") || s.endsWith("THOUSAND")) {
    mult = 1e3;
    s = s.replace(/(K|THOUSAND)$/, "");
  } else if (s.endsWith("B") || s.endsWith("BN")) {
    mult = 1e9;
    s = s.replace(/(B|BN)$/, "");
  }
  const n = parseFloat(s);
  return isNaN(n) ? 0 : n * mult;
}

/** Infer display unit from amount string (e.g. "800 K" -> "K", "2.7M" -> "M"). */
export function parseDisplayUnit(s) {
  if (!s || typeof s !== "string") return null;
  const u = s.trim().toUpperCase().replace(/\s+/g, "");
  if (/[K]|THOUSAND/.test(u)) return "K";
  if (/[MB]|MIL|MILLION|BN/.test(u)) return "M";
  return null;
}

export function parseHorizonYears(text) {
  if (!text || typeof text !== "string") return null;
  const t = text.trim();
  const m = t.match(/(\d+)\s*years?/i);
  if (m) return parseInt(m[1], 10);
  const yearMatch = t.match(/(\d{4})/);
  if (yearMatch) {
    const targetYear = parseInt(yearMatch[1], 10);
    const now = new Date();
    const years = targetYear - now.getFullYear();
    return years > 0 && years <= 80 ? years : null;
  }
  return null;
}

/**
 * Years to retirement for backtesting: use only the timeline fields that apply to the
 * current retirement status (hidden fields may still hold stale text — do not OR blindly).
 */
export function inferHorizonYears({
  planningFor = "self",
  retirementStatus = "both_working",
  retirementTimelineSelf = "",
  retirementTimelinePartner = "",
}) {
  const selfY = parseHorizonYears(retirementTimelineSelf);
  const partnerY = parseHorizonYears(retirementTimelinePartner);
  if (retirementStatus === "both_retired") return 0;

  if (planningFor === "self") {
    if (retirementStatus === "self_retired") return 0;
    return selfY ?? null;
  }

  if (retirementStatus === "self_retired") return partnerY ?? null;
  if (retirementStatus === "partner_retired") return selfY ?? null;
  if (retirementStatus === "both_working") {
    if (selfY != null && partnerY != null) return Math.max(selfY, partnerY);
    return selfY ?? partnerY ?? null;
  }
  return selfY ?? partnerY ?? null;
}

const WORD_YEARS = { a: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };

/** Match backend intake_parser.spending_field_declares_one_time_outflows — timeline markers only for real big-spending text. */
export function spendingFieldDeclaresOneTimeOutflows(text) {
  if (text == null || typeof text !== 'string') return false;
  const s = text.trim();
  if (!s) return false;
  const low = s.toLowerCase();
  if (['none', 'not specified', 'n/a', 'na', '-'].includes(low)) return false;
  if (low.includes('no big spending expected')) return false;
  if (low.startsWith('retirement status:')) return false;
  if (low.includes('big spending:') && low.includes('current investment value:')) return false;
  if (low.includes('monthly investment contributions:') && low.includes('big spending:')) return false;
  return true;
}

export function parseExpenses(spendingText) {
  const expenses = [];
  // "1 M in house in 3 years" — purpose between the two "in"s (simple /\d+ years/ misses these)
  const rePurposeYears =
    /([\d,.]+(?:\s*[KMB])?)\s+in\s+([A-Za-z0-9][A-Za-z0-9\s,.'’_]{0,80}?)\s+in\s+(\d+)\s*years?/gi;
  let m;
  while ((m = rePurposeYears.exec(spendingText)) !== null) {
    const amount = parseAmount(m[1]);
    const years = parseInt(m[3], 10);
    const purpose = (m[2] || '').trim();
    if (amount > 0 && years >= 0) {
      const row = { years, amount };
      if (purpose) row.label = purpose;
      expenses.push(row);
    }
  }
  const rePurposeWordYear =
    /([\d,.]+(?:\s*[KMB])?)\s+in\s+([A-Za-z0-9][A-Za-z0-9\s,.'’_]{0,80}?)\s+in\s+(a|one|two|three|four|five|six|seven|eight|nine|ten)\s*years?/gi;
  while ((m = rePurposeWordYear.exec(spendingText)) !== null) {
    const amount = parseAmount(m[1]);
    const years = WORD_YEARS[m[3].toLowerCase()];
    const purpose = (m[2] || '').trim();
    if (amount > 0 && years != null) {
      const row = { years, amount };
      if (purpose) row.label = purpose;
      expenses.push(row);
    }
  }
  // "500K in 3 years" / "500K in 3 years for house" (backend expense_dicts_to_spending_line)
  const reYears = /([\d,.]+(?:\s*[KMB])?)\s+in\s+(\d+)\s*years?(?:\s+for\s+([^,\n]+))?/gi;
  while ((m = reYears.exec(spendingText)) !== null) {
    const amount = parseAmount(m[1]);
    const years = parseInt(m[2], 10);
    const purpose = (m[3] || '').trim();
    if (amount > 0 && years >= 0) {
      const row = { years, amount };
      if (purpose) row.label = purpose;
      expenses.push(row);
    }
  }
  const reWordYear =
    /([\d,.]+(?:\s*[KMB])?)\s+in\s+(a|one|two|three|four|five|six|seven|eight|nine|ten)\s*years?(?:\s+for\s+([^,\n]+))?/gi;
  while ((m = reWordYear.exec(spendingText)) !== null) {
    const amount = parseAmount(m[1]);
    const years = WORD_YEARS[m[2].toLowerCase()];
    const purpose = (m[3] || '').trim();
    if (amount > 0 && years != null) {
      const row = { years, amount };
      if (purpose) row.label = purpose;
      expenses.push(row);
    }
  }
  // "500K in 2027" / "500K in 2027 for house" -> calendar year (chart maps via start_year)
  const reYear = /([\d,.]+(?:\s*[KMB])?)\s+in\s+(\d{4})\b(?:\s+for\s+([^,\n]+))?/gi;
  while ((m = reYear.exec(spendingText)) !== null) {
    const amount = parseAmount(m[1]);
    const year = parseInt(m[2], 10);
    const purpose = (m[3] || '').trim();
    if (amount > 0 && year >= 2020 && year <= 2100) {
      const row = { years: year, amount };
      if (purpose) row.label = purpose;
      expenses.push(row);
    }
  }
  return expenses;
}

/**
 * Intake form rows ($, years, label) → merged into `spending` text on the API payload (server parses).
 * Middle field: years from now, or 4-digit calendar year (2020–2100), same as free-form parsing.
 */
export function expensesFromBigSpendingRows(rows) {
  const out = [];
  for (const r of rows || []) {
    const amount = parseAmount(String(r?.amount ?? "").trim());
    const yearsRaw = String(r?.years ?? "").trim();
    if (!amount || amount <= 0 || !yearsRaw) continue;
    let years;
    if (/^\d{4}$/.test(yearsRaw)) {
      const cy = parseInt(yearsRaw, 10);
      if (cy >= 2020 && cy <= 2100) years = cy;
    }
    if (years === undefined) {
      const y = parseInt(yearsRaw, 10);
      if (!Number.isFinite(y) || y < 0 || y > 150) continue;
      years = y;
    }
    const label = String(r?.label ?? "").trim();
    const o = { years, amount };
    if (label) o.label = label;
    out.push(o);
  }
  return out;
}

/** Summary line for chat / optional `spending` field on the API. */
export function bigSpendingNarrativeFromRows(rows) {
  const parts = [];
  for (const r of rows || []) {
    const amt = String(r?.amount ?? "").trim();
    const yRaw = String(r?.years ?? "").trim();
    const label = String(r?.label ?? "").trim();
    if (!amt || !yRaw) continue;
    const lbl = label ? ` for ${label}` : "";
    const isCal =
      /^\d{4}$/.test(yRaw) &&
      (() => {
        const n = parseInt(yRaw, 10);
        return n >= 2020 && n <= 2100;
      })();
    parts.push(isCal ? `${amt} in ${yRaw}${lbl}` : `${amt} in ${yRaw} years${lbl}`);
  }
  return parts.length ? parts.join(", ") : "";
}
