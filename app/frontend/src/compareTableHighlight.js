/** Highlight differing rows between two chart mount roots (tables rendered by charts.js). */

const DIFF_CLASS = "compare-diff-row";

function normText(s) {
  return String(s || "")
    .replace(/\s+/g, " ")
    .trim();
}

function firstTdKey(tr, rowIdx) {
  const td = tr.querySelector("td");
  if (!td) return `__no_td_${rowIdx}`;
  const t = normText(td.textContent);
  return t || `__blank_${rowIdx}`;
}

function clearHighlights(root) {
  if (!root) return;
  root.querySelectorAll(`tr.${DIFF_CLASS}`).forEach((tr) => tr.classList.remove(DIFF_CLASS));
}

function highlightRows(lrows, rrows) {
  const rByKey = new Map();
  rrows.forEach((tr, idx) => {
    const k = firstTdKey(tr, idx);
    if (!rByKey.has(k)) rByKey.set(k, []);
    rByKey.get(k).push(tr);
  });
  const matchedR = new Set();
  lrows.forEach((ltr, lidx) => {
    const k = firstTdKey(ltr, lidx);
    const queue = rByKey.get(k);
    const match = queue && queue.length ? queue.shift() : null;
    if (!match) {
      ltr.classList.add(DIFF_CLASS);
      return;
    }
    matchedR.add(match);
    if (normText(ltr.textContent) !== normText(match.textContent)) {
      ltr.classList.add(DIFF_CLASS);
      match.classList.add(DIFF_CLASS);
    }
  });
  rrows.forEach((rtr) => {
    if (!matchedR.has(rtr)) rtr.classList.add(DIFF_CLASS);
  });
}

/**
 * @param {HTMLElement | null} leftRoot
 * @param {HTMLElement | null} rightRoot
 */
export function applyCompareTableHighlights(leftRoot, rightRoot) {
  clearHighlights(leftRoot);
  clearHighlights(rightRoot);
  if (!leftRoot || !rightRoot) return;
  const leftTables = [...leftRoot.querySelectorAll("table.metrics-table")];
  const rightTables = [...rightRoot.querySelectorAll("table.metrics-table")];
  const n = Math.max(leftTables.length, rightTables.length);
  for (let i = 0; i < n; i++) {
    const LT = leftTables[i];
    const RT = rightTables[i];
    if (!LT || !RT) {
      if (LT) LT.querySelectorAll("tbody tr").forEach((tr) => tr.classList.add(DIFF_CLASS));
      if (RT) RT.querySelectorAll("tbody tr").forEach((tr) => tr.classList.add(DIFF_CLASS));
      continue;
    }
    const lrows = [...LT.querySelectorAll("tbody tr")];
    const rrows = [...RT.querySelectorAll("tbody tr")];
    highlightRows(lrows, rrows);
  }
}
