import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Default build: served by the local FastAPI backend (absolute /api, /files).
// `--mode showcase`: static GitHub Pages showcase — relative base (works under /<repo>/), read-only synthetic
// snapshot from showcase-public/, separate output folder so it can never replace the local app's build.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "showcase" ? "./" : "/",
  publicDir: mode === "showcase" ? "showcase-public" : "public",
  build: { outDir: mode === "showcase" ? "dist-showcase" : "dist" },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/files": "http://127.0.0.1:8000",
    },
  },
}));
