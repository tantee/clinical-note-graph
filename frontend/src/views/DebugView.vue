<template>
  <div>
    <div class="d-flex align-center mb-4">
      <div>
        <h1 class="text-h5 font-weight-bold mb-1">Debug</h1>
        <div class="text-body-2 text-grey-darken-1">Token usage, cost tracking, and job timeline.</div>
      </div>
      <v-spacer />
      <v-select
        v-model="rangeKey" :items="rangeOptions" item-title="label" item-value="key"
        label="Range" density="compact" hide-details style="max-width: 200px"
        @update:model-value="reload"
      />
      <v-btn variant="text" prepend-icon="mdi-refresh" @click="reload">Refresh</v-btn>
    </div>

    <v-row>
      <v-col cols="6" md="3"><KpiCard label="Total spend" :value="formatUSD(summary.total_cost_usd)" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="AI calls"    :value="summary.total_calls" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="Avg latency" :value="`${Math.round(summary.avg_latency_ms || 0)} ms`" /></v-col>
      <v-col cols="6" md="3"><KpiCard label="Failures"    :value="`${summary.failures || 0}`" :color="summary.failures ? 'error' : 'success'" /></v-col>
    </v-row>

    <v-tabs v-model="tab" color="primary" density="comfortable" class="mt-4">
      <v-tab value="overview" prepend-icon="mdi-view-dashboard-outline">Overview</v-tab>
      <v-tab value="calls"    prepend-icon="mdi-text-box-search-outline">AI calls</v-tab>
      <v-tab value="jobs"     prepend-icon="mdi-clock-outline">Jobs</v-tab>
    </v-tabs>

    <v-window v-model="tab" class="mt-4">
      <v-window-item value="overview" eager>
        <v-row>
          <v-col cols="12" md="7">
            <v-card>
              <SectionHeader title="Spend by day" icon="mdi-chart-bar" />
              <v-divider />
              <v-card-text>
                <BarChart :data="byDayChart" />
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" md="5">
            <v-card>
              <SectionHeader title="By model" icon="mdi-tag-outline" />
              <v-divider />
              <v-data-table density="comfortable" :headers="modelHeaders" :items="byModel">
                <template #item.cost_usd="{ item }">{{ formatUSD(item.cost_usd) }}</template>
                <template #item.prompt_tokens="{ item }">{{ formatTokens(item.prompt_tokens) }}</template>
              </v-data-table>
            </v-card>
          </v-col>
        </v-row>
      </v-window-item>

      <v-window-item value="calls">
        <v-card>
          <SectionHeader title="AI calls" icon="mdi-text-box-search-outline">
            <template #actions>
              <v-text-field
                v-model="callsQ"
                density="compact"
                placeholder="Search error / model"
                prepend-inner-icon="mdi-magnify"
                hide-details
                style="max-width: 280px"
              />
              <v-select
                v-model="callsStatus"
                :items="['', 'ok', 'failed']"
                label="Status"
                density="compact"
                hide-details
                style="max-width: 130px; margin-left: 8px;"
                clearable
              />
              <v-btn variant="text" prepend-icon="mdi-download" :href="csvUrl" target="_blank">CSV</v-btn>
            </template>
          </SectionHeader>
          <v-divider />
          <v-data-table
            density="comfortable"
            :headers="callsHeaders"
            :items="callsRows"
            :loading="callsLoading"
            @click:row="onCallClick"
          >
            <template #item.cost_usd="{ item }">{{ formatUSD(item.cost_usd) }}</template>
            <template #item.prompt_tokens="{ item }">{{ formatTokens(item.prompt_tokens) }}</template>
            <template #item.completion_tokens="{ item }">{{ formatTokens(item.completion_tokens) }}</template>
            <template #item.error="{ item }">
              <v-chip v-if="item.error" size="x-small" color="error" variant="tonal">err</v-chip>
              <v-chip v-else size="x-small" color="success" variant="tonal">ok</v-chip>
            </template>
          </v-data-table>
        </v-card>

        <v-navigation-drawer v-model="callDrawer" location="right" width="600" temporary>
          <v-card flat>
            <SectionHeader title="AI call detail" icon="mdi-information-outline" />
            <v-divider />
            <v-card-text v-if="selectedCall">
              <pre class="cng-raw">{{ JSON.stringify(selectedCall, null, 2) }}</pre>
            </v-card-text>
          </v-card>
        </v-navigation-drawer>
      </v-window-item>
      <v-window-item value="jobs">
        <v-card>
          <SectionHeader title="Jobs" icon="mdi-clock-outline">
            <template #actions>
              <v-select
                v-model="jobsStatus"
                :items="['', 'pending', 'running', 'completed', 'failed']"
                label="Status"
                density="compact"
                hide-details
                style="max-width: 160px"
                clearable
              />
            </template>
          </SectionHeader>
          <v-divider />
          <v-data-table :headers="jobsHeaders" :items="jobsRows" :loading="jobsLoading">
            <template #item.status="{ item }">
              <v-chip
                size="x-small"
                :color="{ completed: 'success', failed: 'error', running: 'info', pending: 'warning' }[item.status] || 'grey'"
                variant="tonal"
              >
                {{ item.status }}
              </v-chip>
            </template>
            <template #item.error="{ item }">
              <!-- Truncated inline; full text on hover via title attr,
                   plus a "Details" button that opens a drawer with the
                   complete error + progress trace for diagnosis. -->
              <div v-if="item.error" class="d-flex align-center" style="max-width: 360px;">
                <span class="text-caption text-error text-truncate" :title="item.error">
                  {{ item.error }}
                </span>
                <v-btn
                  size="x-small" variant="text" class="ml-1" icon="mdi-information-outline"
                  aria-label="Show failure detail"
                  @click.stop="openJobDetail(item)"
                />
              </div>
              <span v-else class="text-caption text-grey-darken-1">—</span>
            </template>
            <template #item.actions="{ item }">
              <v-btn
                v-if="item.status === 'failed'"
                size="x-small"
                color="primary"
                @click="requeue(item)"
              >
                Re-queue
              </v-btn>
            </template>
          </v-data-table>
        </v-card>

        <v-navigation-drawer v-model="jobDrawer" location="right" width="640" temporary>
          <v-card flat>
            <SectionHeader title="Job failure detail" icon="mdi-alert-circle-outline" />
            <v-divider />
            <v-card-text v-if="selectedJob">
              <div class="mb-2">
                <v-chip size="x-small" color="error" variant="tonal" class="mr-1">{{ selectedJob.status }}</v-chip>
                <span class="text-caption">{{ selectedJob.type }} · {{ selectedJob.job_id }}</span>
              </div>
              <div v-if="selectedJob.patient_id" class="text-caption text-grey-darken-1 mb-2">
                Patient: {{ selectedJob.patient_id }}
              </div>
              <v-alert v-if="selectedJob.error" type="error" variant="tonal" density="compact" class="mb-3">
                <pre class="cng-raw" style="white-space: pre-wrap;">{{ selectedJob.error }}</pre>
              </v-alert>
              <div class="text-caption text-grey-darken-1 mb-1">Progress trace</div>
              <pre class="cng-raw">{{ JSON.stringify(selectedJob.progress || {}, null, 2) }}</pre>
            </v-card-text>
          </v-card>
        </v-navigation-drawer>
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getDebugSummary, getDebugByModel, getDebugByDay, listAiCalls, getAiCall, listJobs, requeueJob } from '../api/client.js'
import { formatTokens, formatUSD } from '../utils/format.js'
import SectionHeader from '../components/SectionHeader.vue'
import BarChart from '../components/BarChart.vue'
import KpiCard from '../components/KpiCard.vue'

const tab = ref('overview')
const rangeKey = ref('7d')
const rangeOptions = [
  { key: '24h', label: 'Last 24 hours' },
  { key: '7d',  label: 'Last 7 days' },
  { key: '30d', label: 'Last 30 days' },
  { key: '90d', label: 'Last 90 days' },
]

const summary = ref({ total_cost_usd: 0, total_calls: 0, avg_latency_ms: 0, failures: 0, total_tokens: 0 })
const byModel = ref([])
const byDay = ref([])

const modelHeaders = [
  { title: 'Model',     key: 'model' },
  { title: 'Calls',     key: 'calls', align: 'end' },
  { title: 'Prompt tk', key: 'prompt_tokens', align: 'end' },
  { title: 'Cost',      key: 'cost_usd', align: 'end' },
]

function rangeParams() {
  const map = { '24h': 1, '7d': 7, '30d': 30, '90d': 90 }
  const days = map[rangeKey.value] || 7
  const end = new Date()
  const start = new Date(Date.now() - days * 86400_000)
  return { start: start.toISOString(), end: end.toISOString() }
}

async function reload() {
  const p = rangeParams()
  const [s, m, d] = await Promise.all([getDebugSummary(p), getDebugByModel(p), getDebugByDay(p)])
  summary.value = s || summary.value
  byModel.value = m || []
  byDay.value = d || []
}

const byDayChart = computed(() => {
  const days = [...new Set(byDay.value.map(r => String(r.day).slice(0, 10)))]
  const types = ['extract', 'summary', 'coding', 'embed']
  return {
    labels: days,
    datasets: types.map(t => ({
      label: t,
      data: days.map(d => Number(byDay.value.find(r => String(r.day).slice(0, 10) === d && r.call_type === t)?.cost_usd || 0)),
      stack: 'cost',
    })),
  }
})

const callsHeaders = [
  { title: 'Time',     key: 'created_at' },
  { title: 'Type',     key: 'call_type' },
  { title: 'Model',    key: 'model' },
  { title: 'Prompt',   key: 'prompt_tokens', align: 'end' },
  { title: 'Compl.',   key: 'completion_tokens', align: 'end' },
  { title: 'Latency',  key: 'latency_ms', align: 'end' },
  { title: 'Cost',     key: 'cost_usd', align: 'end' },
  { title: 'Status',   key: 'error' },
]

const callsRows = ref([])
const callsLoading = ref(false)
const callsQ = ref('')
const callsStatus = ref('')
const callDrawer = ref(false)
const selectedCall = ref(null)

const csvUrl = computed(() => {
  const p = new URLSearchParams(rangeParams())
  if (callsQ.value) p.set('q', callsQ.value)
  if (callsStatus.value) p.set('status', callsStatus.value)
  return (import.meta.env.VITE_API_BASE || '') + '/api/debug/ai-calls.csv?' + p.toString()
})

async function loadCalls() {
  callsLoading.value = true
  try {
    callsRows.value = await listAiCalls({
      ...rangeParams(),
      q: callsQ.value || undefined,
      status: callsStatus.value || undefined,
      limit: 200,
    })
  } finally { callsLoading.value = false }
}

async function onCallClick(_, ctx) {
  const item = ctx?.item
  if (!item?.id) return
  selectedCall.value = await getAiCall(item.id)
  callDrawer.value = true
}

const jobsHeaders = [
  { title: 'Created',  key: 'created_at' },
  { title: 'Type',     key: 'type' },
  { title: 'Patient',  key: 'patient_id' },
  { title: 'Status',   key: 'status' },
  { title: 'Attempts', key: 'attempts', align: 'end' },
  { title: 'Error',    key: 'error', sortable: false },
  { title: '',         key: 'actions', sortable: false, align: 'end' },
]

const jobsRows = ref([])
const jobsLoading = ref(false)
const jobsStatus = ref('')
const jobDrawer = ref(false)
const selectedJob = ref(null)

function openJobDetail(item) {
  selectedJob.value = item
  jobDrawer.value = true
}

async function loadJobs() {
  jobsLoading.value = true
  try {
    jobsRows.value = await listJobs({ status: jobsStatus.value || undefined, limit: 100 })
  } finally {
    jobsLoading.value = false
  }
}

async function requeue(job) {
  await requeueJob(job.job_id)
  await loadJobs()
}

watch(tab, (t) => {
  if (t === 'calls') loadCalls()
  if (t === 'jobs')  loadJobs()
})
watch([callsQ, callsStatus], loadCalls)
watch(jobsStatus, loadJobs)

onMounted(reload)
</script>
