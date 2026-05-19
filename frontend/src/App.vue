<template>
  <v-app class="cng-app" :theme="ui.theme">
    <v-app-bar density="comfortable" :elevation="1" color="surface">
      <div class="d-flex align-center font-weight-bold pl-4">
        <v-icon class="mr-2" color="primary">mdi-graph-outline</v-icon>
        <span class="text-body-1 mr-3">Clinical Note Graph</span>
        <v-chip
          size="x-small"
          color="warning"
          variant="tonal"
          prepend-icon="mdi-alert-decagram-outline"
          class="d-none d-md-inline-flex"
        >
          AI-assisted · review required
        </v-chip>
      </div>
      <v-spacer />
      <PatientSearchInput class="d-none d-md-inline-flex mr-3" />
      <v-btn variant="text" to="/patients" prepend-icon="mdi-account-multiple-outline">Patients</v-btn>
      <v-btn variant="text" to="/ingest" prepend-icon="mdi-cloud-upload-outline">Ingest</v-btn>
      <v-btn variant="text" to="/config" prepend-icon="mdi-cog-outline">Config</v-btn>
      <v-btn variant="text" to="/debug" prepend-icon="mdi-chart-line-variant">Debug</v-btn>
      <v-btn variant="text" to="/vector-demo" prepend-icon="mdi-database-search-outline">Vector</v-btn>
      <v-btn
        variant="text"
        :href="apiDocsUrl"
        target="_blank"
        prepend-icon="mdi-api"
        aria-label="OpenAPI docs"
      >API</v-btn>
      <v-btn
        :icon="ui.theme === 'dark' ? 'mdi-weather-sunny' : 'mdi-weather-night'"
        variant="text"
        aria-label="Toggle theme"
        @click="ui.toggleTheme"
      />
    </v-app-bar>

    <v-main>
      <v-container fluid class="pa-6">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </v-container>
    </v-main>

    <v-snackbar
      v-model="ui.snackbar.show"
      :color="ui.snackbar.color"
      :timeout="ui.snackbar.timeout"
      location="bottom right"
    >
      {{ ui.snackbar.message }}
      <template #actions>
        <v-btn variant="text" @click="ui.dismiss">Close</v-btn>
      </template>
    </v-snackbar>

    <v-footer color="transparent" class="text-caption text-grey">
      <span>Prototype &mdash; not for clinical use.</span>
      <v-spacer />
      <a :href="apiDocsUrl" target="_blank" class="text-grey">OpenAPI</a>
    </v-footer>
  </v-app>
</template>

<script setup>
import { computed } from 'vue'
import { useUiStore } from './stores/ui.js'
import PatientSearchInput from './components/PatientSearchInput.vue'

const ui = useUiStore()
const apiDocsUrl = computed(() => (import.meta.env.VITE_API_BASE || 'http://localhost:8000') + '/docs')
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
