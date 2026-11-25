import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to FastAPI. This is why no CORS middleware had to
// be added to the backend: in development the browser only ever talks to Vite,
// which forwards same-origin. For a deployed frontend on a different origin the
// backend would need CORS — see KNOWN_LIMITATIONS in the report.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
