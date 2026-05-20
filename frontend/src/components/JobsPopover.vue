<template>
  <v-menu v-model="open" location="bottom end" :close-on-content-click="false" max-width="420">
    <!-- Match the same `prepend-icon` + label pattern the other app-bar
         buttons use (Patients / Ingest / Config / etc.). Earlier
         iterations tried `v-btn :icon=` plus a wrapping `v-badge`, but
         the icon-only mode + floating badge combination rendered with
         zero visible width in this Vuetify build. The straightforward
         text-button form just works, and the active count goes inline
         as the button's label so the user sees "3" right next to the
         clock when there's work in flight. -->
    <template #activator="{ props: a }">
      <v-btn
        v-bind="a"
        variant="text"
        :prepend-icon="hasActive ? 'mdi-progress-clock' : 'mdi-progress-clock-outline'"
        :color="hasActive ? 'warning' : ''"
        aria-label="Active jobs"
      >
        <span v-if="hasActive">{{ active.length }}</span>
        <span v-else class="d-sr-only">No active jobs</span>
      </v-btn>
    </template>

    <v-card width="420">
      <v-card-title class="text-subtitle-2 d-flex align-center">
        <v-icon class="mr-2" color="primary">mdi-progress-clock</v-icon>
        Active jobs
        <v-spacer />
        <v-chip size="x-small" variant="tonal">{{ active.length }} active</v-chip>
      </v-card-title>
      <v-divider />

      <!-- Empty state -->
      <div v-if="!loading && active.length === 0 && failed.length === 0" class="pa-4 text-center text-grey-darken-1">
        <v-icon color="grey-lighten-1" size="48" class="mb-2">mdi-check-circle-outline</v-icon>
        <div class="text-body-2">No active jobs.</div>
        <v-btn variant="text" size="x-small" :to="{ name: 'debug' }" @click="open = false" class="mt-2">
          View all
        </v-btn>
      </div>

      <!-- Active section -->
      <v-list v-if="active.length" density="compact" lines="three">
        <v-list-item
          v-for="j in active"
          :key="j.job_id"
          :to="j.patient_id ? { name: 'patient', params: { id: j.patient_id } } : null"
          @click="open = false"
        >
          <template #prepend>
            <v-icon :color="j.status === 'running' ? 'primary' : 'grey-darken-1'">
              {{ j.status === 'running' ? 'mdi-loading mdi-spin' : 'mdi-clock-outline' }}
            </v-icon>
          </template>
          <v-list-item-title class="text-body-2">
            {{ jobLabel(j.type) }}
            <span v-if="j.patient_id" class="text-grey-darken-1">· {{ j.patient_id }}</span>
          </v-list-item-title>
          <v-list-item-subtitle>
            <v-chip size="x-small" class="mr-1" variant="tonal" :color="j.status === 'running' ? 'primary' : 'default'">
              {{ j.status }}
            </v-chip>
            <v-chip v-if="lastStage(j)" size="x-small" class="mr-1" variant="outlined">
              {{ lastStage(j) }}
            </v-chip>
            <span class="text-caption text-grey-darken-1">{{ elapsed(j) }}</span>
          </v-list-item-subtitle>
          <template #append>
            <v-icon size="small" color="grey">mdi-arrow-right</v-icon>
          </template>
        </v-list-item>
      </v-list>

      <!-- Recently failed section — separate, with re-queue affordance -->
      <template v-if="failed.length">
        <v-divider />
        <div class="px-4 py-2 d-flex align-center">
          <v-icon size="small" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          <span class="text-caption">Recently failed ({{ failed.length }})</span>
        </div>
        <v-list density="compact" lines="two">
          <v-list-item
            v-for="j in failed"
            :key="`f-${j.job_id}`"
            :to="j.patient_id ? { name: 'patient', params: { id: j.patient_id } } : null"
            @click="open = false"
          >
            <template #prepend>
              <v-icon color="error">mdi-close-circle-outline</v-icon>
            </template>
            <v-list-item-title class="text-body-2">
              {{ jobLabel(j.type) }}
              <span v-if="j.patient_id" class="text-grey-darken-1">· {{ j.patient_id }}</span>
            </v-list-item-title>
            <v-list-item-subtitle>
              <v-chip size="x-small" class="mr-1" color="error" variant="tonal">failed</v-chip>
              <span class="text-caption text-grey-darken-1">{{ elapsed(j) }}</span>
            </v-list-item-subtitle>
            <template #append>
              <v-btn
                size="x-small" variant="text" prepend-icon="mdi-restart"
                :loading="requeuing[j.job_id]"
                @click.stop.prevent="onRequeue(j)"
              >Re-queue</v-btn>
            </template>
          </v-list-item>
        </v-list>
      </template>

      <v-divider />
      <v-card-actions class="px-3">
        <span v-if="loading" class="text-caption text-grey-darken-1">refreshing…</span>
        <span v-else class="text-caption text-grey-darken-1">
          updated {{ updatedAt ? relativeTime(updatedAt) : 'just now' }}
        </span>
        <v-spacer />
        <v-btn size="x-small" variant="text" :to="{ name: 'debug' }" @click="open = false">
          View all
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-menu>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listJobs, requeueJob } from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import { JOB_TYPE_LABELS } from '../constants/jobs.js'

const ui = useUiStore()

const open = ref(false)
const loading = ref(false)
const active = ref([])
const failed = ref([])
const updatedAt = ref(null)
const requeuing = ref({})

// Smart polling. Three tiers, designed to keep the badge fresh without
// burning requests:
//   - 5s while the popover is open or there are active jobs.
//   - 30s while it's closed and the last poll showed no activity.
//   - Stops entirely after 60s of zero-activity polls (woken back up
//     when the user clicks the icon).
const POLL_FAST_MS = 5_000
const POLL_SLOW_MS = 30_000
const IDLE_STOP_AFTER_MS = 60_000

let timer = null
let lastActivityAt = Date.now()

const hasActive = computed(() => active.value.length > 0)

function jobLabel(type) {
  return JOB_TYPE_LABELS[type] || type
}

function lastStage(j) {
  const p = j.progress || {}
  const keys = Object.keys(p)
  if (!keys.length) return null
  // Progress keys arrive as stage_* names; show the most recent stage
  // (last-set wins — the queue writer mutates the dict in order).
  return keys[keys.length - 1]
}

function elapsed(j) {
  const startedRaw = j.started_at || j.created_at
  if (!startedRaw) return ''
  const started = new Date(startedRaw).getTime()
  const end = j.finished_at ? new Date(j.finished_at).getTime() : Date.now()
  const s = Math.max(0, Math.round((end - started) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  return r ? `${m}m ${r}s` : `${m}m`
}

function relativeTime(ts) {
  const s = Math.round((Date.now() - ts) / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  return `${Math.floor(s / 60)}m ago`
}

async function fetchJobs() {
  if (loading.value) return
  loading.value = true
  try {
    // pending + running in one call (backend supports comma-separated).
    const [a, f] = await Promise.all([
      listJobs({ status: 'pending,running', limit: 25 }),
      listJobs({ status: 'failed', limit: 5 }),
    ])
    active.value = a || []
    failed.value = f || []
    updatedAt.value = Date.now()
    if (a?.length) lastActivityAt = Date.now()
  } catch {
    // Snackbar already fired by the global axios interceptor.
  } finally {
    loading.value = false
  }
}

function scheduleNext() {
  if (timer) clearTimeout(timer)
  // Stop polling entirely when the popover is closed AND there's been
  // no activity for a while. Wakes back up when the user clicks the
  // icon (the watch on `open` re-arms the timer).
  if (!open.value && Date.now() - lastActivityAt > IDLE_STOP_AFTER_MS) {
    timer = null
    return
  }
  const delay = open.value || hasActive.value ? POLL_FAST_MS : POLL_SLOW_MS
  timer = setTimeout(async () => {
    await fetchJobs()
    scheduleNext()
  }, delay)
}

watch(open, (now) => {
  if (now) {
    lastActivityAt = Date.now()  // user is asking, treat as activity
    fetchJobs().then(scheduleNext)
  } else {
    scheduleNext()
  }
})

async function onRequeue(j) {
  requeuing.value = { ...requeuing.value, [j.job_id]: true }
  try {
    await requeueJob(j.job_id)
    ui.success('Job re-queued')
    await fetchJobs()
  } finally {
    requeuing.value = { ...requeuing.value, [j.job_id]: false }
  }
}

onMounted(async () => {
  await fetchJobs()
  scheduleNext()
})
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

defineExpose({ refresh: fetchJobs })
</script>
