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
          <SectionHeader title="AI provider" icon="mdi-robot-outline">
            <template #actions>
              <v-menu>
                <template #activator="{ props }">
                  <v-btn v-bind="props" size="small" variant="text" prepend-icon="mdi-lightning-bolt-outline">Quick setup</v-btn>
                </template>
                <v-list density="compact">
                  <v-list-item @click="applyPreset('openrouter')" title="OpenRouter" subtitle="Single key, many models" />
                  <v-list-item @click="applyPreset('openai')" title="OpenAI" subtitle="api.openai.com" />
                  <v-list-item @click="applyPreset('groq')" title="Groq" subtitle="Llama, Mixtral on Groq Cloud" />
                  <v-list-item @click="applyPreset('mock')" title="Mock (offline)" subtitle="Deterministic keyword extractor" />
                </v-list>
              </v-menu>
            </template>
          </SectionHeader>
          <v-divider />
          <!-- The form fields used inconsistent margins (some mt-2, others
               relying on persistent-hint to add a gap). When persistent-hint
               wasn't set the next field landed flush against the previous
               one, especially after the API key row. Use a flex column with
               an explicit `ga-3` gap so every field has the same breathing
               room regardless of whether it shows a hint. -->
          <v-card-text class="d-flex flex-column ga-3">
            <v-select v-model="patch.AI_PROVIDER" :items="['mock','openai','custom']" label="Provider"
                      hint="Use 'openai' for any OpenAI-compatible endpoint (OpenRouter, Groq, vLLM, …)" persistent-hint />
            <v-text-field v-model="patch.AI_BASE_URL" label="API base URL"
                          placeholder="https://openrouter.ai/api/v1" />
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
            <v-text-field v-model="patch.AI_MODEL" label="Default chat model"
                          hint="Used for any chat task without a per-task override below" persistent-hint />
            <v-text-field v-model="patch.AI_MODEL_EXTRACT" label="Model — EMR extract"
                          placeholder="(blank = use default)" persistent-placeholder
                          hint="Strongest model recommended here — handles severity, temporality, and inter-fact relationships"
                          persistent-hint />
            <v-text-field v-model="patch.AI_MODEL_SUMMARY" label="Model — Summary"
                          placeholder="(blank = use default)" persistent-placeholder />
            <v-text-field v-model="patch.AI_MODEL_CODING" label="Model — Coding suggest"
                          placeholder="(blank = use default)" persistent-placeholder />
            <v-divider class="my-1" />
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
        <PricingTable />
      </v-col>

      <v-col cols="12">
        <v-card>
          <SectionHeader title="Export profiles" icon="mdi-file-export-outline">
            <template #actions>
              <v-btn size="small" variant="tonal" color="primary" prepend-icon="mdi-plus" @click="newProfile">New profile</v-btn>
            </template>
          </SectionHeader>
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
                  <EmptyState v-if="!profiles.length" icon="mdi-file-export-outline" title="No profiles yet"
                              hint="Click 'New profile' to add one." />
                </v-list>
              </v-col>
              <v-col cols="12" md="7">
                <div v-if="selected">
                  <v-text-field v-model="selected.profile_id" label="profileId"
                                hint="lowercase-with-dashes, used in URLs"
                                persistent-hint />
                  <v-text-field v-model="selected.name" label="Name" class="mt-2" />
                  <v-textarea v-model="selectedConfigStr" label="Config (JSON)" rows="10"
                              :error-messages="profileError ? [profileError] : []" class="mt-2" />
                  <div class="d-flex">
                    <v-btn color="primary" prepend-icon="mdi-content-save-outline"
                           :disabled="!selected.profile_id || !selected.name || !!profileError"
                           @click="saveProfile">Save profile</v-btn>
                    <v-spacer />
                    <v-btn variant="text" @click="selected = null">Cancel</v-btn>
                  </div>
                </div>
                <v-alert v-else type="info" variant="tonal">
                  Select a profile to edit, or click <strong>New profile</strong> to add one.
                </v-alert>
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
import {
  getConfig, patchConfig, listExportProfiles, upsertExportProfile,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import PricingTable from '../components/PricingTable.vue'

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
  // Both /api/config and /api/config/export-profiles sit behind the
  // X-API-Key gate in prod. Wrap each call so a 401 (or any failure)
  // doesn't crash the page — the Browser API key card below still has
  // to render so the user can paste their key and unlock the rest.
  try {
    const cfg = await getConfig()
    current.value = cfg
    // Only seed the patch with non-secret fields. AI_API_KEY is intentionally left empty
    // (the masked value is shown as placeholder; empty submission means "leave unchanged").
    patch.value = {
      AI_PROVIDER: cfg.settings.AI_PROVIDER,
      AI_BASE_URL: cfg.settings.AI_BASE_URL,
      AI_API_KEY: '',
      AI_MODEL: cfg.settings.AI_MODEL,
      AI_MODEL_EXTRACT: cfg.settings.AI_MODEL_EXTRACT || '',
      AI_MODEL_SUMMARY: cfg.settings.AI_MODEL_SUMMARY || '',
      AI_MODEL_CODING: cfg.settings.AI_MODEL_CODING || '',
      AI_EMBEDDING_MODEL: cfg.settings.AI_EMBEDDING_MODEL,
      VAULT_PATH: cfg.settings.VAULT_PATH,
      CODING_ICD10: cfg.settings.CODING_ICD10,
      CODING_SNOMEDCT: cfg.settings.CODING_SNOMEDCT,
      CODING_LOINC: cfg.settings.CODING_LOINC,
      CODING_RXNORM: cfg.settings.CODING_RXNORM,
    }
  } catch {
    // Leave current.value / patch.value at their initial empty shapes.
    // The global axios error handler already surfaced a snackbar.
  }
  try {
    profiles.value = await listExportProfiles()
  } catch {
    profiles.value = []
  }
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

const PRESETS = {
  openrouter: { AI_PROVIDER: 'openai', AI_BASE_URL: 'https://openrouter.ai/api/v1', AI_MODEL: 'anthropic/claude-3.5-sonnet', AI_EMBEDDING_MODEL: 'openai/text-embedding-3-small' },
  openai:     { AI_PROVIDER: 'openai', AI_BASE_URL: 'https://api.openai.com/v1',    AI_MODEL: 'gpt-4o-mini',                  AI_EMBEDDING_MODEL: 'text-embedding-3-small' },
  groq:       { AI_PROVIDER: 'openai', AI_BASE_URL: 'https://api.groq.com/openai/v1', AI_MODEL: 'llama-3.3-70b-versatile',    AI_EMBEDDING_MODEL: 'text-embedding-3-small' },
  mock:       { AI_PROVIDER: 'mock',   AI_BASE_URL: '',                              AI_MODEL: 'gpt-4o-mini',                  AI_EMBEDDING_MODEL: 'text-embedding-3-small' },
}
function applyPreset(name) {
  const preset = PRESETS[name]
  if (!preset) return
  Object.assign(patch.value, preset)
  // Don't touch AI_API_KEY — that's user-supplied and we never store the masked value.
  ui.success(`Applied ${name} preset. Set your API key, then Save.`)
}
function newProfile() {
  selected.value = {
    profile_id: '',
    name: '',
    config: { format: 'fhir', includeRejected: false, includeEvidence: true },
  }
}

async function saveProfile() {
  if (profileError.value) {
    ui.error('Fix the JSON in the config field first.')
    return
  }
  if (!selected.value.profile_id || !selected.value.name) {
    ui.error('profileId and Name are required.')
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
