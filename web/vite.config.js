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
          // Do NOT proxy static assets (JS modules, CSS, images, etc.)
          // These are Vite-served files, not backend API routes.
          // Backend API routes have no file extension.
          const url = req.url || "";
          const isStaticAsset = /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|map)$/i.test(url);
          // Backend API routes: /api/auth/login, /api/contracts/audit, etc. (no extension)
          const isApiRoute = url.startsWith("/api/") && !isStaticAsset;
          if (!isApiRoute) {
            return url; // bypass proxy, let Vite serve it locally
          }
          return undefined; // go through proxy to backend
        },
      },
    },
  },
})