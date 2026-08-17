import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // En desarrollo el frontend corre aparte y habla con el backend local.
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
  build: {
    // El dist se commitea: el tutor no instala Node ni buildea nada.
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
