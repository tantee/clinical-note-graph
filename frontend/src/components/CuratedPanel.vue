<template>
  <v-card class="fill-height">
    <SectionHeader :title="title" :icon="icon" :color="color">
      <template #actions>
        <v-btn size="small" variant="text" prepend-icon="mdi-plus"
               data-test="curated-add" :aria-label="`Add ${singular}`" @click="openAdd">Add</v-btn>
      </template>
    </SectionHeader>
    <v-divider />

    <v-list v-if="items.length" density="compact">
      <v-list-item v-for="it in items" :key="it.id">
        <v-list-item-title>{{ it.displayValue }}</v-list-item-title>
        <v-list-item-subtitle>
          {{ formatDateRange(it) }}
          <span v-if="it.scheduleText"> · {{ it.scheduleText }}</span>
          <span v-if="it.normalizedCode" data-test="curated-code"> · {{ codeLabel(it) }}</span>
        </v-list-item-subtitle>
        <template #append>
          <v-chip size="x-small" variant="tonal"
                  :color="meta(it.reviewStatus).color"
                  :prepend-icon="meta(it.reviewStatus).icon">
            {{ meta(it.reviewStatus).label }}
          </v-chip>
          <v-btn icon="mdi-pencil" size="x-small" variant="text"
                 data-test="curated-edit" aria-label="Edit" @click="openEdit(it)" />
          <v-btn icon="mdi-delete" size="x-small" variant="text" color="error"
                 data-test="curated-delete" aria-label="Delete" @click="remove(it)" />
        </template>
      </v-list-item>
    </v-list>
    <EmptyState v-else icon="mdi-tray" :title="`No ${title.toLowerCase()} yet`" />

    <v-divider />
    <div class="px-2 py-1">
      <v-btn size="x-small" variant="text"
             :prepend-icon="showDismissed ? 'mdi-chevron-up' : 'mdi-chevron-down'"
             data-test="curated-toggle-dismissed" @click="toggleDismissed">
        {{ showDismissed ? 'Hide dismissed' : 'Show dismissed' }}
      </v-btn>
    </div>
    <v-list v-if="showDismissed && dismissed.length" density="compact">
      <v-list-item v-for="it in dismissed" :key="it.id">
        <v-list-item-title class="text-medium-emphasis text-decoration-line-through">
          {{ it.displayValue }}
        </v-list-item-title>
        <v-list-item-subtitle>{{ formatDateRange(it) }}</v-list-item-subtitle>
        <template #append>
          <v-btn icon="mdi-restore" size="x-small" variant="text" color="primary"
                 data-test="curated-restore" aria-label="Restore" @click="restore(it)" />
        </template>
      </v-list-item>
    </v-list>
    <EmptyState v-else-if="showDismissed" icon="mdi-tray-remove" title="No dismissed items" />

    <v-dialog v-model="dialog" max-width="520">
      <v-card>
        <SectionHeader :title="isNew ? `Add ${singular}` : `Edit ${singular}`" icon="mdi-pencil-outline" />
        <v-divider />
        <v-card-text>
          <v-text-field v-model="form.displayValue" label="Name / value" />
          <div class="d-flex ga-2">
            <v-text-field v-model="form.startDate" label="Start (YYYY-MM-DD)" />
            <v-select v-model="form.startQualifier" :items="START_QUALIFIERS" label="Start qualifier" />
          </div>
          <div class="d-flex ga-2">
            <v-text-field v-model="form.stopDate" label="Stop (YYYY-MM-DD)" />
            <v-select v-model="form.stopQualifier" :items="STOP_QUALIFIERS" label="Stop qualifier" />
          </div>
          <v-text-field v-model="form.scheduleText" label="Schedule (free text)" />
          <v-text-field v-model="form.status" :label="type === 'medication' ? 'Action' : 'Status'" />
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
import { ref, reactive, onMounted, watch } from 'vue'
import SectionHeader from './SectionHeader.vue'
import EmptyState from './EmptyState.vue'
import { REVIEW_META, FACT_TYPE_META } from '../constants/clinical.js'
import { formatDateRange } from '../utils/dateRange.js'
import { useUiStore } from '../stores/ui.js'
import {
  getCurated, createCurated, updateCurated, deleteCurated, restoreCurated,
} from '../api/client.js'

const props = defineProps({
  patientId: { type: String, required: true },
  type: { type: String, required: true },   // 'condition' | 'medication'
  title: { type: String, required: true },
})

const START_QUALIFIERS = ['exact', 'estimated', 'before', 'unknown']
const STOP_QUALIFIERS = ['exact', 'estimated', 'ongoing', 'unknown']

const ui = useUiStore()
const items = ref([])
const dismissed = ref([])
const showDismissed = ref(false)
const dialog = ref(false)
const isNew = ref(true)
const editingId = ref(null)
const form = reactive(emptyForm())
const meta = (s) => REVIEW_META[s] || { color: 'grey', icon: 'mdi-help-circle-outline', label: s || 'unknown' }
const icon = FACT_TYPE_META[props.type]?.icon || 'mdi-clipboard-text'
const color = FACT_TYPE_META[props.type]?.color || 'primary'
const singular = props.type === 'medication' ? 'medication' : 'problem'

function codeLabel(it) {
  return `${it.codingSystem || 'code'}: ${it.normalizedCode}`
}

function emptyForm() {
  return {
    displayValue: '', startDate: '', startQualifier: 'unknown',
    stopDate: '', stopQualifier: 'ongoing', scheduleText: '', status: '',
  }
}

async function load() {
  const res = await getCurated(props.patientId, props.type, undefined)
  items.value = res.items || []
  if (showDismissed.value) await loadDismissed()
}

async function loadDismissed() {
  const res = await getCurated(props.patientId, props.type, undefined, 'dismissed')
  dismissed.value = res.items || []
}

async function toggleDismissed() {
  showDismissed.value = !showDismissed.value
  if (showDismissed.value) await loadDismissed()
}

async function restore(it) {
  try {
    await restoreCurated(it.id)
    await load()
    ui.success('Restored')
  } catch {
    ui.error('Restore failed')
  }
}

function openAdd() {
  Object.assign(form, emptyForm())
  isNew.value = true
  editingId.value = null
  dialog.value = true
}

function openEdit(it) {
  Object.assign(form, {
    displayValue: it.displayValue, startDate: it.startDate || '',
    startQualifier: it.startQualifier, stopDate: it.stopDate || '',
    stopQualifier: it.stopQualifier, scheduleText: it.scheduleText || '',
    status: it.status || '',
  })
  isNew.value = false
  editingId.value = it.id
  dialog.value = true
}

function payload() {
  return {
    type: props.type,
    displayValue: form.displayValue,
    startDate: form.startDate || null,
    startQualifier: form.startQualifier,
    stopDate: form.stopDate || null,
    stopQualifier: form.stopQualifier,
    scheduleText: form.scheduleText || null,
    status: form.status || null,
  }
}

async function save() {
  if (!form.displayValue) { ui.error('Name is required'); return }
  try {
    if (isNew.value) {
      await createCurated(props.patientId, payload())
      ui.success('Added')
    } else {
      const { type, ...patch } = payload()
      await updateCurated(editingId.value, patch)
      ui.success('Saved')
    }
    dialog.value = false
    await load()
  } catch {
    ui.error('Save failed')
  }
}

async function remove(it) {
  if (!window.confirm(`Remove "${it.displayValue}" from this list?`)) return
  try {
    await deleteCurated(it.id)
    await load()
    ui.success('Removed')
  } catch {
    ui.error('Delete failed')
  }
}

defineExpose({ form, save })
watch(() => props.patientId, load)
onMounted(load)
</script>
