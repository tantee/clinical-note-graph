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
    error(msg) { this.notify(msg, 'error', 6000) },
    warning(msg) { this.notify(msg, 'warning') },
    dismiss() { this.snackbar.show = false },
  },
})
