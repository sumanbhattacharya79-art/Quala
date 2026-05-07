import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  root: path.join(__dirname, "app/frontend"),
  base: "/static/",
  build: {
    outDir: path.join(__dirname, "app/frontend/dist"),
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
