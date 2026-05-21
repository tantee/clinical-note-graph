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

                <!-- This-encounter facts: rendered as one v-list, mirroring
                     the Background panel on the right. FactSection emits a
                     v-list-subheader + v-list-items, so the parent v-list's
                     native padding applies and the two columns line up.
                     `mt-4` only when there's a preceding card — otherwise
                     this card would sit 16px below the Background card on
                     the right because SummaryCard/CodingCard use v-if=value
                     and contribute no height when empty. -->
                <v-card v-if="hasThisEncounterFacts"
                        :class="{ 'mt-4': summary || codingResp }">
                  <SectionHeader title="This encounter" icon="mdi-clipboard-text-outline" />
                  <v-divider />
                  <v-list density="compact">
                    <template v-for="(group, idx) in thisEncounterGroups" :key="group.title">
                      <v-divider v-if="idx > 0" class="my-1" />
                      <FactSection :title="group.title" :items="group.items" />
                    </template>
                  </v-list>
                </v-card>

                <!-- Same conditional-spacing rule as This-encounter above. -->
                <v-card v-if="docs.length"
                        :class="{ 'mt-4': summary || codingResp || hasThisEncounterFacts }">
                  <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
                  <v-divider />
                  <v-list density="compact" nav>
                    <v-list-item v-for="d in docs" :key="d.documentId"
                                 :title="d.documentId"
                                 :subtitle="`v${d.version} · ${d.format}`" />
                  </v-list>
                </v-card>

                <v-alert v-if="!summary && !codingResp && !hasThisEncounterFacts && !docs.length"
                         type="info" variant="tonal">
                  This encounter has no extracted facts or documents yet.
                  Click <strong>Summarize</strong> or <strong>Coding</strong> to generate output.
                </v-alert>
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getEncounterFacts,
  getLatestEncounterSummary, getLatestEncounterCoding,
  summarizeEncounter, suggestEncounterCoding,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import FactSection from '../components/FactSection.vue'
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
const thisEncounter = ref({
  problems: [], medications: [], observations: [], procedures: [],
  plans: [], allergies: [], diagnoses: [], codingCandidates: [],
})
const docs = ref([])
const summary = ref(null)
const codingResp = ref(null)
const busy = reactive({ summary: false, coding: false })

const hasThisEncounterFacts = computed(() => {
  const t = thisEncounter.value
  return (
    t.problems.length || t.medications.length || t.observations.length ||
    t.procedures.length || t.plans.length || t.diagnoses.length
  )
})

// Drive the template loop from data so the dividers between groups are
// rendered between (not after) each section. Order matches the clinical
// reading flow: problems first, then meds, then numeric findings, then
// procedures, plans, and finally any extra diagnosis candidates.
const thisEncounterGroups = computed(() => {
  const t = thisEncounter.value
  return [
    { title: 'Problems',     items: t.problems },
    { title: 'Medications',  items: t.medications },
    { title: 'Observations', items: t.observations },
    { title: 'Procedures',   items: t.procedures },
    { title: 'Plan',         items: t.plans },
    { title: 'Diagnoses',    items: t.diagnoses },
  ].filter((g) => g.items.length)
})

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    // gather_encounter_facts is the single source of truth for the dialog
    // header (encounter metadata), the left-column facts, the right-column
    // background, AND the documents list. Older code split this across
    // three endpoints and silently rendered a blank pane when none of them
    // returned anything — see bug fix in fix/encounter-dialog-blank.
    const [facts, sum, cod] = await Promise.all([
      getEncounterFacts(props.patientId, props.eid).catch((e) => {
        if (e?.response?.status === 404) error.value = 'Encounter not found for this patient.'
        return null
      }),
      getLatestEncounterSummary(props.patientId, props.eid).catch(() => null),
      getLatestEncounterCoding(props.patientId, props.eid).catch(() => null),
    ])
    if (facts) {
      encounter.value = facts.encounter || null
      thisEncounter.value = {
        problems:         facts.thisEncounter?.problems || [],
        medications:      facts.thisEncounter?.medications || [],
        observations:     facts.thisEncounter?.observations || [],
        procedures:       facts.thisEncounter?.procedures || [],
        plans:            facts.thisEncounter?.plans || [],
        allergies:        facts.thisEncounter?.allergies || [],
        diagnoses:        facts.thisEncounter?.diagnoses || [],
        codingCandidates: facts.thisEncounter?.codingCandidates || [],
      }
      background.value = {
        chronicProblems: facts.background?.chronicProblems || [],
        homeMedications: facts.background?.homeMedications || [],
        knownAllergies:  facts.background?.knownAllergies || [],
      }
      docs.value = facts.documents || []
    }
    summary.value = sum
    codingResp.value = cod?.payload || cod || null
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
