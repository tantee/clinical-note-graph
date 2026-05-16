<template>
  <div>
    <div class="mb-4">
      <h1 class="text-h5 font-weight-bold mb-1">Configuration</h1>
      <div class="text-body-2 text-grey-darken-1">
        Adjust runtime AI provider, coding standards, and export profiles. Changes are persisted in the database
        and merged over environment defaults — no restart required.
      </div>
    </div>

    <v-row>
      <v-col cols="12" md="6">
        <v-card>
          <SectionHeader title="AI provider" icon="mdi-robot-outline" />
          <v-divider />
          <v-card-text>
            <v-select v-model="patch.AI_PROVIDER" :items="['mock','openai','ollama','custom']" label="Provider" />
            <v-text-field v-model="patch.AI_BASE_URL" label="API base URL" placeholder="https://api.openai.com/v1 or http://ollama:11434/v1" />
            <v-text-field
              v-model="patch.AI_API_KEY"
              label="API key"
              :type="showKey ? 'text' : 'password'"
              :append-inner-icon="showKey ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
              @click:append-inner="showKey = !showKey"
              :placeholder="apiKeyPlaceholder"
              persistent-placeholder
              :hint="apiKeyHint"
              persistent-hint
            />
            <v-text-field v-model="patch.AI_MODEL" label="Model" />
            <v-text-field v-model="patch.AI_EMBEDDING_MODEL" label="Embedding model" />
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-card>
          <SectionHeader title="Coding standards &amp; storage" icon="mdi-tag-outline" />
          <v-divider />
          <v-card-text>
            <v-row>
              <v-col cols="6"><v-switch v-model="patch.CODING_ICD10" label="ICD-10" color="primary" hide-details /></v-col>
              <v-col cols="6"><v-switch v-model="patch.CODING_SNOMEDCT" label="SNOMED CT" color="primary" hide-details /></v-col>
              <v-col cols="6"><v-switch v-model="patch.CODING_LOINC" label="LOINC" color="primary" hide-details /></v-col>
              <v-col cols="6"><v-switch v-model="patch.CODING_RXNORM" label="RxNorm" color="primary" hide-details /></v-col>
            </v-row>
            <v-text-field v-model="patch.VAULT_PATH" label="Markdown vault path (in container)" class="mt-3" hint="Bind-mounted at /data/vault by default" persistent-hint />
          </v-card-text>
          <v-divider />
          <v-card-actions class="px-4 pb-4">
            <v-btn color="primary" :loading="saving" prepend-icon="mdi-content-save-outline" @click="save">Save changes</v-btn>
            <v-btn variant="text" @click="reset">Reset</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card>
          <SectionHeader title="Browser API key" icon="mdi-key-outline" />
          <v-divider />
          <v-card-text>
            <div class="text-body-2 text-grey-darken-1 mb-2">
              Stored only in <code>localStorage</code> and sent as <code>X-API-Key</code> on every request.
              Set this when the backend is started with the <code>API_KEY</code> env var (production).
            </div>
            <v-text-field
              v-model="browserKey"
              label="X-API-Key (browser-side)"
              :type="showBrowserKey ? 'text' : 'password'"
              :append-inner-icon="showBrowserKey ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
              @click:append-inner="showBrowserKey = !showBrowserKey"
            />
            <v-btn color="primary" variant="tonal" @click="saveBrowserKey">Save in this browser</v-btn>
            <v-btn variant="text" class="ml-2" @click="clearBrowserKey">Clear</v-btn>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card>
          <SectionHeader title="Export profiles" icon="mdi-file-export-outline" />
          <v-divider />
          <v-card-text>
            <v-row>
              <v-col cols="12" md="5">
                <v-list density="compact" nav>
                  <v-list-item
                    v-for="p in profiles"
                    :key="p.profile_id"
                    :title="p.name"
                    :subtitle="p.profile_id"
                    :active="selected?.profile_id === p.profile_id"
                    @click="selected = structuredClone(p)"
                  />
                  <EmptyState v-if="!profiles.length" icon="mdi-file-export-outline" title="No profiles yet" />
                </v-list>
              </v-col>
              <v-col cols="12" md="7">
                <div v-if="selected">
                  <v-text-field v-model="selected.profile_id" label="profileId" />
                  <v-text-field v-model="selected.name" label="Name" />
                  <v-textarea v-model="selectedConfigStr" label="Config (JSON)" rows="10" :error-messages="profileError ? [profileError] : []" />
                  <v-btn color="primary" prepend-icon="mdi-content-save-outline" @click="saveProfile">Save profile</v-btn>
                </div>
                <v-alert v-else type="info" variant="tonal">Select a profile to edit.</v-alert>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card>
          <SectionHeader title="Effective configuration" icon="mdi-information-outline" />
          <v-divider />
          <v-card-text><pre class="cng-raw">{{ JSON.stringify(current, null, 2) }}</pre></v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getConfig, patchConfig, listExportProfiles, upsertExportProfile } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const ui = useUiStore()

const current = ref({})
const patch = ref({})
const profiles = ref([])
const selected = ref(null)
const profileError = ref('')
const saving = ref(false)
const showKey = ref(false)
const showBrowserKey = ref(false)
const browserKey = ref(localStorage.getItem('cng_api_key') || '')

const apiKeyPlaceholder = computed(() => current.value?.settings?.AI_API_KEY || 'sk-…')
const apiKeyHint = computed(() => 'Leave empty to keep the existing key. The current value is shown masked.')

const selectedConfigStr = computed({
  get: () => (selected.value ? JSON.stringify(selected.value.config, null, 2) : ''),
  set: (v) => {
    if (!selected.value) return
    try {
      selected.value.config = JSON.parse(v)
      profileError.value = ''
    } catch (e) {
      profileError.value = e.message
    }
  },
})

async function load() {
  const cfg = await getConfig()
  current.value = cfg
  // Only seed the patch with non-secret fields. AI_API_KEY is intentionally left empty
  // (the masked value is shown as placeholder; empty submission means "leave unchanged").
  patch.value = {
    AI_PROVIDER: cfg.settings.AI_PROVIDER,
    AI_BASE_URL: cfg.settings.AI_BASE_URL,
    AI_API_KEY: '',
    AI_MODEL: cfg.settings.AI_MODEL,
    AI_EMBEDDING_MODEL: cfg.settings.AI_EMBEDDING_MODEL,
    VAULT_PATH: cfg.settings.VAULT_PATH,
    CODING_ICD10: cfg.settings.CODING_ICD10,
    CODING_SNOMEDCT: cfg.settings.CODING_SNOMEDCT,
    CODING_LOINC: cfg.settings.CODING_LOINC,
    CODING_RXNORM: cfg.settings.CODING_RXNORM,
  }
  profiles.value = await listExportProfiles()
}
async function save() {
  saving.value = true
  try {
    const payload = { ...patch.value }
    if (!payload.AI_API_KEY) delete payload.AI_API_KEY
    await patchConfig(payload)
    ui.success('Configuration saved.')
    await load()
  } finally {
    saving.value = false
  }
}
function reset() { load() }
async function saveProfile() {
  if (profileError.value) {
    ui.error('Fix the JSON in the config field first.')
    return
  }
  await upsertExportProfile({ profileId: selected.value.profile_id, name: selected.value.name, config: selected.value.config })
  profiles.value = await listExportProfiles()
  ui.success(`Profile "${selected.value.profile_id}" saved.`)
}
function saveBrowserKey() {
  if (browserKey.value) localStorage.setItem('cng_api_key', browserKey.value)
  else localStorage.removeItem('cng_api_key')
  ui.success('Browser API key saved.')
}
function clearBrowserKey() {
  localStorage.removeItem('cng_api_key')
  browserKey.value = ''
  ui.success('Browser API key cleared.')
}

onMounted(load)
</script>
