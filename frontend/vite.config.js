import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Honor a PORT assigned by the tooling (e.g. preview autoPort); default 5173.
    port: Number(process.env.PORT) || 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split heavy libraries into their own chunks so the initial load is smaller.
        // (mermaid is already dynamically imported, so it gets its own chunk automatically.)
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm', 'remark-math', 'rehype-highlight', 'rehype-katex'],
          katex: ['katex'],
        },
      },
    },
  },
})
