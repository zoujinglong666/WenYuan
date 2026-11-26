import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

const src = fileURLToPath(new URL('./src', import.meta.url))
const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8088'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': src },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/v1': { target: apiTarget, changeOrigin: true },
      '/health': { target: apiTarget, changeOrigin: true },
      '/ready': { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
