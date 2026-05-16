<template>
  <v-card>
    <SectionHeader title="Ingest job" icon="mdi-clock-outline" />
    <v-divider />
    <v-card-text>
      <v-chip size="x-small" class="mr-2" :color="statusColor">{{ status }}</v-chip>
      <span class="text-caption text-grey-darken-1">Job {{ jobId }}</span>

      <v-progress-linear v-if="running" indeterminate class="mt-3" />

      <div v-if="error" class="text-error mt-3">{{ error }}</div>

      <div class="stages mt-3">
        <span v-for="s in STAGES" :key="s" :class="['stage', stageClass(s)]">
          <v-icon size="14" v-if="hasStage(s)">mdi-check</v-icon>
          <v-icon size="14" v-else>mdi-circle-outline</v-icon>
          {{ s.replace('stage_', '') }}
        </span>
      </div>

      <div v-if="metrics" class="text-caption mt-3">
        Tokens: {{ formatTokens(metrics.tokens) }} ·
        Cost: {{ formatUSD(metrics.cost) }} ·
        Latency: {{ metrics.latency_ms ?? '–' }} ms
      </div>

      <div class="actions mt-3">
        <v-btn v-if="status === 'failed'" size="small" color="primary" @click="$emit('retry')">Retry</v-btn>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import SectionHeader from './SectionHeader.vue'
import { getJob } from '../api/client.js'
import { formatTokens, formatUSD } from '../utils/format.js'

const props = defineProps({
  jobId: { type: String, required: true },
  intervalMs: { type: Number, default: 1500 },
})
const emit = defineEmits(['done', 'failed', 'retry'])

const STAGES = ['stage_persisted', 'stage_ai_extract', 'stage_facts', 'stage_graph_and_markdown', 'stage_embed']

const status = ref('pending')
const error = ref('')
const progress = ref({})
const metrics = ref(null)
let timer = null

const running = computed(() => ['queued', 'pending', 'running'].includes(status.value))
const statusColor = computed(() => ({
  completed: 'success', failed: 'error', running: 'info', pending: 'warning', queued: 'warning',
})[status.value] || 'grey')

function hasStage(s) { return Boolean(progress.value?.[s]) }
function stageClass(s) { return hasStage(s) ? 'done' : 'todo' }

async function tick() {
  try {
    const j = await getJob(props.jobId)
    status.value = j.status || 'pending'
    progress.value = j.progress || {}
    error.value = j.error || ''
    const tok = progress.value?.stage_ai_extract?.prompt_tokens
    const cost = progress.value?.stage_ai_extract?.cost_usd
    if (tok || cost) {
      metrics.value = {
        tokens: tok,
        cost,
        latency_ms: progress.value?.stage_ai_extract?.latency_ms,
      }
    }

    if (status.value === 'completed') {
      stop()
      emit('done', j.result || {})
    } else if (status.value === 'failed' || status.value === 'cancelled') {
      stop()
      emit('failed', j.error)
    }
  } catch (_e) {
    // axios interceptor already toasted; keep polling unless the route 404s
  }
}

function start() {
  tick()
  timer = setInterval(tick, props.intervalMs)
}
function stop() { if (timer) { clearInterval(timer); timer = null } }

onMounted(start)
onBeforeUnmount(stop)
</script>

<style scoped>
.stages { display: flex; flex-wrap: wrap; gap: 8px; }
.stage  { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.stage.done { color: rgb(var(--v-theme-success)); }
.stage.todo { opacity: 0.5; }
</style>
