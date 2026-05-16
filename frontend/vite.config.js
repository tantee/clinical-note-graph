import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    host: '0.0.0.0',
    port: 5173,
    watch: { usePolling: true },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.spec.{js,ts}', 'tests/**/*.spec.{js,ts}'],
    setupFiles: ['./tests/setup.js'],
  },
})
