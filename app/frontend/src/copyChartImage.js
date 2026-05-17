/**
 * Copy a DOM subtree as PNG to the clipboard (desktop + mobile) with download fallback.
 */

import { applyShareCopyButtonChrome } from "./shareCopyIcon.js";

function exportBackgroundColor() {
  const theme = document.documentElement.getAttribute("data-theme");
  return theme === "light" ? "#fffcf7" : "#0a0a0a";
}

function slugifyFilename(title) {
  const s = String(title || "chart")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
  return s || "chart";
}

/**
 * @param {HTMLElement} element
 * @param {{ filename?: string, pixelRatio?: number, backgroundColor?: string }} [opts]
 * @returns {Promise<{ method: 'clipboard' | 'download' }>}
 */
export async function copyElementToClipboard(element, opts = {}) {
  if (!element || !(element instanceof HTMLElement)) {
    throw new Error("Nothing to copy");
  }
  const { toPng } = await import("html-to-image");
  const pixelRatio = opts.pixelRatio ?? Math.min(2, window.devicePixelRatio || 2);
  const backgroundColor = opts.backgroundColor ?? exportBackgroundColor();
  const filename = opts.filename ?? "quala-chart.png";

  const hiddenControls = [];
  element.querySelectorAll(".share-copy-btn").forEach((btn) => {
    if (!(btn instanceof HTMLElement)) return;
    hiddenControls.push(btn);
    btn.style.visibility = "hidden";
  });

  let dataUrl;
  try {
    dataUrl = await toPng(element, {
      pixelRatio,
      cacheBust: true,
      backgroundColor,
      skipFonts: false,
    });
  } finally {
    hiddenControls.forEach((btn) => {
      btn.style.visibility = "";
    });
  }

  const res = await fetch(dataUrl);
  const blob = await res.blob();

  if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      return { method: "clipboard" };
    } catch {
      /* fall through to download — common when permission denied on mobile */
    }
  }

  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  return { method: "download" };
}

/**
 * Add "Copy" buttons to chart cards rendered imperatively (life planner paired charts).
 * @param {HTMLElement | null} container
 * @param {{ enabled?: boolean }} [opts]
 * @returns {() => void} cleanup
 */
export function decorateLifePlannerChartsForSharing(container, opts = {}) {
  const enabled = opts.enabled !== false;
  if (!container || !enabled) return () => {};

  const selectors = ".chart-card, .backtest-result-header";
  const cleanups = [];

  const attach = () => {
    container.querySelectorAll(selectors).forEach((el) => {
      if (!(el instanceof HTMLElement) || el.querySelector(":scope > .share-copy-btn")) return;

      el.classList.add("share-copyable");
      const title =
        el.querySelector("h3")?.textContent?.trim() ||
        el.querySelector(".backtest-result-header")?.textContent?.trim() ||
        "Chart snapshot";

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "share-copy-btn";
      applyShareCopyButtonChrome(btn, `Copy ${title} as image`);

      const onClick = async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (btn.disabled) return;
        btn.disabled = true;
        try {
          const result = await copyElementToClipboard(el, {
            filename: `quala-${slugifyFilename(title)}.png`,
          });
          btn.classList.add("share-copy-btn--success");
          btn.setAttribute(
            "aria-label",
            result.method === "clipboard" ? "Copied to clipboard" : "Image saved",
          );
        } catch (err) {
          console.warn("Copy chart image failed:", err);
          btn.classList.add("share-copy-btn--failed");
          btn.setAttribute("aria-label", "Copy failed");
        }
        window.setTimeout(() => {
          btn.classList.remove("share-copy-btn--success", "share-copy-btn--failed");
          applyShareCopyButtonChrome(btn, `Copy ${title} as image`);
          btn.disabled = false;
        }, 2200);
      };

      btn.addEventListener("click", onClick);
      el.appendChild(btn);
      cleanups.push(() => {
        btn.removeEventListener("click", onClick);
        btn.remove();
        el.classList.remove("share-copyable");
      });
    });
  };

  const mo = new MutationObserver(() => attach());
  mo.observe(container, { childList: true, subtree: true });
  attach();

  return () => {
    mo.disconnect();
    while (cleanups.length) cleanups.pop()();
  };
}
