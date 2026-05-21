import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    theme: localStorage.getItem('cng_theme') || 'light',
    snackbar: { show: false, message: '', color: 'info', timeout: 4000 },
  }),
  actions: {
    setTheme(t) {
      this.theme = t
      localStorage.setItem('cng_theme', t)
    },
    toggleTheme() {
      this.setTheme(this.theme === 'light' ? 'dark' : 'light')
    },
    notify(message, color = 'info', timeout = 4000) {
      this.snackbar = { show: true, message, color, timeout }
    },
    success(msg) { this.notify(msg, 'success') },
    // Accept an optional `{ timeout }` override so action-oriented
    // toasts (e.g. "set your X-API-Key in Config →…") can stay visible
    // long enough to read.
    error(msg, opts = {}) { this.notify(msg, 'error', opts.timeout ?? 6000) },
    warning(msg) { this.notify(msg, 'warning') },
    dismiss() { this.snackbar.show = false },
  },
})
