import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: Number(process.env.PORT) || 3000,
    proxy: {
      "/analyze": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
      "/metadata": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
