/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the MSW mock answers /api; set VITE_API_PROXY to use a real backend instead.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: process.env.VITE_API_PROXY ? { "/api": process.env.VITE_API_PROXY } : undefined,
  },
  test: { environment: "jsdom", setupFiles: ["src/test/setup.ts"] },
});
