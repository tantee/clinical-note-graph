import { config } from '@vue/test-utils'

// Stub localStorage for jsdom in case the env doesn't provide one
if (typeof window !== 'undefined' && !window.localStorage) {
  const store = new Map()
  window.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  }
}

config.global.stubs = {}
