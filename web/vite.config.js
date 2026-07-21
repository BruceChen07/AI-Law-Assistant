import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
        bypass(req) {
          // Only proxy /api/... routes, not /api.js /api.css etc.
          if (req.url && !req.url.startsWith("/api/")) {
            return req.url;
          }
        },
      },
    },
  },
})