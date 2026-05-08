import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/splat-highlight-pilot/",
  define: {
    __BUILD_DATE__: JSON.stringify(new Date().toISOString()),
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL || "http://localhost:8030",
        changeOrigin: true,
      },
    },
  },
});
