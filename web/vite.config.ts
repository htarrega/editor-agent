import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'

// El front habla siempre con `/api`, mismo origen. En desarrollo lo redirige
// este proxy; en producción lo sirve el mismo contenedor que la API, así que
// no hay CORS en ningún lado y las dos situaciones no divergen.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
