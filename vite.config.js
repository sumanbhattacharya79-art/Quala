import { copyFileSync, existsSync } from "fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const isVercel = Boolean(process.env.VERCEL);

// Vercel serves the SPA at the site root; FastAPI serves the same build under /static.
const base = isVercel ? "/" : "/static/";

// Vercel's default output folder is repo-root `dist`. Local / FastAPI keep `app/frontend/dist`.
const outDir = isVercel
  ? path.join(__dirname, "dist")
  : path.join(__dirname, "app/frontend/dist");

/** After Vercel build, copy entry HTML to `index.html` so `/` works without vercel.json rewrites. */
function vercelRootIndexHtml() {
  return {
    name: "vercel-root-index-html",
    closeBundle() {
      if (!isVercel) return;
      const from = path.join(outDir, "index-react.html");
      const to = path.join(outDir, "index.html");
      if (existsSync(from)) copyFileSync(from, to);
    },
  };
}

export default defineConfig({
  plugins: [react(), vercelRootIndexHtml()],
  root: path.join(__dirname, "app/frontend"),
  base,
  build: {
    outDir,
    emptyOutDir: true,
    rollupOptions: {
      input: path.join(__dirname, "app/frontend/index-react.html"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
