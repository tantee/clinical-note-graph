<template>
  <v-card>
    <SectionHeader title="Model pricing" icon="mdi-tag-outline">
      <template #actions>
        <v-btn variant="text" prepend-icon="mdi-refresh" :loading="refreshing" @click="onRefresh">Refresh from OpenRouter</v-btn>
        <v-btn variant="text" prepend-icon="mdi-plus" @click="addRow">Add</v-btn>
      </template>
    </SectionHeader>
    <v-divider />
    <v-data-table density="comfortable" :headers="headers" :items="rows" :loading="loading">
      <template #item.prompt_per_1m="{ item }">{{ fmt(item.prompt_per_1m) }}</template>
      <template #item.completion_per_1m="{ item }">{{ fmt(item.completion_per_1m) }}</template>
      <template #item.embedding_per_1m="{ item }">{{ fmt(item.embedding_per_1m) }}</template>
      <template #item.actions="{ item }">
        <v-btn size="x-small" variant="text" prepend-icon="mdi-pencil" @click="edit(item)">Edit</v-btn>
        <v-btn size="x-small" variant="text" color="error" prepend-icon="mdi-delete" @click="remove(item)">Delete</v-btn>
      </template>
    </v-data-table>

    <v-dialog v-model="dialog" max-width="500">
      <v-card>
        <SectionHeader title="Edit pricing" icon="mdi-pencil-outline" />
        <v-divider />
        <v-card-text>
          <v-text-field v-model="form.model" label="Model" :disabled="!isNew" />
          <v-text-field v-model.number="form.prompt_per_1m" label="Prompt $ / 1M" type="number" step="0.0001" />
          <v-text-field v-model.number="form.completion_per_1m" label="Completion $ / 1M" type="number" step="0.0001" />
          <v-text-field v-model.number="form.embedding_per_1m" label="Embedding $ / 1M" type="number" step="0.0001" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn color="primary" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listPricing, upsertPricing, deletePricing, refreshOpenRouter } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from './SectionHeader.vue'

const ui = useUiStore()
const headers = [
  { title: 'Model', key: 'model' },
  { title: 'Prompt $/1M', key: 'prompt_per_1m', align: 'end' },
  { title: 'Completion $/1M', key: 'completion_per_1m', align: 'end' },
  { title: 'Embedding $/1M', key: 'embedding_per_1m', align: 'end' },
  { title: 'Source', key: 'source' },
  { title: '', key: 'actions', sortable: false, align: 'end' },
]
const rows = ref([])
const loading = ref(false)
const refreshing = ref(false)
const dialog = ref(false)
const isNew = ref(false)
const form = ref({})

const fmt = (v) => (v == null ? '–' : `$${Number(v).toFixed(4)}`)

async function load() {
  loading.value = true
  try {
    rows.value = await listPricing()
  } finally {
    loading.value = false
  }
}

function addRow() {
  form.value = { model: '', prompt_per_1m: null, completion_per_1m: null, embedding_per_1m: null }
  isNew.value = true
  dialog.value = true
}
function edit(item) {
  form.value = { ...item }
  isNew.value = false
  dialog.value = true
}
async function save() {
  if (!form.value.model) { ui.error('Model is required'); return }
  await upsertPricing(form.value.model, {
    prompt_per_1m: form.value.prompt_per_1m ?? null,
    completion_per_1m: form.value.completion_per_1m ?? null,
    embedding_per_1m: form.value.embedding_per_1m ?? null,
    source: 'manual',
  })
  ui.success(`Saved ${form.value.model}`)
  dialog.value = false
  await load()
}
async function remove(item) {
  if (!window.confirm(`Delete pricing for ${item.model}?`)) return
  await deletePricing(item.model)
  await load()
}
async function onRefresh() {
  refreshing.value = true
  try {
    const r = await refreshOpenRouter()
    ui.success(`Upserted ${r.upserted} models from OpenRouter`)
    await load()
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>
