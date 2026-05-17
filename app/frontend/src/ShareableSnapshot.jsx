import { useCallback, useRef, useState } from "react";
import { copyElementToClipboard } from "./copyChartImage.js";

/**
 * Wraps content (gauges, etc.) with a one-click copy-to-clipboard control for social sharing.
 */
export function ShareableSnapshot({
  title,
  subtitle,
  filename,
  children,
  disabled = false,
  className = "",
}) {
  const rootRef = useRef(null);
  const [status, setStatus] = useState("");

  const onCopy = useCallback(async () => {
    if (!rootRef.current || disabled) return;
    setStatus("");
    try {
      const result = await copyElementToClipboard(rootRef.current, {
        filename: filename || "quala-life-planner.png",
      });
      setStatus(
        result.method === "clipboard"
          ? "Copied — paste in Reddit or social"
          : "Image saved — upload from downloads",
      );
    } catch (err) {
      console.warn(err);
      setStatus("Copy failed — try again");
    }
    window.setTimeout(() => setStatus(""), 4000);
  }, [disabled, filename]);

  return (
    <div className={`share-snapshot ${className}`.trim()}>
      <div className="share-snapshot__toolbar">
        <button
          type="button"
          className="share-copy-btn share-copy-btn--toolbar"
          onClick={onCopy}
          disabled={disabled}
          aria-label={title ? `Copy ${title} as image` : "Copy as image"}
        >
          Copy image
        </button>
        {status ? (
          <span className="share-snapshot__status" role="status">
            {status}
          </span>
        ) : null}
      </div>
      <div ref={rootRef} className="share-snapshot__capture">
        {subtitle ? <div className="share-snapshot__subtitle">{subtitle}</div> : null}
        {children}
        <div className="share-snapshot__brand" aria-hidden>
          Quala.ai · hypothetical scenario, not financial advice
        </div>
      </div>
    </div>
  );
}
