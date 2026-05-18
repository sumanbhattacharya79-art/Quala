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

/** @param {'default' | 'compact' | 'sidebar'} size */
function dialLayout(size) {
  if (size === "sidebar") {
    return { R: 20, cx: 30, cy: 24, sw: 4, w: 60, h: 38, vbW: 60, vbH: 38, pctFont: 11, labelFont: 7, pctMarginTop: -4, labelMarginTop: 1 };
  }
  if (size === "compact") {
    return { R: 30, cx: 45, cy: 36, sw: 6, w: 90, h: 58, vbW: 90, vbH: 58, pctFont: 15, labelFont: 8, pctMarginTop: -6, labelMarginTop: 2 };
  }
  return { R: 48, cx: 72, cy: 56, sw: 10, w: 144, h: 78, vbW: 144, vbH: 78, pctFont: 22, labelFont: 11, pctMarginTop: -10, labelMarginTop: 4 };
}

function resolveDialSize({ size, compact }) {
  if (size) return size;
  return compact ? "compact" : "default";
}

function SemiDial({ valuePct, label, accent, valueColor, emptyLabel, compact, size }) {
  const pct = valuePct == null || !Number.isFinite(valuePct) ? null : Math.max(0, Math.min(100, valuePct));
  const dialSize = resolveDialSize({ size, compact });
  const { R, cx, cy, sw, w, h, vbW, vbH, pctFont, labelFont, pctMarginTop, labelMarginTop } = dialLayout(dialSize);
  const arcLen = Math.PI * R;
  const dash = pct == null ? 0 : (pct / 100) * arcLen;

  return (
    <div style={{ width: w, maxWidth: "100%", textAlign: "center", flex: dialSize === "sidebar" ? "1 1 0" : "0 0 auto", minWidth: 0 }}>
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
          marginTop: pctMarginTop,
          fontSize: pctFont,
          fontWeight: 700,
          color: pct == null ? "var(--text-muted)" : valueColor || "var(--text)",
          fontFamily: "'DM Mono', ui-monospace, monospace",
          lineHeight: 1.1,
        }}
      >
        {pct == null ? emptyLabel || "—" : `${pct.toFixed(0)}%`}
      </div>
      <div
        style={{
          fontSize: labelFont,
          fontWeight: 600,
          color: "#c8a96e",
          letterSpacing: dialSize === "default" ? "0.06em" : "0.03em",
          textTransform: "uppercase",
          marginTop: labelMarginTop,
          lineHeight: 1.2,
          padding: dialSize === "default" ? 0 : "0 1px",
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
  size,
  className,
}) {
  const { arc, value } = accentPairFromRetirementSuccess(retirementSuccessPercent);
  const dialSize = resolveDialSize({ size, compact });
  if (kind === "goal") {
    return (
      <div className={className}>
      <SemiDial
        valuePct={goalFundedPercent}
        label={goalLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        size={dialSize}
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
      size={dialSize}
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
  size,
  showTopBorder = true,
  className,
}) {
  const { arc, value } = accentPairFromRetirementSuccess(retirementSuccessPercent);
  const dialSize = resolveDialSize({ size, compact });
  const isSidebar = dialSize === "sidebar";
  const rowClass = [className, isSidebar ? "life-planner-dials--sidebar" : null].filter(Boolean).join(" ");
  return (
    <div
      className={rowClass || undefined}
      style={{
        display: "flex",
        flexWrap: isSidebar ? "nowrap" : "wrap",
        gap: isSidebar ? 4 : compact ? 6 : 28,
        justifyContent: isSidebar ? "space-between" : "center",
        alignItems: "flex-start",
        marginTop: isSidebar || compact ? 0 : 14,
        paddingTop: isSidebar || compact ? 0 : 14,
        borderTop: showTopBorder ? "1px solid var(--border-soft)" : "none",
        maxWidth: isSidebar || compact ? "100%" : "none",
        width: isSidebar || compact ? "100%" : undefined,
        boxSizing: "border-box",
      }}
    >
      <SemiDial
        valuePct={goalFundedPercent}
        label={goalLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        size={dialSize}
      />
      <SemiDial
        valuePct={retirementSuccessPercent}
        label={retirementLabel}
        accent={arc}
        valueColor={value}
        emptyLabel="—"
        size={dialSize}
      />
    </div>
  );
}
