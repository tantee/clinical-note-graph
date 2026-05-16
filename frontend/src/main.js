import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createVuetify } from 'vuetify'
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'

import App from './App.vue'
import router from './router.js'
import './styles/app.css'

const vuetify = createVuetify({
  defaults: {
    VCard: { rounded: 'lg' },
    VBtn: { rounded: 'lg', class: 'text-none' },
    VTextField: { variant: 'outlined', density: 'comfortable' },
    VSelect: { variant: 'outlined', density: 'comfortable' },
    VTextarea: { variant: 'outlined', density: 'comfortable' },
  },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        dark: false,
        colors: {
          background: '#f5f7fb',
          surface: '#ffffff',
          primary: '#1f6feb',
          'primary-darken-1': '#1455c0',
          secondary: '#7286d3',
          info: '#0288d1',
          success: '#2e7d32',
          warning: '#ed6c02',
          error: '#c62828',
        },
      },
      dark: {
        dark: true,
        colors: {
          background: '#0d1117',
          surface: '#161b22',
          primary: '#58a6ff',
          secondary: '#a5b4fc',
          info: '#38bdf8',
          success: '#4ade80',
          warning: '#fbbf24',
          error: '#f87171',
        },
      },
    },
  },
})

createApp(App).use(createPinia()).use(router).use(vuetify).mount('#app')
