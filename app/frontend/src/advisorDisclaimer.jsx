export const ADVISOR_MODEL_OUTPUT_DISCLAIMER_TEXT =
  "Note: Hypothetical AI simulation. Not advice; verify all math before acting.";

export const ADVISOR_MODEL_OUTPUT_DISCLAIMER_MR_BROWN_TEXT =
  "Note: Hypothetical AI simulation. Not advice; verify all math, prices, and tax impact before trading.";

export function AdvisorModelOutputDisclaimer({ className = "", variant = "default" }) {
  const text =
    variant === "mrBrown" ? ADVISOR_MODEL_OUTPUT_DISCLAIMER_MR_BROWN_TEXT : ADVISOR_MODEL_OUTPUT_DISCLAIMER_TEXT;
  return (
    <div
      className={`advisor-model-output-disclaimer${className ? ` ${className}` : ""}`}
      role="note"
    >
      <span className="advisor-model-output-disclaimer__icon" aria-hidden>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4M12 8h.01" strokeLinecap="round" />
        </svg>
      </span>
      <span className="advisor-model-output-disclaimer__text">{text}</span>
    </div>
  );
}
