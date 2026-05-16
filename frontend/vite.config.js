import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig(({ mode }) => {
  const isTest = mode === 'test' || process.env.VITEST === 'true'
  return {
    // vite-plugin-vuetify auto-imports vuetify components AND injects CSS side-effects.
    // Vitest can't parse those CSS files, so we leave the plugin out in test mode and
    // rely on test-level stubs for v-* components.
    plugins: [vue(), ...(isTest ? [] : [vuetify({ autoImport: true })])],
    server: {
      host: '0.0.0.0',
      port: 5173,
      watch: { usePolling: true },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      include: ['tests/**/*.spec.{js,ts}', 'src/**/*.spec.{js,ts}'],
      // Playwright suites live in tests/e2e — never run them under Vitest.
      exclude: ['node_modules/**', 'tests/e2e/**', 'dist/**'],
      setupFiles: ['./tests/setup.js'],
      server: {
        deps: { inline: ['vuetify'] },
      },
      css: false,
    },
  }
})
