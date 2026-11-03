import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// En desarrollo la UI usa el backend de `gestplan serve` (puerto 8765) a través del proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
  build: { outDir: 'dist', sourcemap: false },
})
