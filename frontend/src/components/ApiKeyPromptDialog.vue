<template>
  <!-- Persistent modal: triggered when a request 401s with the
       "X-API-Key" detail. The user cannot dismiss without either
       entering a key or explicitly opening Config. This replaces the
       earlier snackbar-only prompt, which was too easy to miss on a
       fresh prod deployment where every page-load endpoint 401s. -->
  <v-dialog v-model="open" max-width="520" persistent>
    <v-card>
      <v-card-title class="text-subtitle-1">
        <v-icon class="mr-2" color="warning">mdi-key-alert-outline</v-icon>
        Backend requires an API key
      </v-card-title>
      <v-card-text>
        <p class="text-body-2 mb-3">
          This backend is started with <code>BACKEND_API_KEY</code> set, so
          every <code>/api</code> request must carry an <code>X-API-Key</code>
          header. Paste the value here — it's stored only in
          <code>localStorage</code> in this browser.
        </p>

        <v-text-field
          v-model="keyInput"
          label="X-API-Key"
          :type="showKey ? 'text' : 'password'"
          :append-inner-icon="showKey ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
          autofocus
          @click:append-inner="showKey = !showKey"
          @keyup.enter="onSave"
        />

        <v-alert
          v-if="error"
          type="error"
          density="compact"
          variant="tonal"
          class="mt-2"
        >{{ error }}</v-alert>

        <v-alert
          type="info"
          variant="tonal"
          density="compact"
          icon="mdi-information-outline"
          class="mt-3"
        >
          You can change or clear this key later in
          <strong>Config → Browser API key</strong>.
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-btn variant="text" @click="goToConfig">Open Config</v-btn>
        <v-spacer />
        <v-btn
          color="primary"
          :disabled="!keyInput"
          @click="onSave"
        >Save key</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useUiStore } from '../stores/ui.js'
import { resetApiKeyPromptShown } from '../api/client.js'

const ui = useUiStore()
const router = useRouter()

const keyInput = ref('')
const showKey = ref(false)
const error = ref('')

const open = computed({
  get: () => ui.apiKeyDialog,
  set: (v) => { if (!v) ui.dismissApiKeyDialog() },
})

// Reset transient state every time the dialog opens — so a previous
// failed save doesn't leave an "error" banner stuck on the next open.
watch(open, (now) => {
  if (now) {
    keyInput.value = localStorage.getItem('cng_api_key') || ''
    error.value = ''
    showKey.value = false
  }
})

function onSave() {
  const v = keyInput.value.trim()
  if (!v) {
    error.value = 'Enter the X-API-Key value from your BACKEND_API_KEY env var.'
    return
  }
  localStorage.setItem('cng_api_key', v)
  // Clear the once-per-session 401 latch so a future bad key reopens
  // the dialog instead of silently swallowing the next 401.
  resetApiKeyPromptShown()
  ui.dismissApiKeyDialog()
  ui.success('API key saved — retry your action.')
}

function goToConfig() {
  ui.dismissApiKeyDialog()
  router.push({ name: 'config' })
}
</script>
