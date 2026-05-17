import { useEffect, useMemo, useRef, useState } from "react";
import { useMobileViewport } from "./useMobileViewport.js";
import { renderComparePairedCharts } from "./charts.js";
import {
  compareBacktestArtifactsReady,
  computeGoalFundedPercent,
  extractGrowthTerminalValueP50,
  extractRetirementProbabilityOfSuccess,
} from "./compareGrowthRetireBridge.js";
import { LifePlannerDials } from "./LifePlannerDials.jsx";
import { BigSpendingUpcomingEditor } from "./BigSpendingUpcomingEditor.jsx";
import { ComparePortfolioColumnHeader } from "./ComparePortfolioColumnHeader.jsx";
import { MrBrownChat } from "./MrBrownChat.jsx";
import { AdvisorModelOutputDisclaimer } from "./advisorDisclaimer.jsx";

function ingestInputStyle(w) {
  return {
    width: w,
    padding: 6,
    fontSize: 13,
    background: "#111",
    border: "1px solid #2a2a2a",
    borderRadius: 4,
    color: "var(--text)",
  };
}

/** Shared profile + spending (used for both columns; retirement omits accumulation savings). */
function CompareSharedProfileFields({ form, setForm, variant, readOnly }) {
  const u = (fn) => setForm((s) => fn({ ...s }));
  const initialLabel =
    variant === "retirement" ? "Initial portfolio at retirement" : "Initial investment amount";
  return (
    <>
      <label style={{ cursor: "pointer" }}>
        Who are you planning for?
        <div className="retirement-status-row">
          {[
            { value: "self", label: "Just me" },
            { value: "couple", label: "The two of us" },
          ].map((opt) => (
            <button
              key={opt.value}
              type="button"
              disabled={readOnly}
              className={`choice-btn retirement-status-btn ${form.planningFor === opt.value ? "selected" : ""}`}
              onClick={() => u((s) => ({ ...s, planningFor: opt.value }))}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </label>
      <label className="birth-dates-row-label">Birth dates (year and month)</label>
      <div className="birth-dates-row">
        <div className="birth-date-group">
          <span className="birth-date-label">Your</span>
          <div className="birth-date-row">
            <input readOnly={readOnly} type="number" placeholder="Year" min="1920" max="2010" value={form.birthYear1} onChange={(e) => u((s) => ({ ...s, birthYear1: e.target.value }))} />
            <input readOnly={readOnly} type="number" placeholder="Month" min="1" max="12" value={form.birthMonth1} onChange={(e) => u((s) => ({ ...s, birthMonth1: e.target.value }))} />
          </div>
        </div>
        {form.planningFor === "couple" && (
          <div className="birth-date-group">
            <span className="birth-date-label">Partner</span>
            <div className="birth-date-row">
              <input readOnly={readOnly} type="number" placeholder="Year" min="1920" max="2010" value={form.birthYear2} onChange={(e) => u((s) => ({ ...s, birthYear2: e.target.value }))} />
              <input readOnly={readOnly} type="number" placeholder="Month" min="1" max="12" value={form.birthMonth2} onChange={(e) => u((s) => ({ ...s, birthMonth2: e.target.value }))} />
            </div>
          </div>
        )}
      </div>
      <label>
        Retirement status
        <div className="retirement-status-row">
          {form.planningFor === "self"
            ? [
                { value: "self_retired", label: "I am retired" },
                { value: "both_working", label: "I am working" },
              ].map((opt) => (
                <button key={opt.value} type="button" disabled={readOnly} className={`choice-btn retirement-status-btn ${form.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => u((s) => ({ ...s, retirementStatus: opt.value }))}>
                  {opt.label}
                </button>
              ))
            : [
                { value: "self_retired", label: "I am retired" },
                { value: "partner_retired", label: "Partner retired" },
                { value: "both_retired", label: "Both retired" },
                { value: "both_working", label: "Both working" },
              ].map((opt) => (
                <button key={opt.value} type="button" disabled={readOnly} className={`choice-btn retirement-status-btn ${form.retirementStatus === opt.value ? "selected" : ""}`} onClick={() => u((s) => ({ ...s, retirementStatus: opt.value }))}>
                  {opt.label}
                </button>
              ))}
        </div>
      </label>
      {((form.planningFor === "self" && form.retirementStatus === "both_working") ||
        (form.planningFor === "couple" && ["partner_retired", "both_working"].includes(form.retirementStatus)) ||
        (form.planningFor === "couple" && ["self_retired", "both_working"].includes(form.retirementStatus))) && (
        <label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            {((form.planningFor === "self" && form.retirementStatus === "both_working") ||
              (form.planningFor === "couple" && ["partner_retired", "both_working"].includes(form.retirementStatus))) && (
              <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                I plan to retire in
                <input readOnly={readOnly} type="text" placeholder="e.g. 10 years" value={form.retirementTimelineSelf} onChange={(e) => u((s) => ({ ...s, retirementTimelineSelf: e.target.value }))} />
              </span>
            )}
            {form.planningFor === "couple" && ["self_retired", "both_working"].includes(form.retirementStatus) && (
              <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
                Partner plans to retire in
                <input readOnly={readOnly} type="text" placeholder="e.g. 8 years" value={form.retirementTimelinePartner} onChange={(e) => u((s) => ({ ...s, retirementTimelinePartner: e.target.value }))} />
              </span>
            )}
          </div>
        </label>
      )}
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label>
          Country & state
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>USA</span>
            <input readOnly={readOnly} type="text" placeholder="State" value={form.state} onChange={(e) => u((s) => ({ ...s, state: e.target.value }))} style={{ width: 80, maxWidth: 80 }} />
          </div>
        </label>
        <label>
          Inflation rate (%)
          <input readOnly={readOnly} type="text" placeholder="3" value={form.inflationAssumption} onChange={(e) => u((s) => ({ ...s, inflationAssumption: e.target.value }))} style={{ width: 60 }} />
        </label>
      </div>
      <label>
        Risk appetite
        <input readOnly={readOnly} type="text" placeholder="e.g. medium risk" value={form.risk} onChange={(e) => u((s) => ({ ...s, risk: e.target.value }))} />
      </label>
      <BigSpendingUpcomingEditor disabled={readOnly} rows={form.bigSpendingRows} onRowsChange={(next) => u((s) => ({ ...s, bigSpendingRows: next, spending: "" }))} />
      <label>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
            {initialLabel}
            <span style={{ display: "flex", alignItems: "center", width: "100%" }}>
              <span style={{ color: "var(--text-muted)", marginRight: 4 }}>$</span>
              <input readOnly={readOnly} type="text" placeholder="e.g. 1M" value={form.investmentValue} onChange={(e) => u((s) => ({ ...s, investmentValue: e.target.value }))} style={{ flex: 1, width: "100%" }} />
            </span>
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            {variant === "growth" ? (
              <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
                Monthly savings
                <span style={{ display: "flex", alignItems: "center" }}>
                  <span style={{ color: "var(--text-muted)", marginRight: 4 }}>$</span>
                  <input readOnly={readOnly} type="text" placeholder="e.g. 500" value={form.monthlyContribution} onChange={(e) => u((s) => ({ ...s, monthlyContribution: e.target.value }))} style={{ flex: 1 }} />
                </span>
              </span>
            ) : null}
            <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 120 }}>
              Monthly expense
              <span style={{ display: "flex", alignItems: "center" }}>
                <span style={{ color: "var(--text-muted)", marginRight: 4 }}>$</span>
                <input readOnly={readOnly} type="text" placeholder="e.g. 3000" value={form.monthlyExpense} onChange={(e) => u((s) => ({ ...s, monthlyExpense: e.target.value }))} style={{ flex: 1 }} />
              </span>
            </span>
          </div>
        </div>
      </label>
    </>
  );
}

/** Growth accumulation intake (view-only in Life planner; change via drag/drop). */
function CompareGrowthIntakeColumn({ form, setForm, hints, readOnly, intakeFrozen }) {
  const u = (fn) => setForm((s) => fn({ ...s }));
  return (
    <div className="form-panel portfolio-view-panel compare-linked-column" style={{ marginBottom: 0, maxWidth: "none", flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>
        Growth intake
      </div>
      {!intakeFrozen ? (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12, lineHeight: 1.5 }}>
          Loaded from your dropped growth portfolio or scenario — not editable here. Use <strong style={{ color: "var(--text)" }}>Clear</strong> or pick another item to change assumptions, then{" "}
          <strong style={{ color: "var(--text)" }}>Continue</strong> to run backtests.
        </p>
      ) : (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12, lineHeight: 1.5 }}>
          View only — saved life plan intake.
        </p>
      )}
      <fieldset disabled={readOnly} style={{ border: "none", margin: 0, padding: 0, minWidth: 0 }}>
        <CompareSharedProfileFields form={form} setForm={setForm} variant="growth" readOnly={readOnly} />
        <div style={{ marginTop: 16, padding: 16, border: "1px solid var(--border)", borderRadius: 6, background: "var(--surface-elevated)" }}>
        <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>Growth what-if</div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>Extra monthly income to invest</div>
          {(form.growthMonthlyIncomeRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
            <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
                <input
                  type="text"
                  placeholder="Amount"
                  value={row.monthly}
                  onChange={(e) =>
                    u((s) => ({
                      ...s,
                      growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, monthly: e.target.value } : r)),
                    }))
                  }
                  style={ingestInputStyle(80)}
                />
              </span>
              <input
                type="number"
                placeholder="Start age"
                value={row.startAge}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, startAge: e.target.value } : r)),
                  }))
                }
                style={ingestInputStyle(75)}
              />
              <input
                type="number"
                placeholder="End age"
                value={row.endAge}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, endAge: e.target.value } : r)),
                  }))
                }
                style={ingestInputStyle(75)}
              />
              <input
                type="text"
                inputMode="decimal"
                placeholder="YoY %"
                title="YoY %: real change (inflation rate already included); optional; negative allowed"
                value={row.yoyPct ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) =>
                      i === idx ? { ...r, yoyPct: e.target.value } : r,
                    ),
                  }))
                }
                style={ingestInputStyle(64)}
              />
              <input
                type="text"
                placeholder="Label"
                value={row.label ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMonthlyIncomeRows: (s.growthMonthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, label: e.target.value } : r)),
                  }))
                }
                style={{ ...ingestInputStyle(undefined), flex: 1, minWidth: 100 }}
              />
              {idx === (form.growthMonthlyIncomeRows || []).length - 1 ? (
                <button
                  type="button"
                  className="whatif-row-add-btn"
                  onClick={() =>
                    u((s) => ({
                      ...s,
                      growthMonthlyIncomeRows: [...(s.growthMonthlyIncomeRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                    }))
                  }
                >
                  +
                </button>
              ) : null}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>{hints.monthly}</div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>Misc monthly spending</div>
          {(form.growthMiscMonthlySpendingRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
            <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
                <input
                  type="text"
                  placeholder="Amount"
                  value={row.monthly}
                  onChange={(e) =>
                    u((s) => ({
                      ...s,
                      growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, monthly: e.target.value } : r)),
                    }))
                  }
                  style={ingestInputStyle(80)}
                />
              </span>
              <input
                type="number"
                placeholder="Start age"
                value={row.startAge}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, startAge: e.target.value } : r)),
                  }))
                }
                style={ingestInputStyle(75)}
              />
              <input
                type="number"
                placeholder="End age"
                value={row.endAge}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, endAge: e.target.value } : r)),
                  }))
                }
                style={ingestInputStyle(75)}
              />
              <input
                type="text"
                inputMode="decimal"
                placeholder="YoY %"
                title="YoY %: real change (inflation rate already included); optional; negative allowed"
                value={row.yoyPct ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) =>
                      i === idx ? { ...r, yoyPct: e.target.value } : r,
                    ),
                  }))
                }
                style={ingestInputStyle(64)}
              />
              <input
                type="text"
                placeholder="Label"
                value={row.label ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    growthMiscMonthlySpendingRows: (s.growthMiscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, label: e.target.value } : r)),
                  }))
                }
                style={{ ...ingestInputStyle(undefined), flex: 1, minWidth: 100 }}
              />
              {idx === (form.growthMiscMonthlySpendingRows || []).length - 1 ? (
                <button
                  type="button"
                  className="whatif-row-add-btn"
                  onClick={() =>
                    u((s) => ({
                      ...s,
                      growthMiscMonthlySpendingRows: [...(s.growthMiscMonthlySpendingRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }],
                    }))
                  }
                >
                  +
                </button>
              ) : null}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>{hints.monthly}</div>
        </div>
        <BigSpendingUpcomingEditor
          disabled={readOnly}
          title="One-time inflow (inheritance, bonus, sale proceeds)"
          hintText={hints.inflow}
          rows={form.growthOneTimeInflowRows}
          onRowsChange={(next) => u((s) => ({ ...s, growthOneTimeInflowRows: next }))}
        />
      </div>
      </fieldset>
    </div>
  );
}

/** Retirement decumulation intake (view-only in Life planner; change via drag/drop). */
function CompareRetirementIntakeColumn({ form, setForm, hints, readOnly, intakeFrozen }) {
  const u = (fn) => setForm((s) => fn({ ...s }));
  return (
    <div className="form-panel portfolio-view-panel compare-linked-column" style={{ marginBottom: 0, maxWidth: "none", flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>
        Retirement intake
      </div>
      {!intakeFrozen ? (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12, lineHeight: 1.5 }}>
          Loaded from your dropped retirement portfolio or scenario — not editable here. Each <strong style={{ color: "var(--text)" }}>Continue</strong> sets{" "}
          <strong style={{ color: "var(--text)" }}>Initial portfolio at retirement</strong> from the growth Monte Carlo <strong>P50</strong> at the horizon. Use <strong style={{ color: "var(--text)" }}>Clear</strong> or a new drop to change assumptions, then <strong style={{ color: "var(--text)" }}>Continue</strong>.
        </p>
      ) : (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12, lineHeight: 1.5 }}>
          View only — saved life plan intake.
        </p>
      )}
      <fieldset disabled={readOnly} style={{ border: "none", margin: 0, padding: 0, minWidth: 0 }}>
        <CompareSharedProfileFields form={form} setForm={setForm} variant="retirement" readOnly={readOnly} />
        <div style={{ marginTop: 16, padding: 16, border: "1px solid var(--border)", borderRadius: 6, background: "var(--surface-elevated)" }}>
        <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 12, letterSpacing: "0.06em", textTransform: "uppercase" }}>Retirement what-if</div>
        <label style={{ display: "block", marginBottom: 14 }}>
          <span style={{ fontSize: 13, color: "var(--text)" }}>Effective tax rate on withdrawals (%)</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder="0"
            value={form.retirementEffectiveTaxRate}
            onChange={(e) => u((s) => ({ ...s, retirementEffectiveTaxRate: e.target.value }))}
            style={{ display: "block", marginTop: 6, width: 88, padding: "8px 10px", fontSize: 13, background: "#111", border: "1px solid #2a2a2a", borderRadius: 4, color: "var(--text)" }}
          />
        </label>
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 6 }}>Discretionary spending (optional)</div>
          <p style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.45 }}>
            Extra monthly $ when prior-year total return ≥ hurdle; optional start/end calendar ages (both, or leave blank for any age).
          </p>
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
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
                <input
                  type="text"
                  value={form.retirementDiscretionaryMonthly ?? ""}
                  onChange={(e) => u((s) => ({ ...s, retirementDiscretionaryMonthly: e.target.value }))}
                  style={ingestInputStyle(88)}
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
                type="text"
                inputMode="decimal"
                placeholder="e.g. 5"
                value={form.retirementDiscretionaryMinPriorReturnPct ?? ""}
                onChange={(e) => u((s) => ({ ...s, retirementDiscretionaryMinPriorReturnPct: e.target.value }))}
                style={ingestInputStyle(72)}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Start age</span>
              <input
                type="number"
                min={0}
                max={120}
                placeholder="optional"
                value={form.retirementDiscretionaryStartAge ?? ""}
                onChange={(e) => u((s) => ({ ...s, retirementDiscretionaryStartAge: e.target.value }))}
                style={ingestInputStyle(88)}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 0 auto" }}>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>End age</span>
              <input
                type="number"
                min={0}
                max={120}
                placeholder="optional"
                value={form.retirementDiscretionaryEndAge ?? ""}
                onChange={(e) => u((s) => ({ ...s, retirementDiscretionaryEndAge: e.target.value }))}
                style={ingestInputStyle(88)}
              />
            </label>
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>Monthly income (SS, pension, etc.)</div>
          {(form.monthlyIncomeRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
            <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
                <input
                  type="text"
                  value={row.monthly}
                  onChange={(e) =>
                    u((s) => ({
                      ...s,
                      monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, monthly: e.target.value } : r)),
                    }))
                  }
                  style={ingestInputStyle(80)}
                />
              </span>
              <input type="number" placeholder="Start age" value={row.startAge} onChange={(e) => u((s) => ({ ...s, monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, startAge: e.target.value } : r)) }))} style={ingestInputStyle(75)} />
              <input type="number" placeholder="End age" value={row.endAge} onChange={(e) => u((s) => ({ ...s, monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, endAge: e.target.value } : r)) }))} style={ingestInputStyle(75)} />
              <input
                type="text"
                inputMode="decimal"
                placeholder="YoY %"
                title="YoY %: real change (inflation rate already included); optional; negative allowed"
                value={row.yoyPct ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, yoyPct: e.target.value } : r)),
                  }))
                }
                style={ingestInputStyle(64)}
              />
              <input type="text" placeholder="Label" value={row.label ?? ""} onChange={(e) => u((s) => ({ ...s, monthlyIncomeRows: (s.monthlyIncomeRows || []).map((r, i) => (i === idx ? { ...r, label: e.target.value } : r)) }))} style={{ ...ingestInputStyle(undefined), flex: 1, minWidth: 100 }} />
              {idx === (form.monthlyIncomeRows || []).length - 1 ? (
                <button type="button" className="whatif-row-add-btn" onClick={() => u((s) => ({ ...s, monthlyIncomeRows: [...(s.monthlyIncomeRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }] }))}>
                  +
                </button>
              ) : null}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>{hints.monthly}</div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8 }}>Misc monthly spending</div>
          {(form.miscMonthlySpendingRows || [{ monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }]).map((row, idx) => (
            <div key={idx} style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
                <input
                  type="text"
                  value={row.monthly}
                  onChange={(e) =>
                    u((s) => ({
                      ...s,
                      miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, monthly: e.target.value } : r)),
                    }))
                  }
                  style={ingestInputStyle(80)}
                />
              </span>
              <input type="number" placeholder="Start age" value={row.startAge} onChange={(e) => u((s) => ({ ...s, miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, startAge: e.target.value } : r)) }))} style={ingestInputStyle(75)} />
              <input type="number" placeholder="End age" value={row.endAge} onChange={(e) => u((s) => ({ ...s, miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, endAge: e.target.value } : r)) }))} style={ingestInputStyle(75)} />
              <input
                type="text"
                inputMode="decimal"
                placeholder="YoY %"
                title="YoY %: real change (inflation rate already included); optional; negative allowed"
                value={row.yoyPct ?? ""}
                onChange={(e) =>
                  u((s) => ({
                    ...s,
                    miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) =>
                      i === idx ? { ...r, yoyPct: e.target.value } : r,
                    ),
                  }))
                }
                style={ingestInputStyle(64)}
              />
              <input type="text" placeholder="Label" value={row.label ?? ""} onChange={(e) => u((s) => ({ ...s, miscMonthlySpendingRows: (s.miscMonthlySpendingRows || []).map((r, i) => (i === idx ? { ...r, label: e.target.value } : r)) }))} style={{ ...ingestInputStyle(undefined), flex: 1, minWidth: 100 }} />
              {idx === (form.miscMonthlySpendingRows || []).length - 1 ? (
                <button type="button" className="whatif-row-add-btn" onClick={() => u((s) => ({ ...s, miscMonthlySpendingRows: [...(s.miscMonthlySpendingRows || []), { monthly: "", startAge: "", endAge: "", yoyPct: "", label: "" }] }))}>
                  +
                </button>
              ) : null}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>{hints.monthly}</div>
        </div>
        <BigSpendingUpcomingEditor
          disabled={readOnly}
          title="One-time inflow (windfall)"
          hintText={hints.inflow}
          rows={form.windfallInflowRows}
          onRowsChange={(next) => u((s) => ({ ...s, windfallInflowRows: next }))}
        />
      </div>
      </fieldset>
    </div>
  );
}

/**
 * Compare: growth (left) + retirement (right). Intake is view-only — change assumptions only via drag/drop; saved plans use Delete only.
 */
export function CompareView({
  mrBrownUserId,
  onBack,
  compareNotice,
  compareLeftSel,
  compareRightSel,
  growthForm,
  setGrowthForm,
  retireForm,
  setRetireForm,
  compareHydrating,
  growthArtifacts,
  retireArtifacts,
  growthRunLoading,
  retireRunLoading,
  retireSyncMessage,
  handleCompareDrop,
  onClearSide,
  onLifePlannerContinue,
  intakeHints,
  connectLifeScenarioNameInput,
  setConnectLifeScenarioNameInput,
  onSavePairScenarios,
  connectPairScenarioSaving,
  connectPairScenarioError,
  connectPairScenarioSuccess,
  intakeFrozen,
  /** If >= 1, block saving a *new* life plan until the user deletes the existing one (server enforces the same). */
  existingSavedLifePlanCount = 0,
  showLifePlannerSavedBar,
  onLifePlannerDelete,
  frozenGrowthMedianAtRetirementUsd,
  currentGrowthPortfolioValueUsd,
  /** Re-render paired charts when theme toggles (D3 uses theme at draw time; CSS alone cannot fix inline styles). */
  theme = "dark",
}) {
  const pairedChartsRef = useRef(null);
  const isMobile = useMobileViewport();
  const [mobileColumnTab, setMobileColumnTab] = useState("growth");

  const leftArt = growthArtifacts;
  const rightArt = retireArtifacts;

  useEffect(() => {
    const el = pairedChartsRef.current;
    if (!el) return;
    const l = compareBacktestArtifactsReady(leftArt) ? leftArt : null;
    const r = compareBacktestArtifactsReady(rightArt) ? rightArt : null;
    if (!l && !r) {
      // Must use charts helper (not only innerHTML): renderComparePairedCharts bumps a gen so stale
      // async finish() from a prior run cannot re-append the right column after both sides are cleared.
      renderComparePairedCharts(null, null, el);
      return;
    }
    renderComparePairedCharts(l, r, el);
    return () => {
      renderComparePairedCharts(null, null, el);
    };
  }, [leftArt, rightArt, theme]);

  const showEditors = growthForm && retireForm && !compareHydrating;
  /** Life planner intake columns are never editable here — only drag/drop (and server merge on Continue). */
  const intakeFormsReadOnly = true;
  const bothChartsReady =
    compareBacktestArtifactsReady(growthArtifacts) && compareBacktestArtifactsReady(retireArtifacts);
  const saveNewLifePlanBlocked =
    !intakeFrozen && typeof existingSavedLifePlanCount === "number" && existingSavedLifePlanCount >= 1;
  const saveLifeScenarioDisabled =
    intakeFrozen || !bothChartsReady || !!connectPairScenarioSaving || saveNewLifePlanBlocked;

  const growthAtRetire = useMemo(() => extractGrowthTerminalValueP50(leftArt), [leftArt]);
  const retirementSuccessRate = useMemo(() => extractRetirementProbabilityOfSuccess(rightArt), [rightArt]);
  const goalFundedPercent = useMemo(
    () => computeGoalFundedPercent(frozenGrowthMedianAtRetirementUsd, currentGrowthPortfolioValueUsd),
    [frozenGrowthMedianAtRetirementUsd, currentGrowthPortfolioValueUsd],
  );
  const retirementSuccessPercentDial = useMemo(() => {
    const p = extractRetirementProbabilityOfSuccess(rightArt);
    if (p == null || !Number.isFinite(p)) return null;
    const n = Number(p);
    return Math.min(100, Math.max(0, n <= 1 ? n * 100 : n));
  }, [rightArt]);

  const fmtGoalUsd = (n) => {
    if (n == null || !Number.isFinite(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
    return `$${Math.round(n).toLocaleString()}`;
  };

  const fmtSuccessPct = (p01) => {
    if (p01 == null || !Number.isFinite(p01)) return null;
    return `${(p01 * 100).toFixed(1)}%`;
  };

  const showGoalPanel = compareLeftSel && compareRightSel;

  return (
    <div className="messages-area compare-messages-area" style={{ padding: "20px 28px", width: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10, flexWrap: "wrap", width: "100%" }}>
        <button type="button" className="toggle-btn" onClick={onBack} title="Back">
          ←
        </button>
        <span className="topbar-title">Life planner</span>
        {showLifePlannerSavedBar ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: "auto", flexWrap: "wrap" }}>
            <button type="button" className="choice-btn" onClick={onLifePlannerDelete}>
              Delete
            </button>
          </div>
        ) : null}
      </div>
      {intakeFrozen ? (
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 16px", lineHeight: 1.55, maxWidth: 720 }}>
          Saved life scenario — charts below reflect your last backtest. Use <strong style={{ color: "var(--text)" }}>Delete</strong> to remove this plan from Life planner.
        </p>
      ) : (
        <span className="compare-page-subhint" style={{ display: "block", marginBottom: 16 }}>
          Drag one <strong style={{ color: "#c8a96e" }}>growth</strong> item on the left and one <strong style={{ color: "#c8a96e" }}>retirement</strong> item on the right. Intake is <strong style={{ color: "#c8a96e" }}>not editable</strong> here — change assumptions only by clearing or dropping different items. Each <strong style={{ color: "#c8a96e" }}>Continue</strong> runs growth Monte Carlo, sets retirement <strong style={{ color: "#c8a96e" }}>initial portfolio at retirement</strong> from the growth median (P50) at the horizon, and runs the retirement backtest. Save once under <strong style={{ color: "#c8a96e" }}>Life scenario</strong> to store both sides under one name in the sidebar.
        </span>
      )}
      {showGoalPanel ? (
        <div
          className="life-planner-goal-panel"
          style={{
            marginBottom: 18,
            padding: "14px 18px",
            borderRadius: 8,
            border: "1px solid var(--border-soft)",
            background: "var(--surface-elevated)",
            boxSizing: "border-box",
          }}
        >
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text)", fontSize: 14, lineHeight: 1.55 }}>
            <li style={{ marginBottom: 10 }}>
              <strong>Portfolio value at retirement</strong> (growth portfolio, Monte Carlo median at your horizon):{" "}
              {growthAtRetire != null ? (
                <strong style={{ color: "var(--text)", fontFamily: "'DM Mono', ui-monospace, monospace" }}>{fmtGoalUsd(growthAtRetire)}</strong>
              ) : (
                <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  Use <strong style={{ color: "var(--text)" }}>Continue</strong> to run backtests and estimate.
                </span>
              )}
            </li>
            <li>
              <strong>Retirement lasting your lifespan</strong> (retirement portfolio Monte Carlo):{" "}
              {fmtSuccessPct(retirementSuccessRate) != null ? (
                <>
                  <strong style={{ color: "var(--text)", fontFamily: "'DM Mono', ui-monospace, monospace" }}>
                    {fmtSuccessPct(retirementSuccessRate)}
                  </strong>
                  <span style={{ color: "var(--text-muted)", fontSize: 13 }}> probability paths do not run out within the plan horizon.</span>
                </>
              ) : (
                <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  Use <strong style={{ color: "var(--text)" }}>Continue</strong> to see success rate after growth and retirement backtests.
                </span>
              )}
            </li>
          </ul>
          {showGoalPanel &&
          frozenGrowthMedianAtRetirementUsd != null &&
          Number.isFinite(Number(frozenGrowthMedianAtRetirementUsd)) &&
          Number(frozenGrowthMedianAtRetirementUsd) > 0 ? (
            <>
              <p
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  margin: "12px 0 0",
                  lineHeight: 1.45,
                  maxWidth: "100%",
                  whiteSpace: "nowrap",
                  overflowX: "auto",
                }}
              >
                <strong style={{ color: "var(--text)" }}>Goal funded</strong> uses your growth portfolio’s latest value and the median value at retirement from the growth Monte Carlo.
              </p>
              <LifePlannerDials
                goalFundedPercent={goalFundedPercent}
                retirementSuccessPercent={retirementSuccessPercentDial}
                goalLabel={intakeFrozen ? "Goal saved" : "Funded"}
                retirementLabel={intakeFrozen ? "Retirement success" : "Success"}
              />
            </>
          ) : null}
      </div>
      ) : null}
      {compareNotice ? (
        <div className="compare-notice-banner" role="status">
          {compareNotice}
        </div>
      ) : null}
      {retireSyncMessage && !intakeFrozen ? (
        <div
          style={{
            marginBottom: 14,
            padding: "10px 14px",
            borderRadius: 6,
            border: "1px solid #2d6a4f",
            background: "rgba(45, 106, 79, 0.15)",
            color: "var(--text)",
            fontSize: 13,
            lineHeight: 1.5,
          }}
          role="status"
        >
          {retireSyncMessage}
        </div>
      ) : null}
      {connectPairScenarioError ? (
        <div style={{ color: "#f87171", fontSize: 13, marginBottom: 14 }} role="alert">
          {connectPairScenarioError}
        </div>
      ) : null}
      {connectPairScenarioSuccess ? (
        <div style={{ color: "#4ade80", fontSize: 13, marginBottom: 14 }} role="status">
          {connectPairScenarioSuccess}
        </div>
      ) : null}
      <div className="compare-page-scroll">
        <div className="compare-page-inner">
          {intakeFrozen ? null : (
            <div className="compare-drop-zones-row">
              {["left", "right"].map((side) => {
                const isLeft = side === "left";
                const sel = isLeft ? compareLeftSel : compareRightSel;
                const label = isLeft ? "Growth" : "Retirement";
                return (
                  <div key={side} className="compare-drop-zone-col" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    <div
                      role="region"
                      aria-label={isLeft ? "Drop zone growth" : "Drop zone retirement"}
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
                      onDrop={(e) => handleCompareDrop(side, e)}
                      className="compare-drop-zone-panel"
                    >
                      {sel ? (
                        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, justifyContent: "space-between" }}>
                          <div>
                            <div className="compare-drop-zone-title">{sel.label}</div>
                            <div className="compare-drop-zone-meta">
                              {label}
                              {sel.source === "scenario" ? " · Scenario" : " · Portfolio"}
                            </div>
                          </div>
                          <button
                            type="button"
                            className="login-cancel-btn"
                            style={{ fontSize: 11, padding: "6px 12px" }}
                            onClick={() => onClearSide(side)}
                          >
                            Clear
                          </button>
                        </div>
                      ) : (
                        <div className="compare-drop-zone-placeholder">
                          Drop a <strong style={{ color: "#c8a96e" }}>{isLeft ? "growth" : "retirement"}</strong> portfolio or scenario here.
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {compareHydrating ? <div className="compare-drop-zone-loading">Loading saved intake…</div> : null}
          {showEditors ? (
            <>
              <div className="compare-mobile-tabs" role="tablist" aria-label="Growth or retirement intake">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mobileColumnTab === "growth"}
                  className={`compare-mobile-tab${mobileColumnTab === "growth" ? " selected" : ""}`}
                  onClick={() => setMobileColumnTab("growth")}
                >
                  Growth
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mobileColumnTab === "retirement"}
                  className={`compare-mobile-tab${mobileColumnTab === "retirement" ? " selected" : ""}`}
                  onClick={() => setMobileColumnTab("retirement")}
                >
                  Retirement
                </button>
              </div>
              <div className={`compare-editors-row${isMobile ? " compare-editors-row--tabbed" : ""}`}>
                <div
                  className={`compare-editor-col${
                    !isMobile || mobileColumnTab === "growth" ? " compare-editor-col--active" : ""
                  }`}
                >
                  <ComparePortfolioColumnHeader sel={compareLeftSel} roleLabel="Growth" frozenShortHeader={false} />
                  <CompareGrowthIntakeColumn
                    form={growthForm}
                    setForm={setGrowthForm}
                    hints={intakeHints}
                    readOnly={intakeFormsReadOnly}
                    intakeFrozen={intakeFrozen}
                  />
                </div>
                <div
                  className={`compare-editor-col${
                    !isMobile || mobileColumnTab === "retirement" ? " compare-editor-col--active" : ""
                  }`}
                >
                  <ComparePortfolioColumnHeader sel={compareRightSel} roleLabel="Retirement" frozenShortHeader={false} />
                  <CompareRetirementIntakeColumn
                    form={retireForm}
                    setForm={setRetireForm}
                    hints={intakeHints}
                    readOnly={intakeFormsReadOnly}
                    intakeFrozen={intakeFrozen}
                  />
                </div>
              </div>
              <div className="compare-panel-primary-actions">
                <button
                  type="button"
                  className="form-primary-btn"
                  disabled={
                    intakeFrozen ||
                    growthRunLoading ||
                    retireRunLoading ||
                    !compareLeftSel ||
                    !compareRightSel
                  }
                  onClick={onLifePlannerContinue}
                  title={!compareLeftSel || !compareRightSel ? "Select growth and retirement items above" : ""}
                >
                  {growthRunLoading
                    ? "Running growth backtest…"
                    : retireRunLoading
                      ? "Running retirement backtest…"
                      : "Continue"}
                </button>
              </div>
              <div
                className="form-panel portfolio-view-panel"
                style={{
                  marginTop: 20,
                  padding: 16,
                  maxWidth: "100%",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 8,
                  background: "var(--surface-elevated)",
                }}
              >
                <div style={{ fontSize: 11, color: "#c8a96e", marginBottom: 10, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                  Save life scenario
                </div>
                <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 12px", lineHeight: 1.45 }}>
                  One name saves both the <strong style={{ color: "var(--text)" }}>growth</strong> (left) and{" "}
                  <strong style={{ color: "var(--text)" }}>retirement</strong> (right) intakes. It appears under{" "}
                  <strong style={{ color: "var(--text)" }}>Life planner</strong> in the sidebar. You can keep{" "}
                  <strong style={{ color: "var(--text)" }}>only one</strong> saved life plan: delete the current one
                  there before saving a new plan.
                </p>
                {saveNewLifePlanBlocked ? (
                  <p
                    style={{
                      fontSize: 12,
                      color: "#c97a7a",
                      margin: "0 0 12px",
                      lineHeight: 1.45,
                      padding: "10px 12px",
                      background: "rgba(201, 122, 122, 0.12)",
                      borderRadius: 6,
                      border: "1px solid rgba(201, 122, 122, 0.35)",
                    }}
                  >
                    A life plan is already saved. Open it from <strong style={{ color: "var(--text)" }}>Life planner</strong>{" "}
                    in the sidebar, use <strong style={{ color: "var(--text)" }}>Delete</strong>, then return here and save.
                  </p>
                ) : null}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end", marginBottom: 12 }}>
                  <label style={{ flex: "1 1 240px", minWidth: 200 }}>
                    Life scenario name
                    <input
                      type="text"
                      readOnly={intakeFrozen}
                      value={connectLifeScenarioNameInput ?? ""}
                      onChange={(e) => setConnectLifeScenarioNameInput(e.target.value)}
                      placeholder="e.g. Base case 2035"
                      autoComplete="off"
                      style={{ ...ingestInputStyle("100%"), marginTop: 6, boxSizing: "border-box" }}
                    />
                  </label>
                  <button
                    type="button"
                    className="form-primary-btn"
                    disabled={saveLifeScenarioDisabled}
                    onClick={onSavePairScenarios}
                    title={
                      saveNewLifePlanBlocked
                        ? "Delete your saved life plan in the sidebar first"
                        : !bothChartsReady
                          ? "Run Continue so both charts finish first"
                          : ""
                    }
                  >
                    {connectPairScenarioSaving ? "Saving…" : "Save life scenario"}
                  </button>
                </div>
              </div>
            </>
          ) : null}
          <div
            ref={pairedChartsRef}
            className="compare-paired-mount charts-mount charts-mount--full"
            style={{ marginTop: 16 }}
          />
          {compareBacktestArtifactsReady(growthArtifacts) && compareBacktestArtifactsReady(retireArtifacts) ? (
            <AdvisorModelOutputDisclaimer className="page-output-disclaimer" />
          ) : null}
        </div>
      </div>
      {mrBrownUserId ? (
        <MrBrownChat
          userId={mrBrownUserId}
          page="life_plan"
          growthPortfolioId={compareLeftSel?.portfolioId ?? null}
          retirementPortfolioId={compareRightSel?.portfolioId ?? null}
        />
      ) : null}
    </div>
  );
}
