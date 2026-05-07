/** Structured timing + label: $ [amt] in [years] years for [label], with + / − row controls. */
export function BigSpendingUpcomingEditor({
  rows,
  disabled,
  onRowsChange,
  title = "Big spending coming up?",
  hintText = "",
}) {
  const list = Array.isArray(rows) && rows.length ? rows : [{ amount: "", years: "", label: "" }];
  const updateRow = (idx, partial) => {
    onRowsChange(list.map((r, i) => (i === idx ? { ...r, ...partial } : r)));
  };
  const removeRow = (idx) => {
    if (list.length <= 1) {
      onRowsChange([{ amount: "", years: "", label: "" }]);
      return;
    }
    onRowsChange(list.filter((_, i) => i !== idx));
  };
  const addRow = () => {
    onRowsChange([...list, { amount: "", years: "", label: "" }]);
  };
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 8, display: "block" }}>{title}</div>
      {list.map((row, idx) => (
        <div
          key={idx}
          style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>$</span>
            <input
              type="text"
              readOnly={disabled}
              placeholder="e.g. 1M"
              value={row.amount}
              onChange={(e) => updateRow(idx, { amount: e.target.value })}
              className="intake-inline-input"
              style={{ width: 88, padding: 6, fontSize: 13 }}
            />
          </span>
          <span style={{ color: "var(--text-muted)", fontSize: 13 }}>in</span>
          <input
            type="text"
            readOnly={disabled}
            placeholder="3 or 2029"
            value={row.years}
            onChange={(e) => updateRow(idx, { years: e.target.value })}
            className="intake-inline-input"
            style={{ width: 76, padding: 6, fontSize: 13 }}
          />
          <span style={{ color: "var(--text-muted)", fontSize: 13 }}>years for</span>
          <input
            type="text"
            readOnly={disabled}
            placeholder="e.g. house"
            value={row.label}
            onChange={(e) => updateRow(idx, { label: e.target.value })}
            className="intake-inline-input"
            style={{ flex: 1, minWidth: 100, padding: 6, fontSize: 13 }}
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => removeRow(idx)}
            title="Remove row"
            className="intake-row-cmd-btn"
          >
            −
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={disabled}
        onClick={addRow}
        title="Add row"
        className="intake-row-cmd-btn intake-row-cmd-btn--add"
      >
        +
      </button>
      {hintText ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>{hintText}</div>
      ) : null}
    </div>
  );
}
