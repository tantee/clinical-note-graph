<template>
  <v-card>
    <SectionHeader title="Patient knowledge graph" icon="mdi-graph-outline">
      <template #actions>
        <v-btn size="small" variant="text" prepend-icon="mdi-fit-to-page-outline" @click="fit">Fit</v-btn>
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="render">Refresh</v-btn>
      </template>
    </SectionHeader>
    <v-divider />
    <div class="graph-wrapper">
      <div ref="container" :style="{ height: height + 'px' }" class="graph-canvas" />
      <div class="graph-legend">
        <div v-for="[label, color] in legend" :key="label" class="legend-row">
          <span class="legend-dot" :style="{ background: color }" />
          <span class="text-caption">{{ label }}</span>
        </div>
      </div>
    </div>
    <EmptyState
      v-if="!data?.nodes?.length"
      icon="mdi-graph-outline"
      title="No graph data"
      hint="Ingest an EMR document for this patient to populate the graph."
    />
  </v-card>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Network, DataSet } from 'vis-network/standalone/esm/vis-network'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from './SectionHeader.vue'
import EmptyState from './EmptyState.vue'

const ui = useUiStore()

const props = defineProps({
  data: { type: Object, default: () => ({ nodes: [], edges: [] }) },
  height: { type: Number, default: 620 },
})

const container = ref(null)
let network = null

const COLORS = {
  Patient: '#1f6feb',
  Encounter: '#7286d3',
  Document: '#9c27b0',
  Condition: '#ef6c00',
  Medication: '#2e7d32',
  Observation: '#0097a7',
  Plan: '#6d4c41',
  Allergy: '#c62828',
  Procedure: '#7b1fa2',
}

const legend = Object.entries(COLORS)

function shortLabel(n) {
  const d = n.data || {}
  return d.value || d.name || d.description || d.patientId || d.encounterId || n.label
}
function tooltip(n) {
  return `${n.label}\n${JSON.stringify(n.data, null, 2)}`
}

function themeColors() {
  // Read live theme tokens so labels stay legible after dark/light toggle.
  const style = getComputedStyle(document.documentElement)
  const onBg = style.getPropertyValue('--v-theme-on-background').trim() || '0,0,0'
  const surface = style.getPropertyValue('--v-theme-surface').trim() || '255,255,255'
  return { label: `rgb(${onBg})`, stroke: `rgb(${surface})` }
}

function render() {
  if (!container.value || !props.data) return
  const { label: labelColor, stroke: strokeColor } = themeColors()
  const nodes = new DataSet((props.data.nodes || []).map((n) => ({
    id: n.id,
    label: shortLabel(n),
    title: tooltip(n),
    color: { background: COLORS[n.label] || '#90a4ae', border: '#37474f' },
    font: { color: labelColor, strokeColor, strokeWidth: 3, size: 12 },
    shape: 'dot',
    size: n.label === 'Patient' ? 24 : 14,
  })))
  const edges = new DataSet((props.data.edges || []).map((e, i) => ({
    id: 'e' + i, from: e.from, to: e.to, label: e.type, arrows: 'to',
    font: { size: 9, color: labelColor, strokeColor, strokeWidth: 3 },
    color: { color: '#9e9e9e', highlight: '#1f6feb' },
    smooth: { type: 'continuous' },
  })))
  if (network) network.destroy()
  network = new Network(container.value, { nodes, edges }, {
    physics: { stabilization: true, barnesHut: { springLength: 140 } },
    interaction: { hover: true, tooltipDelay: 100 },
    nodes: { borderWidth: 1.5 },
  })
}

function fit() {
  if (network) network.fit({ animation: { duration: 350 } })
}

watch(() => props.data, render, { deep: true })
watch(() => ui.theme, () => render())
onMounted(render)
onBeforeUnmount(() => network && network.destroy())
</script>

<style scoped>
.graph-wrapper { position: relative; }
.graph-canvas { background: rgba(127,127,127,0.04); border-radius: 0; }
.graph-legend {
  position: absolute; top: 12px; right: 12px;
  background: rgb(var(--v-theme-surface) / 0.9);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-on-surface) / 0.12);
  backdrop-filter: blur(4px);
  border-radius: 8px; padding: 6px 10px; font-size: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.legend-row { display: flex; align-items: center; gap: 6px; line-height: 1.5; }
.legend-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
</style>
