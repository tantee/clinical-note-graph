<template>
  <div v-if="loading" class="d-flex justify-center pa-8"><v-progress-circular indeterminate /></div>
  <v-alert v-else-if="error" type="error" variant="tonal">{{ error }}</v-alert>

  <div v-else>
    <div class="d-flex align-center mb-4 flex-wrap">
      <v-btn icon="mdi-arrow-left" variant="text" :to="{ name: 'patient', params: { id } }" aria-label="Back to patient" />
      <div class="ml-2">
        <h1 class="text-h5 font-weight-bold mb-0">
          {{ encounter?.type || 'Encounter' }}
        </h1>
        <div class="text-body-2 text-grey-darken-1">
          {{ encounter?.dateTime ? new Date(encounter.dateTime).toLocaleString() : '' }}
          <span v-if="encounter?.department" class="ml-2">· {{ encounter.department }}</span>
          <span v-if="encounter?.provider" class="ml-2">· {{ encounter.provider }}</span>
        </div>
      </div>
      <v-spacer />
      <v-menu offset-y>
        <template #activator="{ props: a }">
          <v-btn v-bind="a" class="mr-2" color="primary" variant="tonal"
                 prepend-icon="mdi-text-box-outline" :loading="busy.summary">
            {{ summary ? 'Regenerate summary' : 'Summarize' }}
            <v-icon end>mdi-chevron-down</v-icon>
          </v-btn>
        </template>
        <v-list density="compact">
          <v-list-item :title="`Discharge summary${defaultIsDischarge ? ' (default)' : ''}`"
                       prepend-icon="mdi-hospital-box-outline"
                       @click="loadSummary('discharge_summary')" />
          <v-list-item title="Detailed" prepend-icon="mdi-text"
                       @click="loadSummary('detailed')" />
          <v-list-item title="Brief" prepend-icon="mdi-text-short"
                       @click="loadSummary('brief')" />
        </v-list>
      </v-menu>
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-medical-bag-outline"
             :loading="busy.coding" @click="loadCoding">
        {{ codingResp ? 'Regenerate coding' : 'Coding' }}
      </v-btn>
    </div>

    <v-row>
      <v-col cols="12" md="8">
        <SummaryCard :value="summary" />
        <CodingCard :value="codingResp" />

        <v-card v-if="docs.length" class="mt-4">
          <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
          <v-divider />
          <v-list density="compact" nav>
            <v-list-item
              v-for="d in docs"
              :key="d.documentId"
              :title="d.documentId"
              :subtitle="`v${d.version} · ${d.format}`"
            />
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
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  getLatestEncounterSummary, getLatestEncounterCoding,
  summarizeEncounter, suggestEncounterCoding,
  listEncounters,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const props = defineProps({
  id:  { type: String, required: true },  // patient HN
  eid: { type: String, required: true },  // encounter id
})
const route = useRoute()
const ui = useUiStore()

const loading = ref(true)
const error = ref('')
const encounter = ref(null)
const background = ref({ chronicProblems: [], homeMedications: [], knownAllergies: [] })
const docs = ref([])
const summary = ref(null)
const codingResp = ref(null)
const busy = reactive({ summary: false, coding: false })

const ADMISSION_TYPES = new Set(['admission', 'discharge_summary', 'admission_note'])
const defaultIsDischarge = computed(() => ADMISSION_TYPES.has(encounter.value?.type))

function extractBackground(evidence) {
  // Populate background from saved evidence when present.
  // The evidence object from the AI store has a 'background' section.
  if (!evidence || typeof evidence !== 'object') {
    return { chronicProblems: [], homeMedications: [], knownAllergies: [] }
  }
  const bg = evidence.background || {}
  return {
    chronicProblems: Array.isArray(bg.problems) ? bg.problems : [],
    homeMedications: Array.isArray(bg.medications) ? bg.medications : [],
    knownAllergies: Array.isArray(bg.allergies) ? bg.allergies : [],
  }
}

async function fetchEncounter() {
  try {
    const [sum, cod] = await Promise.all([
      getLatestEncounterSummary(props.id, props.eid).catch(() => null),
      getLatestEncounterCoding(props.id, props.eid).catch(() => null),
    ])
    summary.value = sum
    codingResp.value = cod?.payload || cod || null

    // Populate background from saved evidence if available.
    if (sum?.evidence) {
      background.value = extractBackground(sum.evidence)
    }
  } catch {
    // 404 handled below via the encounter list endpoint.
  }

  // Encounter metadata via the patient encounters list endpoint.
  try {
    const list = await listEncounters(props.id)
    const match = list.find((e) => e.encounterId === props.eid)
    if (!match) {
      error.value = 'Encounter not found for this patient.'
      return
    }
    encounter.value = match
  } catch (e) {
    error.value = e.message || 'Failed to load encounter.'
  } finally {
    loading.value = false
  }
}

async function loadSummary(type) {
  busy.summary = true
  try {
    summary.value = await summarizeEncounter(props.id, props.eid, {
      type, includeEvidence: false,
    })
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
    codingResp.value = await suggestEncounterCoding(props.id, props.eid, {
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
  await fetchEncounter()
  // Auto-trigger if URL says ?action=summary or ?action=coding
  if (route.query.action === 'summary' && !summary.value && !busy.summary) {
    loadSummary(defaultIsDischarge.value ? 'discharge_summary' : 'detailed')
  } else if (route.query.action === 'coding' && !codingResp.value && !busy.coding) {
    loadCoding()
  }
})
</script>
