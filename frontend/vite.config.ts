import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      // 后端 FastAPI 本地起在 8000;前端一律走同源 /api,免 CORS
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // 每个用例自动清空 vi.fn 的调用记录:跨用例累积会让"只断言出现过"的检查失真
    clearMocks: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
