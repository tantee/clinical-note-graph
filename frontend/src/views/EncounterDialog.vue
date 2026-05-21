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

      <!-- Tab strip mirrors PatientDetail's layout so the two views feel
           consistent. Tabs that only make sense at patient scope (Timeline,
           Encounters) are deliberately omitted — see /docs/features.md.
           `flex-shrink-0` keeps the tab strip at its natural height inside
           the dialog's `d-flex flex-column` container (without it the
           flexbox collapses the tabs to ~13 px, leaving them invisible). -->
      <v-tabs v-model="tab" color="primary" density="comfortable" show-arrows
              class="flex-shrink-0">
        <v-tab value="overview" prepend-icon="mdi-view-dashboard-outline">Overview</v-tab>
        <v-tab value="timeline" prepend-icon="mdi-timeline-text-outline">Timeline</v-tab>
        <v-tab value="notes" prepend-icon="mdi-file-document-multiple-outline">Notes</v-tab>
        <v-tab value="graph" prepend-icon="mdi-graph-outline">Graph</v-tab>
        <v-tab value="raw" prepend-icon="mdi-text-box-search-outline">EMR vs facts</v-tab>
      </v-tabs>
      <v-divider />

      <v-window v-model="tab" class="flex-grow-1 overflow-y-auto" :touch="false">
        <!-- Overview tab — current "Detail" content -->
        <v-window-item value="overview">
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

                <v-alert v-if="!summary && !codingResp && !hasThisEncounterFacts"
                         type="info" variant="tonal">
                  This encounter has no extracted facts yet.
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

        <!-- Timeline — at encounter scope this is the chronological list of
             documents on this admission/visit, rendered with the same
             v-timeline vertical-rail pattern the patient page uses for
             encounters (but one level deeper: patient → encounters,
             encounter → documents). -->
        <v-window-item value="timeline">
          <div class="pa-4">
            <v-card>
              <SectionHeader title="Documents on this encounter" icon="mdi-timeline-clock-outline">
                <template #actions>
                  <v-chip v-if="docs.length" size="x-small" variant="tonal">{{ docs.length }}</v-chip>
                </template>
              </SectionHeader>
              <v-divider />
              <v-card-text>
                <v-timeline v-if="docs.length" density="comfortable" side="end" align="start">
                  <v-timeline-item
                    v-for="d in sortedDocs"
                    :key="d.documentId"
                    dot-color="primary"
                    icon="mdi-file-document-outline"
                    size="small"
                  >
                    <template #opposite>
                      <div class="text-body-2">{{ d.receivedAt ? formatDate(d.receivedAt) : '—' }}</div>
                      <div class="text-caption text-grey">
                        {{ d.receivedAt ? formatRelative(d.receivedAt) : '' }}
                      </div>
                    </template>
                    <v-card variant="outlined" class="cursor-pointer"
                            @click="openDocumentAndJump(d.documentId)">
                      <v-card-text class="py-3">
                        <div class="d-flex align-center mb-1">
                          <span class="text-subtitle-2 mr-2 text-truncate">{{ d.documentId }}</span>
                          <v-chip size="x-small" variant="tonal">{{ d.format || '?' }}</v-chip>
                          <v-chip v-if="d.version" size="x-small" variant="tonal" class="ml-1">v{{ d.version }}</v-chip>
                        </div>
                        <div v-if="d.sourceSystem" class="text-caption text-grey-darken-1">
                          {{ d.sourceSystem }}
                        </div>
                      </v-card-text>
                    </v-card>
                  </v-timeline-item>
                </v-timeline>
                <EmptyState v-else
                            icon="mdi-file-question-outline"
                            title="No documents on this encounter" />
              </v-card-text>
            </v-card>
          </div>
        </v-window-item>

        <!-- Notes — the encounter's vault folder + the markdown for whatever
             is selected. We re-use the patient-level getNotes endpoint and
             filter client-side; encounter-scope files live under
             patients/<HN>/encounters/<eid>/. -->
        <v-window-item value="notes">
          <div class="pa-4">
            <v-row>
              <v-col cols="12" md="4">
                <v-card>
                  <SectionHeader title="Files" icon="mdi-folder-multiple-outline" />
                  <v-divider />
                  <v-list density="compact" nav>
                    <v-list-item
                      v-for="f in encounterNotes"
                      :key="f.path"
                      :title="f.name"
                      :prepend-icon="noteIcon(f.kind)"
                      :active="selectedNote?.path === f.path"
                      @click="openNote(f.path)"
                    />
                    <EmptyState v-if="!encounterNotes.length"
                                icon="mdi-folder-open-outline"
                                title="No notes for this encounter yet"
                                hint="Run Summarize or Coding from the toolbar above to generate one." />
                  </v-list>
                </v-card>
              </v-col>
              <v-col cols="12" md="8">
                <MarkdownViewer
                  v-if="selectedNote"
                  :path="selectedNote.path"
                  :content="selectedNote.content"
                  :backlinks="selectedNote.backlinks"
                  @open="openNote"
                />
                <EmptyState v-else-if="encounterNotes.length"
                            icon="mdi-file-document-outline"
                            title="Pick a file"
                            hint="Select a file from the list on the left." />
                <EmptyState v-else
                            icon="mdi-folder-open-outline"
                            title="Nothing to show"
                            hint="This encounter has no vault notes yet." />
              </v-col>
            </v-row>
          </div>
        </v-window-item>

        <!-- Graph — single-encounter subgraph -->
        <v-window-item value="graph" class="h-100">
          <GraphView scope="encounter" :patient-id="patientId" :encounter-ids="[eid]" :height="640" />
        </v-window-item>

        <!-- EMR vs facts — same three-column layout as PatientDetail but
             pre-scoped to this encounter's documents. -->
        <v-window-item value="raw">
          <div class="pa-4">
            <v-row>
              <v-col cols="12" md="3">
                <v-card>
                  <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
                  <v-divider />
                  <v-list density="compact" nav>
                    <v-list-item
                      v-for="d in docs"
                      :key="d.documentId"
                      :title="d.documentId"
                      :subtitle="`v${d.version} · ${d.format}`"
                      :active="selectedDocument?.document?.document_id === d.documentId"
                      @click="openDocument(d.documentId)"
                    />
                    <EmptyState v-if="!docs.length"
                                icon="mdi-file-question-outline"
                                title="No documents on this encounter" />
                  </v-list>
                </v-card>
              </v-col>
              <v-col cols="12" md="4">
                <v-card>
                  <SectionHeader title="Raw EMR" icon="mdi-text-box-outline" />
                  <v-divider />
                  <v-card-text>
                    <pre v-if="selectedDocument?.document?.raw_content" class="cng-raw cng-raw-fill">{{ selectedDocument.document.raw_content }}</pre>
                    <EmptyState v-else icon="mdi-text-box-outline" title="No document selected" />
                  </v-card-text>
                </v-card>
              </v-col>
              <v-col cols="12" md="5">
                <v-card>
                  <SectionHeader title="Extracted facts" icon="mdi-format-list-bulleted-square">
                    <template #actions>
                      <v-chip v-if="selectedDocument?.facts?.length" size="x-small" variant="tonal">
                        {{ selectedDocument.facts.length }}
                      </v-chip>
                    </template>
                  </SectionHeader>
                  <v-divider />
                  <v-list density="comfortable">
                    <v-list-item v-for="f in selectedDocument?.facts || []" :key="f.id" :title="f.value">
                      <template #prepend>
                        <v-icon :color="FACT_TYPE_META[f.type]?.color || 'grey'">
                          {{ FACT_TYPE_META[f.type]?.icon || 'mdi-circle-medium' }}
                        </v-icon>
                      </template>
                      <v-list-item-subtitle>
                        <v-chip size="x-small" class="mr-2" variant="tonal">{{ f.type }}</v-chip>
                        <span v-if="f.normalized_code">{{ f.coding_system }}: {{ f.normalized_code }}</span>
                        <span v-if="f.confidence != null"> · conf {{ f.confidence.toFixed(2) }}</span>
                      </v-list-item-subtitle>
                    </v-list-item>
                    <EmptyState v-if="!selectedDocument?.facts?.length"
                                icon="mdi-format-list-bulleted-square" title="No facts" />
                  </v-list>
                </v-card>
              </v-col>
            </v-row>

            <!-- Raw AI output for the selected document — folded into this
                 tab as a collapsible card so reviewers can compare the
                 model's literal response against the extracted facts above
                 without flipping tabs. -->
            <v-card v-if="selectedDocument" class="mt-4">
              <v-expansion-panels variant="accordion">
                <v-expansion-panel>
                  <v-expansion-panel-title>
                    <v-icon class="mr-2" color="primary">mdi-robot-outline</v-icon>
                    Raw AI output
                  </v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <pre v-if="selectedDocument.aiOutput" class="cng-raw">{{ JSON.stringify(selectedDocument.aiOutput.raw_output, null, 2) }}</pre>
                    <EmptyState v-else icon="mdi-robot-confused-outline" title="No AI output for this document" />
                  </v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
            </v-card>
          </div>
        </v-window-item>

      </v-window>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getDocument,
  getEncounterFacts,
  getLatestEncounterSummary, getLatestEncounterCoding,
  getNote, getNotes,
  summarizeEncounter, suggestEncounterCoding,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import { FACT_TYPE_META } from '../constants/clinical.js'
import { formatDate, formatRelative } from '../utils/format.js'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import FactSection from '../components/FactSection.vue'
import GraphView from '../components/GraphView.vue'
import MarkdownViewer from '../components/MarkdownViewer.vue'

const props = defineProps({
  patientId: { type: String, required: true },
  eid:       { type: String, required: true },
})
defineEmits(['close'])

const route = useRoute()
const ui = useUiStore()

const tab = ref('overview')
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

// Notes tab state
const notes = ref([])
const selectedNote = ref(null)

// EMR vs facts + AI output share the same selected document
const selectedDocument = ref(null)

const hasThisEncounterFacts = computed(() => {
  const t = thisEncounter.value
  return (
    t.problems.length || t.medications.length || t.observations.length ||
    t.procedures.length || t.plans.length || t.diagnoses.length
  )
})

// Documents in chronological order for the Timeline tab. Falls back to
// the natural order from gather_encounter_facts when receivedAt is null
// (older rows without timestamps).
const sortedDocs = computed(() =>
  (docs.value || []).slice().sort((a, b) => {
    const ta = a.receivedAt ? new Date(a.receivedAt).getTime() : 0
    const tb = b.receivedAt ? new Date(b.receivedAt).getTime() : 0
    return ta - tb
  }),
)

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

// Encounter-scoped vault files. The patient `getNotes` endpoint returns
// every file under patients/<HN>/; we filter to anything that lives under
// encounters/<eid>/ (sources, AI-generated summary/coding) so the user
// sees only the files that relate to this admission/visit.
const encounterNotes = computed(() => {
  const needle = `/encounters/${props.eid}/`
  return (notes.value || []).filter((f) => f.path.includes(needle))
})

function noteIcon(kind) {
  return {
    visits: 'mdi-calendar-text',
    summaries: 'mdi-text-box-outline',
    coding: 'mdi-tag-text-outline',
    sources: 'mdi-text-box',
  }[kind] || 'mdi-file-document-outline'
}

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const [facts, sum, cod, allNotes] = await Promise.all([
      getEncounterFacts(props.patientId, props.eid).catch((e) => {
        if (e?.response?.status === 404) error.value = 'Encounter not found for this patient.'
        return null
      }),
      getLatestEncounterSummary(props.patientId, props.eid).catch(() => null),
      getLatestEncounterCoding(props.patientId, props.eid).catch(() => null),
      getNotes(props.patientId).catch(() => ({ files: [] })),
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
    notes.value = allNotes?.files || []
    // Auto-open the first encounter note (typically the source admission_note.md)
    if (encounterNotes.value.length) {
      await openNote(encounterNotes.value[0].path)
    }
    // Auto-open the first encounter document so EMR vs facts and AI output
    // have something to show without an extra click.
    if (docs.value.length) {
      await openDocument(docs.value[0].documentId)
    }
  } finally {
    loading.value = false
  }
}

async function openNote(path) {
  try {
    selectedNote.value = await getNote(props.patientId, path)
  } catch {
    ui.error('Failed to load note')
  }
}

async function openDocument(documentId) {
  try {
    selectedDocument.value = await getDocument(props.patientId, documentId)
  } catch {
    ui.error('Failed to load document')
  }
}

// Click a row on the Timeline tab → load the document AND switch to the
// EMR vs facts tab so the user immediately sees raw text + facts side by
// side. The folded Raw-AI-output panel below the facts list will pick up
// the same selection automatically.
async function openDocumentAndJump(documentId) {
  await openDocument(documentId)
  tab.value = 'raw'
}

async function loadSummary(type) {
  busy.summary = true
  try {
    summary.value = await summarizeEncounter(props.patientId, props.eid, { type, includeEvidence: false })
    ui.success('Summary ready')
    // Reload notes so the new summary's vault file shows up under Notes.
    const all = await getNotes(props.patientId).catch(() => null)
    if (all) notes.value = all.files || []
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
    const all = await getNotes(props.patientId).catch(() => null)
    if (all) notes.value = all.files || []
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
