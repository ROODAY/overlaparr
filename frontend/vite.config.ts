import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";

const backend = "http://localhost:8686";

// The backend rejects unsafe methods whose Origin does not match the Host
// (cross-site protection), so the dev proxy rewrites both: changeOrigin sends
// the backend's Host and `headers` replaces the browser's localhost:5173 Origin.
const proxyTo = (target: string) => ({
  target,
  changeOrigin: true,
  headers: { origin: target },
});

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": proxyTo(backend),
      "/health": proxyTo(backend),
    },
    // Tests read the backend Rule schema to check the editor defaults against it.
    ...(process.env.VITEST && {
      fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../backend/overlaparr/config"] },
    }),
  },
  build: {
    outDir: "../backend/overlaparr/static",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
