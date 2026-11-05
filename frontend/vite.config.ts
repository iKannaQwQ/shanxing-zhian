import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期把 /api 与 /ws 代理到本机后端，避免 CORS 与端口配置。
export default defineConfig({
  plugins: [vue()],
  // es2019: 覆盖 Safari 12.1+ / Chrome 80+ / Edge 79+，避免老浏览器白屏
  build: { target: 'es2019' },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
