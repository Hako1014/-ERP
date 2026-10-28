import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  envDir: '..',
  cacheDir: '.vite-cache',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 5173 落在 Windows 动态端口排除区（5162–5261，Hyper-V 预留），宿主机 bind 报
    // EACCES: permission denied，故改用 5273（已实测可绑定，且在全部排除区之外）。
    // 与 .env 中 Redis 由 6379 改 16379 是同一类处理。
    // 改端口时需同步：backend/app/core/config.py 的 CORS 白名单、
    // frontend/playwright.config.ts 的 baseURL。
    port: 5273,
    host: true,
  },
})
