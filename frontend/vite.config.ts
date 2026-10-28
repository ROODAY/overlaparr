import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";

const backend = "http://localhost:8686";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": backend,
      "/health": backend,
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
