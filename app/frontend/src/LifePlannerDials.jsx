/**
 * Two semi-circular gauges for Life planner: goal funded % and retirement success %.
 */

/** @param {number | null | undefined} retirementPct */
export function accentPairFromRetirementSuccess(retirementPct) {
  if (retirementPct == null || !Number.isFinite(retirementPct)) {
    return { arc: "rgba(148, 163, 184, 0.55)", value: "var(--text)" };
  }
  const p = Math.max(0, Math.min(100, retirementPct));
  if (p > 80) {
    return { arc: "rgba(34, 197, 94, 0.95)", value: "rgb(34, 197, 94)" };
  }
  if (p >= 60) {
    return { arc: "rgba(234, 179, 8, 0.95)", value: "rgb(202, 138, 4)" };
  }
  return { arc: "rgba(239, 68, 68, 0.95)", value: "rgb(248, 113, 113)" };
}

function SemiDial({ valuePct, label, accent, valueColor, emptyLabel, compact }) {
  const pct = valuePct == null || !Number.isFinite(valuePct) ? null : Math.max(0, Math.min(100, valuePct));
  const R = compact ? 30 : 48;
  const cx = compact ? 45 : 72;
  const cy = compact ? 36 : 56;
  const sw = compact ? 6 : 10;
  const w = compact ? 90 : 144;
  const h = compact ? 58 : 78;
  const vbW = compact ? 90 : 144;
  const vbH = compact ? 58 : 78;
  const arcLen = Math.PI * R;
  const dash = pct == null ? 0 : (pct / 100) * arcLen;

  return (
    <div style={{ width: w, textAlign: "center", flex: "0 0 auto" }}>
      <svg width={w} height={h} viewBox={`0 0 ${vbW} ${vbH}`} aria-hidden>
        <path
          d={`M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}`}
          fill="none"
          stroke="var(--border-soft, #333)"
          strokeWidth={sw}
          strokeLinecap="round"
        />
        {pct != null ? (
          <path
            d={`M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}`}
            fill="none"
            stroke={accent}
            strokeWidth={sw}
            strokeLinecap="round"
            strokeDasharray={`${dash} ${arcLen}`}
          />
        ) : null}
      </svg>
      <div
        style={{
          marginTop: compact ? -6 : -10,
          fontSize: compact ? 15 : 22,
          fontWeight: 700,
          color: pct == null ? "var(--text-muted)" : valueColor || "var(--text)",
          fontFamily: "'DM Mono', ui-monospace, monospace",
        }}
      >
        {pct == null ? emptyLabel || "—" : `${pct.toFixed(0)}%`}
      </div>
      <div
        style={{
          fontSize: compact ? 8 : 11,
          fontWeight: 600,
          color: "#c8a96e",
          letterSpacing: compact ? "0.04em" : "0.06em",
          textTransform: "uppercase",
          marginTop: compact ? 2 : 4,
          lineHeight: compact ? 1.2 : 1.35,
          padding: compact ? "0 2px" : 0,
        }}
      >
        {label}
      </div>
    </div>
  );
}

/**
 * @param {{
 *   goalFundedPercent: number | null,
 *   retirementSuccessPercent: number | null,
 *   goalLabel?: string,
 *   retirementLabel?: string,
 *   compact?: boolean,
 *   showTopBorder?: boolean,
 *   className?: string,
 * }} props
 */
/** Single gauge for per-chart social sharing. */
export function LifePlannerSingleDial({
  kind,
  goalFundedPercent,
  retirementSuccessPercent,
  goalLabel = "Funded",
  retirementLabel = "Success",
  compact = false,
  className,
}) {
  const { arc, value } = accentPairFromRetirementSuccess(retirementSuccessPercent);
  if (kind === "goal") {
    return (
      <div className={className}>
      <SemiDial
        valuePct={goalFundedPercent}
        label={goalLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        compact={compact}
      />
      </div>
    );
  }
  return (
    <div className={className}>
      <SemiDial
      valuePct={retirementSuccessPercent}
      label={retirementLabel}
      accent={arc}
      valueColor={value}
      emptyLabel="—"
      compact={compact}
    />
    </div>
  );
}

export function LifePlannerDials({
  goalFundedPercent,
  retirementSuccessPercent,
  goalLabel = "Funded",
  retirementLabel = "Success",
  compact = false,
  showTopBorder = true,
  className,
}) {
  const { arc, value } = accentPairFromRetirementSuccess(retirementSuccessPercent);
  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexWrap: compact ? "nowrap" : "wrap",
        gap: compact ? 6 : 28,
        justifyContent: compact ? "flex-start" : "center",
        alignItems: "flex-start",
        marginTop: compact ? 0 : 14,
        paddingTop: compact ? 0 : 14,
        borderTop: showTopBorder ? "1px solid var(--border-soft)" : "none",
        maxWidth: compact ? 200 : "none",
        boxSizing: "border-box",
      }}
    >
      <SemiDial
        valuePct={goalFundedPercent}
        label={goalLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        compact={compact}
      />
      <SemiDial
        valuePct={retirementSuccessPercent}
        label={retirementLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        compact={compact}
      />
    </div>
  );
}
