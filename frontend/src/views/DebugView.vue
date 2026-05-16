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
        <v-alert type="info" variant="tonal">AI calls view — coming in Task 16.</v-alert>
      </v-window-item>
      <v-window-item value="jobs">
        <v-alert type="info" variant="tonal">Jobs view — coming in Task 17.</v-alert>
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getDebugSummary, getDebugByModel, getDebugByDay } from '../api/client.js'
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

onMounted(reload)
</script>
