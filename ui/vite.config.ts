import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const version_file = fileURLToPath(new URL('../marathon/common/version.py', import.meta.url))

// The Python package is the single source of the version number
function read_app_version(): string {
  const match = readFileSync(version_file, 'utf-8').match(/^app_version = "(.+)"$/m)
  if (!match) throw new Error(`app_version not found in ${version_file}`)
  return match[1]
}

export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(read_app_version()) },
  build: {
    outDir: fileURLToPath(new URL('../marathon/server/static', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1000,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': process.env.MARATHON_API_URL ?? 'http://127.0.0.1:8080' },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
})
