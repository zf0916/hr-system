// The HR interface's build. Output goes to dist/, which the Dockerfile copies
// into the runtime image — the runtime carries no Node (SPEC §14).
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    // Only used by `npm run dev` on a developer's machine. The container serves
    // the built files; it never runs Vite.
    proxy: { '/api': 'http://127.0.0.1:8090' },
  },
})
