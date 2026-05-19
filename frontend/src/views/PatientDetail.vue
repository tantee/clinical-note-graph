<template>
  <div v-if="!loading && !patient">
    <v-alert type="error" variant="tonal">Patient not found.</v-alert>
  </div>

  <div v-else>
    <div class="d-flex align-center mb-4 flex-wrap">
      <v-btn icon="mdi-arrow-left" variant="text" :to="{ name: 'patients' }" aria-label="Back to patients" />
      <div class="ml-2">
        <h1 class="text-h5 font-weight-bold mb-0">Patient {{ id }}</h1>
        <div class="text-body-2 text-grey-darken-1">
          {{ patient?.patient?.name || '(no name on file)' }}
          <span v-if="patient?.patient?.gender" class="ml-2">·</span>
          <span v-if="patient?.patient?.gender" class="ml-2">{{ patient.patient.gender }}</span>
          <span v-if="patient?.patient?.birth_date" class="ml-2">· DOB {{ patient.patient.birth_date }}</span>
        </div>
      </div>
      <v-spacer />
      <v-btn class="mr-2" variant="text" prepend-icon="mdi-note-plus-outline" :to="{ name: 'ingest', query: { patientId: id } }">Add note</v-btn>
      <v-btn class="mr-2" color="primary" variant="tonal" prepend-icon="mdi-text-box-outline" :loading="busy.summary" @click="loadSummary">
        {{ summary ? 'Regenerate summary' : 'Summary' }}
      </v-btn>
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-medical-bag-outline" :loading="busy.coding" @click="loadCoding">
        {{ codingResp ? 'Regenerate coding' : 'Coding' }}
      </v-btn>
    </div>

    <v-tabs v-model="tab" color="primary" density="comfortable" show-arrows>
      <v-tab value="overview" prepend-icon="mdi-view-dashboard-outline">Overview</v-tab>
      <v-tab value="timeline" prepend-icon="mdi-timeline-text-outline">Timeline</v-tab>
      <v-tab value="encounters" prepend-icon="mdi-table-account">Encounters</v-tab>
      <v-tab value="notes" prepend-icon="mdi-file-document-multiple-outline">Notes</v-tab>
      <v-tab value="graph" prepend-icon="mdi-graph-outline">Graph</v-tab>
      <v-tab value="raw" prepend-icon="mdi-text-box-search-outline">EMR vs facts</v-tab>
      <v-tab value="ai" prepend-icon="mdi-robot-outline">AI output</v-tab>
    </v-tabs>

    <v-window v-model="tab" class="mt-4" :touch="false">
      <!-- Overview -->
      <v-window-item value="overview" eager>
        <v-row>
          <v-col cols="12" md="6">
            <FactCard
              title="Active problems" icon="mdi-medical-bag" color="deep-orange"
              :items="patient?.problems || []" empty="No problems extracted yet"
              :title-fn="(p) => p.value" :code-fn="(p) => p.normalized_code && `${p.coding_system}: ${p.normalized_code}`"
              :status-fn="(p) => p.review_status"
            />
          </v-col>
          <v-col cols="12" md="6">
            <FactCard
              title="Medications" icon="mdi-pill" color="green"
              :items="patient?.medications || []" empty="No medications yet"
              :title-fn="(m) => m.value" :code-fn="(m) => m.extra?.action"
              :status-fn="(m) => m.review_status"
            />
          </v-col>
          <v-col cols="12" md="6">
            <FactCard
              title="Recent observations" icon="mdi-chart-line" color="cyan"
              :items="(patient?.observations || []).slice(0, 30)" empty="No observations yet"
              :title-fn="(o) => o.value"
            />
          </v-col>
          <v-col cols="12" md="6">
            <FactCard
              title="Plans" icon="mdi-clipboard-list-outline" color="brown"
              :items="patient?.plans || []" empty="No plans yet"
              :title-fn="(p) => p.value"
            />
          </v-col>
        </v-row>

        <SummaryCard ref="summaryCard" :value="summary" />
        <CodingCard ref="codingCard" :value="codingResp" />
      </v-window-item>

      <!-- Timeline -->
      <v-window-item value="timeline">
        <Timeline :encounters="timeline.encounters || []"
                  @select="selectEncounter"
                  @open="openEncounter" />
      </v-window-item>

      <!-- Encounters -->
      <v-window-item value="encounters">
        <v-data-table
          :headers="encounterHeaders"
          :items="encounters"
          items-per-page="25"
          density="comfortable"
          class="elevation-0"
        >
          <template #item.dateTime="{ item }">
            {{ item.dateTime ? new Date(item.dateTime).toLocaleString() : '' }}
          </template>
          <template #item.hasSummary="{ item }">
            <v-icon v-if="item.hasSummary" color="success" size="small">mdi-check</v-icon>
            <span v-else class="text-grey">—</span>
          </template>
          <template #item.hasCoding="{ item }">
            <v-icon v-if="item.hasCoding" color="success" size="small">mdi-check</v-icon>
            <span v-else class="text-grey">—</span>
          </template>
          <template #item.actions="{ item }">
            <v-btn size="x-small" variant="text" :to="{ name: 'encounter', params: { id, eid: item.encounterId } }">View</v-btn>
            <v-btn size="x-small" variant="text"
                   :to="{ name: 'encounter', params: { id, eid: item.encounterId }, query: { action: 'summary' } }">
              Summarize
            </v-btn>
          </template>
        </v-data-table>
      </v-window-item>

      <!-- Notes -->
      <v-window-item value="notes">
        <v-row>
          <v-col cols="12" md="3">
            <v-card>
              <SectionHeader title="Files" icon="mdi-folder-multiple-outline" />
              <v-divider />
              <v-list density="compact" nav open-strategy="multiple" :opened="openedFolders">
                <!-- Files at patient root (index.md, summary-x.md if any), no folder wrapper -->
                <v-list-item
                  v-for="f in noteTree.rootFiles"
                  :key="f.path"
                  :title="f.name"
                  :active="selectedNote?.path === f.path"
                  :prepend-icon="kindIcon(f.kind)"
                  @click="openNote(f.path)"
                />
                <v-list-group
                  v-for="folder in noteTree.folders"
                  :key="folder.kind"
                  :value="folder.kind"
                >
                  <template #activator="{ props: a }">
                    <v-list-item
                      v-bind="a"
                      :title="folder.label"
                      :prepend-icon="kindIcon(folder.kind)"
                      density="compact"
                    >
                      <template #append>
                        <span class="text-caption text-grey-darken-1">{{ folder.files.length }}</span>
                      </template>
                    </v-list-item>
                  </template>
                  <v-list-item
                    v-for="f in folder.files"
                    :key="f.path"
                    :title="f.name"
                    :active="selectedNote?.path === f.path"
                    @click="openNote(f.path)"
                  />
                </v-list-group>
                <EmptyState v-if="!notes.length" icon="mdi-folder-open-outline" title="No notes yet" />
              </v-list>
            </v-card>
          </v-col>
          <v-col cols="12" md="9">
            <MarkdownViewer
              v-if="selectedNote"
              :path="selectedNote.path"
              :content="selectedNote.content"
              :backlinks="selectedNote.backlinks"
              @open="openNote"
            />
            <v-alert v-else type="info" variant="tonal">Select a note from the file tree.</v-alert>
          </v-col>
        </v-row>
      </v-window-item>

      <!-- Graph -->
      <v-window-item value="graph">
        <GraphView :data="graph" :height="640" />
      </v-window-item>

      <!-- Raw EMR vs facts -->
      <v-window-item value="raw">
        <v-row class="cng-emr-row">
          <v-col cols="12" md="3" class="d-flex">
            <v-card class="d-flex flex-column flex-grow-1 overflow-hidden">
              <SectionHeader title="Documents" icon="mdi-file-multiple-outline" />
              <v-divider />
              <v-list density="compact" nav class="flex-grow-1 overflow-y-auto" style="min-height: 0;">
                <v-list-item
                  v-for="d in encounterDocuments"
                  :key="d.document_id"
                  :title="d.document_id"
                  :subtitle="`v${d.version} · ${d.format}`"
                  :active="selectedDocument?.document?.document_id === d.document_id"
                  @click="openDocument(d.document_id)"
                />
                <EmptyState v-if="!encounterDocuments.length" icon="mdi-file-question-outline" title="Pick an encounter on the Timeline tab" />
              </v-list>
            </v-card>
          </v-col>
          <v-col cols="12" md="4" class="d-flex">
            <v-card class="d-flex flex-column flex-grow-1 overflow-hidden">
              <SectionHeader title="Raw EMR" icon="mdi-text-box-outline" />
              <v-divider />
              <v-card-text class="flex-grow-1 overflow-y-auto" style="min-height: 0;">
                <pre v-if="selectedDocument?.document?.raw_content" class="cng-raw cng-raw-fill">{{ selectedDocument.document.raw_content }}</pre>
                <EmptyState v-else icon="mdi-text-box-outline" title="No document selected" />
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" md="5" class="d-flex">
            <v-card class="d-flex flex-column flex-grow-1 overflow-hidden">
              <SectionHeader title="Extracted facts" icon="mdi-format-list-bulleted-square" />
              <v-divider />
              <v-list density="comfortable" class="flex-grow-1 overflow-y-auto" style="min-height: 0;">
                <v-list-item v-for="f in selectedDocument?.facts || []" :key="f.id" :title="f.value">
                  <template #prepend>
                    <v-icon :color="FACT_TYPE_META[f.type]?.color || 'grey'">{{ FACT_TYPE_META[f.type]?.icon || 'mdi-circle-medium' }}</v-icon>
                  </template>
                  <v-list-item-subtitle>
                    <v-chip size="x-small" class="mr-2" variant="tonal">{{ f.type }}</v-chip>
                    <span v-if="f.normalized_code">{{ f.coding_system }}: {{ f.normalized_code }}</span>
                    <span v-if="f.confidence != null"> · conf {{ f.confidence.toFixed(2) }}</span>
                  </v-list-item-subtitle>
                  <template #append>
                    <v-btn-toggle
                      :model-value="f.review_status"
                      mandatory
                      variant="outlined"
                      density="compact"
                      @update:model-value="(v) => onReviewChange(f, v)"
                    >
                      <v-btn value="ai_suggested" size="x-small" icon="mdi-robot" aria-label="AI suggested" />
                      <v-btn value="human_confirmed" size="x-small" icon="mdi-check" color="success" aria-label="Confirm" />
                      <v-btn value="rejected" size="x-small" icon="mdi-close" color="error" aria-label="Reject" />
                    </v-btn-toggle>
                  </template>
                </v-list-item>
                <EmptyState v-if="!selectedDocument?.facts?.length" icon="mdi-format-list-bulleted-square" title="No facts" />
              </v-list>
            </v-card>
          </v-col>
        </v-row>
      </v-window-item>

      <!-- AI raw -->
      <v-window-item value="ai">
        <v-card>
          <SectionHeader title="Latest AI output for selected document" icon="mdi-robot-outline" />
          <v-divider />
          <v-card-text>
            <pre v-if="selectedDocument?.aiOutput" class="cng-raw">{{ JSON.stringify(selectedDocument.aiOutput.raw_output, null, 2) }}</pre>
            <EmptyState v-else icon="mdi-robot-confused-outline" title="No AI output"
                        hint="Select an encounter on the Timeline tab, then a document on the EMR-vs-facts tab." />
          </v-card-text>
        </v-card>
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { FACT_TYPE_META } from '../constants/clinical.js'
import {
  getPatient, getTimeline, getGraph, getNotes, getNote,
  getEncounterDocuments, getDocument, summarize, suggestCoding, reviewFact,
  getLatestSummary, getLatestCoding, listEncounters,
} from '../api/client.js'
import { useUiStore } from '../stores/ui.js'
import MarkdownViewer from '../components/MarkdownViewer.vue'
import GraphView from '../components/GraphView.vue'
import Timeline from '../components/Timeline.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import FactCard from '../components/FactCard.vue'
import SummaryCard from '../components/SummaryCard.vue'
import CodingCard from '../components/CodingCard.vue'

const props = defineProps({ id: { type: String, required: true } })
const ui = useUiStore()
const router = useRouter()

const tab = ref('overview')
const loading = ref(true)
const patient = ref(null)
const timeline = ref({ encounters: [] })
const graph = ref({ nodes: [], edges: [] })
const notes = ref([])
const selectedNote = ref(null)
const encounterDocuments = ref([])
const selectedDocument = ref(null)
const encounters = ref([])
const summary = ref(null)
const codingResp = ref(null)
const summaryCard = ref(null)
const codingCard = ref(null)
const busy = reactive({ summary: false, coding: false })
let activeAbort = null

const encounterHeaders = [
  { title: 'Date', key: 'dateTime', sortable: true },
  { title: 'Type', key: 'type', sortable: true },
  { title: 'Dept', key: 'department', sortable: true },
  { title: 'Provider', key: 'provider', sortable: true },
  { title: 'Docs', key: 'docCount', sortable: true, align: 'end' },
  { title: 'Summary', key: 'hasSummary', sortable: true, align: 'center' },
  { title: 'Coding', key: 'hasCoding', sortable: true, align: 'center' },
  { title: '', key: 'actions', sortable: false, align: 'end' },
]

async function load() {
  if (activeAbort) activeAbort.abort()
  const ctl = new AbortController()
  activeAbort = ctl
  loading.value = true
  try {
    const [p, t, g, n, sum, cod, encs] = await Promise.all([
      getPatient(props.id, ctl.signal),
      getTimeline(props.id, ctl.signal),
      getGraph(props.id, ctl.signal),
      getNotes(props.id, ctl.signal),
      getLatestSummary(props.id).catch(() => null),
      getLatestCoding(props.id).catch(() => null),
      listEncounters(props.id).catch(() => []),
    ])
    patient.value = p
    timeline.value = t
    graph.value = g
    notes.value = n.files
    summary.value = sum || null
    codingResp.value = cod?.payload || null
    encounters.value = encs
    if (notes.value.length) {
      const idx = notes.value.find((f) => f.path.endsWith('index.md')) || notes.value[0]
      openNote(idx.path)
    } else {
      selectedNote.value = null
    }
  } catch (e) {
    if (e.name !== 'CanceledError' && e.name !== 'AbortError') {
      patient.value = null
    }
  } finally {
    loading.value = false
  }
}

async function openNote(path) {
  try {
    selectedNote.value = await getNote(props.id, path)
  } catch {
    selectedNote.value = null
  }
}

async function selectEncounter(e) {
  try {
    const resp = await getEncounterDocuments(props.id, e.encounter_id)
    encounterDocuments.value = resp.documents
    if (resp.documents.length) {
      await openDocument(resp.documents[0].document_id)
    } else {
      selectedDocument.value = null
    }
    tab.value = 'raw'
  } catch (err) {
    /* error already toast-ed by interceptor */
  }
}

function openEncounter(e) {
  router.push({ name: 'encounter', params: { id: props.id, eid: e.encounter_id } })
}

async function openDocument(documentId) {
  selectedDocument.value = await getDocument(props.id, documentId)
}

async function revealCard(cardRef) {
  // After an AI response, jump back to Overview and scroll the new card into
  // view so the user doesn't wonder where the result landed.
  tab.value = 'overview'
  await nextTick()
  const el = cardRef.value?.$el || cardRef.value
  if (el?.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function loadSummary() {
  busy.summary = true
  try {
    summary.value = await summarize(props.id, { type: 'detailed', includeEvidence: true })
    ui.success('Summary ready')
    await revealCard(summaryCard)
  } finally { busy.summary = false }
}
async function loadCoding() {
  busy.coding = true
  try {
    codingResp.value = await suggestCoding(props.id, { standards: ['ICD10', 'SNOMEDCT'], includeEvidence: true })
    ui.success('Coding suggestion ready')
    await revealCard(codingCard)
  } finally { busy.coding = false }
}

async function onReviewChange(fact, status) {
  if (!status || status === fact.review_status) return
  const prev = fact.review_status
  fact.review_status = status
  try {
    await reviewFact(fact.id, status)
    ui.success(`Marked as ${status.replaceAll('_', ' ')}`)
  } catch {
    fact.review_status = prev
  }
}

const FOLDER_ORDER = ['visits', 'summaries', 'problems', 'medications', 'observations', 'labs', 'plans', 'allergies', 'procedures', 'sources']
const FOLDER_LABELS = {
  visits: 'Visits',
  summaries: 'Summaries',
  problems: 'Problems',
  medications: 'Medications',
  observations: 'Observations',
  labs: 'Labs',
  plans: 'Plans',
  allergies: 'Allergies',
  procedures: 'Procedures',
  sources: 'Source documents',
}

const noteTree = computed(() => {
  const rootFiles = []
  const byFolder = new Map()
  for (const f of notes.value || []) {
    // Root-level files have kind = 'index.md' or path with no extra slashes under patient root
    const segments = f.path.split('/')
    const isRoot = segments.length <= 3 // patients/<HN>/<file>
    if (isRoot) {
      rootFiles.push(f)
    } else {
      const folder = segments[2] // patients/<HN>/<folder>/<file>
      if (!byFolder.has(folder)) byFolder.set(folder, [])
      byFolder.get(folder).push(f)
    }
  }
  const folders = []
  const seen = new Set()
  for (const key of FOLDER_ORDER) {
    if (byFolder.has(key)) {
      folders.push({ kind: key, label: FOLDER_LABELS[key] || key, files: byFolder.get(key) })
      seen.add(key)
    }
  }
  // Any folder not in FOLDER_ORDER (future entity types) — append alphabetically.
  for (const [key, files] of [...byFolder.entries()].sort()) {
    if (!seen.has(key)) folders.push({ kind: key, label: FOLDER_LABELS[key] || key, files })
  }
  return { rootFiles, folders }
})

// Auto-open the folder containing the currently selected file.
const openedFolders = computed(() => {
  if (!selectedNote.value) return ['visits', 'summaries']
  const segs = selectedNote.value.path.split('/')
  return segs.length > 3 ? [segs[2]] : ['visits', 'summaries']
})

function kindIcon(kind) {
  return {
    visits: 'mdi-calendar-text',
    summaries: 'mdi-text-box-outline',
    problems: 'mdi-medical-bag',
    medications: 'mdi-pill',
    observations: 'mdi-stethoscope',
    labs: 'mdi-chart-line',
    plans: 'mdi-clipboard-list-outline',
    allergies: 'mdi-allergy',
    procedures: 'mdi-scalpel',
    sources: 'mdi-text-box',
    'index.md': 'mdi-file-document-outline',
  }[kind] || 'mdi-file-document-outline'
}

watch(() => props.id, load)
onMounted(load)
onBeforeUnmount(() => activeAbort && activeAbort.abort())
</script>
