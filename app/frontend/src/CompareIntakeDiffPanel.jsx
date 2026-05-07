import { useRef } from "react";
import { mergedIntakeDiffRows, prettyIntakeCellValue, prettyIntakeFieldPath } from "./compareIntake.js";

function FieldPathBreadcrumb({ rawPath }) {
  if (!rawPath) return null;
  const parts = prettyIntakeFieldPath(rawPath).split(" › ");
  return (
    <div className="compare-intake-path-breadcrumb" title={rawPath}>
      {parts.map((segment, i) => (
        <span key={`${rawPath}-${i}`} className="compare-intake-path-segment-wrap">
          {i > 0 ? <span className="compare-intake-path-sep" aria-hidden="true" /> : null}
          <span className={i === parts.length - 1 ? "compare-intake-path-leaf" : "compare-intake-path-part"}>{segment}</span>
        </span>
      ))}
    </div>
  );
}

function IntakeValueReadOnly({ raw, fieldPath }) {
  if (raw == null || raw === "—") {
    return <span className="compare-intake-value-empty">—</span>;
  }
  const text = prettyIntakeCellValue(raw, fieldPath);
  const isBlock = text.includes("\n") || text.length > 72;

  if (isBlock) {
    return <pre className="compare-intake-form-value-pre" tabIndex={0}>{text}</pre>;
  }
  return <div className="compare-intake-form-value-chip">{text}</div>;
}

function intakeRowBaseKey(path) {
  const i = path.indexOf(".");
  return i === -1 ? path : path.slice(0, i);
}

function IntakeFormColumn({ title, rows, side, scrollRef, onScroll }) {
  const blocks = [];
  let prevBase = null;
  for (const row of rows) {
    const base = intakeRowBaseKey(row.path);
    if (base !== prevBase) {
      // Section headers only for nested paths (e.g. birth_dates › …). Top-level keys like
      // planning_for already repeat the same text as the field label (PLANNING FOR / Planning for).
      if (row.path.includes(".")) {
        blocks.push(
          <div key={`section-${base}-${blocks.length}`} className="compare-intake-form-section-title">
            {prettyIntakeFieldPath(base)}
          </div>,
        );
      }
      prevBase = base;
    }
    const raw = side === "left" ? row.left : row.right;
    blocks.push(
      <div
        key={row.path}
        className={`compare-intake-form-field${row.diff ? " compare-intake-form-field--diff" : ""}`}
      >
        <div className="compare-intake-form-field-label">
          <FieldPathBreadcrumb rawPath={row.path} />
        </div>
        <div className="compare-intake-form-field-control">
          <IntakeValueReadOnly raw={raw} fieldPath={row.path} />
        </div>
      </div>,
    );
  }

  return (
    <div className="chart-card compare-intake-form-card">
      <h3 className="compare-intake-form-card-title">{title}</h3>
      <div ref={scrollRef} className="compare-intake-form-scroll" onScroll={onScroll}>
        {blocks}
      </div>
    </div>
  );
}

/**
 * Two intake snapshots as paired “forms” (same layout as compare chart rows: left | right).
 */
export function CompareIntakeDiffPanel({ labelLeft, labelRight, intakeLeft, intakeRight }) {
  const rows = mergedIntakeDiffRows(intakeLeft, intakeRight);
  const leftScrollRef = useRef(null);
  const rightScrollRef = useRef(null);
  const scrollSyncLock = useRef(false);

  const syncFromSource = (sourceEl, targetEl) => {
    if (!targetEl || scrollSyncLock.current) return;
    const st = sourceEl.scrollTop;
    const sl = sourceEl.scrollLeft;
    if (targetEl.scrollTop === st && targetEl.scrollLeft === sl) return;
    scrollSyncLock.current = true;
    targetEl.scrollTop = st;
    targetEl.scrollLeft = sl;
    requestAnimationFrame(() => {
      scrollSyncLock.current = false;
    });
  };

  const handleLeftScroll = (e) => syncFromSource(e.currentTarget, rightScrollRef.current);
  const handleRightScroll = (e) => syncFromSource(e.currentTarget, leftScrollRef.current);

  return (
    <div className="compare-intake-paired-wrap">
      <div className="compare-paired-charts">
        <div className="compare-chart-pair-row compare-intake-pair-row">
          <div className="compare-pair-cell compare-pair-cell--left">
            <IntakeFormColumn
              title={labelLeft || "Left"}
              rows={rows}
              side="left"
              scrollRef={leftScrollRef}
              onScroll={handleLeftScroll}
            />
          </div>
          <div className="compare-pair-cell compare-pair-cell--right">
            <IntakeFormColumn
              title={labelRight || "Right"}
              rows={rows}
              side="right"
              scrollRef={rightScrollRef}
              onScroll={handleRightScroll}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
