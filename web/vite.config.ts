import { defineConfig } from "vite";

// Build ships into package templates so `lk-sim web` serves without Node on the user machine.
export default defineConfig({
  base: "/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/runs": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "../templates/report-player",
    emptyOutDir: true,
    sourcemap: true,
    assetsDir: "assets",
  },
});
