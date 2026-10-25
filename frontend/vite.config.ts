import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = process.env.SYNFULL_BIND_PORT ?? '8000'
const devPort = Number(process.env.SYNFULL_DEV_PORT ?? '5173')

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: devPort,
    strictPort: true,
    proxy: { '/api': `http://127.0.0.1:${apiPort}`, '/health': `http://127.0.0.1:${apiPort}` },
  },
})
