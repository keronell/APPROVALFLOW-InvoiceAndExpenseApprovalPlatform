import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api + /config to the gateway (or directly to services).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
      "/config": "http://localhost:8080",
    },
  },
});
