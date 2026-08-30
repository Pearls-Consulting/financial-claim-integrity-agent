import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    rollupOptions: {
      output: {
        // pdf.js's worker is pulled in as an .mjs asset (PdfViewerPanel's
        // `new URL(..., import.meta.url)`); emit it as .js so any static
        // host serves it with a JavaScript MIME type — browsers refuse to
        // run a module script served as application/octet-stream.
        assetFileNames: (info) =>
          (info.names?.[0] ?? info.name ?? "").endsWith(".mjs")
            ? "assets/[name]-[hash].js"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
})
