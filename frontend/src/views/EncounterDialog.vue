<template>
  <v-dialog :model-value="!!eid" fullscreen
            transition="dialog-bottom-transition"
            scrollable
            @update:model-value="(v) => !v && $emit('close')">
    <v-card class="d-flex flex-column" style="height: 100vh">
      <v-toolbar color="surface" density="comfortable">
        <v-btn icon="mdi-close" @click="$emit('close')" aria-label="Close" />
        <v-toolbar-title class="text-truncate">
          <span v-if="encounter">
            {{ encounter.type }} · {{ encounter.dateTime ? new Date(encounter.dateTime).toLocaleString() : '' }}
          </span>
          <span v-else>Encounter</span>
        </v-toolbar-title>
        <v-spacer />
        <v-menu>
          <template #activator="{ props: a }">
            <v-btn v-bind="a" class="mr-2" color="primary" variant="tonal"
                   prepend-icon="mdi-text-box-outline" :loading="busy.summary">
              {{ summary ? 'Regenerate summary' : 'Summarize' }}
              <v-icon end>mdi-chevron-down</v-icon>
            </v-btn>
          </template>
          <v-list density="compact">
            <v-list-item title="Discharge summary" prepend-icon="mdi-hospital-box-outline"
                         @click="loadSummary('discharge_summary')" />
            <v-list-item title="Detailed" prepend-icon="mdi-text"
                         @click="loadSummary('detailed')" />
            <v-list-item title="Brief" prepend-icon="mdi-text-short"
                         @click="loadSummary('brief')" />
          </v-list>
        </v-menu>
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-tag-text-outline"
               :loading="busy.coding" @click="loadCoding">
          {{ codingResp ? 'Regenerate coding' : 'Coding' }}
        </v-btn>
      </v-toolbar>

      <v-tabs v-model="tab" color="primary" density="comfortable">
        <v-tab value="detail" prepend-icon="mdi-text-box-outline">Detail</v-tab>
        <v-tab value="graph" prepend-icon="mdi-graph-outline">Graph</v-tab>
      </v-tabs>
      <v-divider />

      <v-window v-model="tab" class="flex-grow-1 overflow-y-auto">
        <v-window-item value="detail">
          <div v-if="loading" class="d-flex justify-center pa-8">
            <v-progress-circular indeterminate />
          </div>
          <v-alert v-else-if="error" type="error" variant="tonal" class="ma-4">
            {{ error }}
          </v-alert>
          <div v-else class="pa-4">
            <v-row>
              <v-col cols="12" md="8">
                <SummaryCard :value="summary" />
                <CodingCard :value="codingResp" />
                <v-card v-if="docs.length" class="mt-4">
                  <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
                  <v-divider />
                  <v-list density="compact" nav>
                    <v-list-item v-for="d in docs" :key="d.documentId"
                                 :title="d.documentId"
                                 :subtitle="`v${d.version} · ${d.format}`" />
                  </v-list>
                </v-card>
              </v-col>
              <v-col cols="12" md="4">
                <v-card>
                  <SectionHeader title="Background" icon="mdi-medical-bag" />
                  <v-divider />
                  <v-list density="compact">
                    <v-list-subheader>Chronic problems</v-list-subheader>
                    <v-list-item v-for="p in background.chronicProblems" :key="`bp-${p.id}`" :title="p.value" />
                    <EmptyState v-if="!background.chronicProblems.length" icon="mdi-medical-bag" title="None recorded" />
                    <v-divider class="my-1" />
                    <v-list-subheader>Home medications</v-list-subheader>
                    <v-list-item v-for="m in background.homeMedications" :key="`bm-${m.id}`" :title="m.value" />
                    <EmptyState v-if="!background.homeMedications.length" icon="mdi-pill" title="None recorded" />
                    <v-divider class="my-1" />
                    <v-list-subheader>Known allergies</v-list-subheader>
                    <v-list-item v-for="a in background.knownAllergies" :key="`ba-${a.id}`" :title="a.value" />
                    <EmptyState v-if="!background.knownAllergies.length" icon="mdi-allergy" title="None recorded" />
                  </v-list>
                </v-card>
              </v-col>
            </v-row>
          </div>
        </v-window-item>

        <v-window-item value="graph" class="h-100">
          <GraphView scope="encounter" :patient-id="patientId" :encounter-ids="[eid]" :height="640" />
        </v-window-item>
      </v-window>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getLatestEncounterSummary, getLatestEncounterCoding,
  summarizeEncounter, suggestEncounterCoding, listEncounters,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import GraphView from '../components/GraphView.vue'

const props = defineProps({
  patientId: { type: String, required: true },
  eid:       { type: String, required: true },
})
defineEmits(['close'])

const route = useRoute()
const ui = useUiStore()

const tab = ref('detail')
const loading = ref(true)
const error = ref('')
const encounter = ref(null)
const background = ref({ chronicProblems: [], homeMedications: [], knownAllergies: [] })
const docs = ref([])
const summary = ref(null)
const codingResp = ref(null)
const busy = reactive({ summary: false, coding: false })

function extractBackground(evidence) {
  if (!evidence || typeof evidence !== 'object' || !evidence.background) {
    return { chronicProblems: [], homeMedications: [], knownAllergies: [] }
  }
  return {
    chronicProblems: evidence.background.chronicProblems || [],
    homeMedications: evidence.background.homeMedications || [],
    knownAllergies: evidence.background.knownAllergies || [],
  }
}

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const [sum, cod, list] = await Promise.all([
      getLatestEncounterSummary(props.patientId, props.eid).catch(() => null),
      getLatestEncounterCoding(props.patientId, props.eid).catch(() => null),
      listEncounters(props.patientId).catch(() => []),
    ])
    summary.value = sum
    codingResp.value = cod?.payload || cod || null
    background.value = extractBackground(sum?.evidence)
    const match = (list || []).find((e) => e.encounterId === props.eid)
    if (!match) {
      error.value = 'Encounter not found for this patient.'
    } else {
      encounter.value = match
    }
  } finally {
    loading.value = false
  }
}

async function loadSummary(type) {
  busy.summary = true
  try {
    summary.value = await summarizeEncounter(props.patientId, props.eid, { type, includeEvidence: false })
    ui.success('Summary ready')
  } catch {
    ui.error('Failed to generate summary')
  } finally {
    busy.summary = false
  }
}

async function loadCoding() {
  busy.coding = true
  try {
    codingResp.value = await suggestEncounterCoding(props.patientId, props.eid, {
      standards: ['ICD10', 'SNOMEDCT'], includeEvidence: false,
    })
    ui.success('Coding suggestion ready')
  } catch {
    ui.error('Failed to suggest coding')
  } finally {
    busy.coding = false
  }
}

onMounted(async () => {
  await fetchAll()
  if (route.query.action === 'summary' && !summary.value && !busy.summary) {
    loadSummary(encounter.value?.type === 'admission' ? 'discharge_summary' : 'detailed')
  } else if (route.query.action === 'coding' && !codingResp.value && !busy.coding) {
    loadCoding()
  }
})

watch(() => props.eid, fetchAll)
</script>
