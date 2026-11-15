import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api goes to the FastAPI service; API_URL lets compose point it at the api container.
// VITE_DEV_AUTH_BYPASS=1 (local test image only) swaps Clerk for a signed-in stub; never set it for production.
const devAuth = process.env.VITE_DEV_AUTH_BYPASS === '1'

export default defineConfig({
  plugins: [react()],
  resolve: devAuth ? { alias: { '@clerk/react': fileURLToPath(new URL('./src/dev/clerk-stub.tsx', import.meta.url)) } } : {},
  server: {
    host: true,
    proxy: {
      '/api': process.env.API_URL ?? 'http://localhost:8000',
      '/ws': { target: process.env.API_URL ?? 'http://localhost:8000', ws: true },
    },
  },
})
