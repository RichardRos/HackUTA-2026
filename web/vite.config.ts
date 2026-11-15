import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api goes to the FastAPI service; API_URL lets compose point it at the api container.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': process.env.API_URL ?? 'http://localhost:8000',
      '/ws': { target: process.env.API_URL ?? 'http://localhost:8000', ws: true },
    },
  },
})
