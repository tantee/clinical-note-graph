<template>
  <v-card class="d-flex flex-column h-100">
    <div class="d-flex align-center pa-2 ga-2 flex-wrap">
      <v-chip-group v-if="scope === 'patient'" v-model="scopeChip" mandatory
                    selected-class="bg-primary-lighten-4">
        <v-chip value="all" filter>All</v-chip>
        <v-chip value="latest" filter>Latest encounter</v-chip>
        <v-chip value="pick" filter>Pick…</v-chip>
      </v-chip-group>
      <span v-else class="text-caption text-grey-darken-1 ml-2">
        Encounter scope · {{ encounterIds.length }} encounter(s)
      </span>
      <v-spacer />
      <span v-if="loading" class="text-caption text-grey-darken-1 mr-2">loading…</span>
      <v-btn icon="mdi-fit-to-page-outline" variant="text" size="small" @click="fit" aria-label="Fit view" />
      <v-btn icon="mdi-cog-outline" variant="text" size="small" @click="filtersOpen = true" aria-label="Filters" />
    </div>
    <v-divider />

    <v-alert v-if="oversized" type="warning" variant="tonal" closable class="ma-2"
             @click:close="oversized = null">
      {{ oversized.detail }} ({{ oversized.nodeCount }} nodes). Try Dedupe on, a single encounter, or "Confirmed only".
    </v-alert>

    <div ref="container" :style="{ height: height + 'px' }" class="graph-canvas" />

    <EmptyState v-if="!loading && !data?.nodes?.length && !oversized"
                icon="mdi-graph-outline" :title="emptyTitle" />

    <!-- Filter side drawer -->
    <v-navigation-drawer v-model="filtersOpen" location="right" temporary width="320">
      <div class="pa-4 text-subtitle-1 font-weight-bold">Filters</div>
      <v-divider />
      <v-list density="compact">
        <v-list-subheader>Node types</v-list-subheader>
        <v-list-item v-for="t in NODE_TYPE_TOGGLES" :key="t.key" :title="t.label">
          <template #append>
            <v-switch v-model="filters[t.key]" hide-details density="compact" inset />
          </template>
        </v-list-item>
        <v-divider class="my-2" />
        <v-list-subheader>Behavior</v-list-subheader>
        <v-list-item title="Dedupe across encounters">
          <template #append>
            <v-switch v-model="filters.dedupe" hide-details density="compact" inset />
          </template>
        </v-list-item>
        <v-divider class="my-2" />
        <v-list-subheader>Review status</v-list-subheader>
        <v-list-item>
          <v-radio-group v-model="filters.reviewStatus" hide-details density="compact">
            <v-radio value="hide_rejected" label="Hide rejected" />
            <v-radio value="all" label="Show all" />
            <v-radio value="confirmed" label="Confirmed only" />
          </v-radio-group>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <!-- Pick-encounters dialog -->
    <v-dialog v-model="pickerOpen" max-width="480">
      <v-card>
        <div class="d-flex align-center pa-4">
          <v-icon class="mr-2">mdi-calendar-check-outline</v-icon>
          <span class="text-subtitle-1 font-weight-bold">Pick encounters</span>
        </div>
        <v-divider />
        <v-card-text>
          <v-text-field v-model="pickerFilter" prepend-inner-icon="mdi-magnify"
                        density="compact" hide-details placeholder="Filter by date or type" />
          <v-list select-strategy="multiple" v-model:selected="pickedEncounterIds"
                  density="compact" class="mt-2" style="max-height: 320px; overflow-y: auto">
            <v-list-item v-for="e in filteredEncounterList" :key="e.encounterId" :value="e.encounterId"
                         :title="`${e.type} · ${new Date(e.dateTime).toLocaleString()}`"
                         :subtitle="e.department || ''" />
          </v-list>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="pickerOpen = false">Cancel</v-btn>
          <v-btn color="primary" :disabled="!pickedEncounterIds.length" @click="applyPicked">Apply</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Network, DataSet } from 'vis-network/standalone/esm/vis-network'
import { getGraph, listEncounters } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  patientId:    { type: String, required: true },
  scope:        { type: String, default: 'patient' },     // 'patient' | 'encounter' | 'encounters'
  encounterIds: { type: Array, default: () => [] },        // used when scope is encounter/encounters
  height:       { type: Number, default: 620 },
})

const ui = useUiStore()
const container = ref(null)
const data = ref({ nodes: [], edges: [] })
const loading = ref(false)
const oversized = ref(null)
const filtersOpen = ref(false)
const pickerOpen = ref(false)
const pickerFilter = ref('')
const pickedEncounterIds = ref([])
const encounterList = ref([])
let network = null
let abortController = null

const COLORS = {
  Patient: '#1f6feb', Encounter: '#7286d3', Document: '#9c27b0',
  Condition: '#ef6c00', Medication: '#2e7d32', Observation: '#0097a7',
  Plan: '#6d4c41', Allergy: '#c62828', Procedure: '#7b1fa2',
}

const NODE_TYPE_TOGGLES = [
  { key: 'includeEncounters', label: 'Encounters' },
  { key: 'includeDocuments', label: 'Documents' },
]

const scopeChip = ref('all')  // 'all' | 'latest' | 'pick'
const filters = reactive({
  includeEncounters: false,
  includeDocuments: false,
  dedupe: true,
  reviewStatus: 'hide_rejected',
})

const emptyTitle = computed(() =>
  props.scope === 'patient' ? 'No facts to display yet — ingest a note for this patient' :
  'This encounter has no extracted facts')

const filteredEncounterList = computed(() => {
  const q = pickerFilter.value.trim().toLowerCase()
  if (!q) return encounterList.value
  return encounterList.value.filter((e) =>
    (e.type || '').toLowerCase().includes(q) ||
    (e.dateTime || '').toLowerCase().includes(q),
  )
})

function themeColors() {
  const style = getComputedStyle(document.documentElement)
  const onBg = style.getPropertyValue('--v-theme-on-background').trim() || '0,0,0'
  const surface = style.getPropertyValue('--v-theme-surface').trim() || '255,255,255'
  return { label: `rgb(${onBg})`, stroke: `rgb(${surface})` }
}

function shortLabel(n) {
  const d = n.data || {}
  return d.value || d.name || d.description || d.patientId || d.encounterId || n.label
}
function tooltip(n) {
  return `${n.label}\n${JSON.stringify(n.data, null, 2)}`
}

function render() {
  if (!container.value) return
  const { label: labelColor, stroke: strokeColor } = themeColors()
  const nodes = new DataSet((data.value.nodes || []).map((n) => ({
    id: n.id,
    label: shortLabel(n),
    title: tooltip(n),
    color: { background: COLORS[n.label] || '#90a4ae', border: '#37474f' },
    font: { color: labelColor, strokeColor, strokeWidth: 3, size: 12 },
    shape: 'dot',
    size: n.label === 'Patient' ? 24 : 14,
  })))
  const edges = new DataSet((data.value.edges || []).map((e, i) => ({
    id: 'e' + i, from: e.from, to: e.to, label: e.type, arrows: 'to',
    font: { size: 9, color: labelColor, strokeColor, strokeWidth: 3 },
    color: { color: '#9e9e9e', highlight: '#1f6feb' },
    smooth: { type: 'continuous' },
  })))
  if (network) network.destroy()
  network = new Network(container.value, { nodes, edges }, {
    physics: { stabilization: { iterations: 200 }, barnesHut: { springLength: 140 } },
    interaction: { hover: true, tooltipDelay: 100 },
    nodes: { borderWidth: 1.5 },
  })
}

function fit() { network && network.fit({ animation: { duration: 350 } }) }

function resolvedQuery() {
  // Build the query options for getGraph from current scope + filters.
  if (props.scope !== 'patient') {
    return {
      scope: props.scope,
      encounterId: props.encounterIds,
      dedupe: filters.dedupe,
      includeEncounters: filters.includeEncounters || props.scope !== 'patient',
      includeDocuments: filters.includeDocuments,
      reviewStatus: filters.reviewStatus,
    }
  }
  if (scopeChip.value === 'all') {
    return { scope: 'patient', dedupe: filters.dedupe,
             includeEncounters: filters.includeEncounters,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  if (scopeChip.value === 'latest' && encounterList.value.length) {
    return { scope: 'encounter', encounterId: [encounterList.value[0].encounterId],
             dedupe: filters.dedupe,
             includeEncounters: true,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  if (scopeChip.value === 'pick' && pickedEncounterIds.value.length) {
    return { scope: 'encounters', encounterId: pickedEncounterIds.value,
             dedupe: filters.dedupe,
             includeEncounters: true,
             includeDocuments: filters.includeDocuments,
             reviewStatus: filters.reviewStatus }
  }
  return null  // no usable selection yet
}

async function load() {
  // Debounce-cancel previous in-flight.
  if (abortController) abortController.abort()
  abortController = new AbortController()
  oversized.value = null
  loading.value = true
  const opts = resolvedQuery()
  if (!opts) {
    loading.value = false
    data.value = { nodes: [], edges: [] }
    render()
    return
  }
  try {
    data.value = await getGraph(props.patientId, { ...opts, signal: abortController.signal })
    render()
  } catch (err) {
    if (err.name === 'CanceledError' || err.name === 'AbortError') return
    if (err.response?.status === 422) {
      oversized.value = err.response.data?.detail || { detail: 'Graph too large', nodeCount: 0 }
      data.value = { nodes: [], edges: [] }
      render()
      return
    }
    // Other errors are surfaced by the axios interceptor's snackbar.
    data.value = { nodes: [], edges: [] }
    render()
  } finally {
    loading.value = false
  }
}

async function loadEncounters() {
  if (props.scope !== 'patient') return
  try {
    const list = await listEncounters(props.patientId)
    encounterList.value = list || []
  } catch {
    encounterList.value = []
  }
}

function applyPicked() {
  pickerOpen.value = false
  load()
}

// Reactivity: re-fetch when scope-state changes.
watch(scopeChip, (chip) => {
  if (chip === 'pick') {
    pickerOpen.value = true
    return  // wait for Apply
  }
  load()
})
watch(filters, () => load(), { deep: true })
watch(() => ui.theme, () => render())
watch(() => props.encounterIds, () => load())

onMounted(async () => {
  await loadEncounters()
  await load()
})
onBeforeUnmount(() => {
  if (abortController) abortController.abort()
  if (network) network.destroy()
})
</script>

<style scoped>
.graph-canvas {
  background: rgba(127, 127, 127, 0.04);
  border-radius: 0;
}
</style>
