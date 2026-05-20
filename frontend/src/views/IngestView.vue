<template>
  <div>
    <h1 class="text-h5 font-weight-bold mb-1">Ingest EMR document</h1>
    <div class="text-body-2 text-grey-darken-1 mb-4">
      Type or paste a clinical note. If the Patient ID matches an existing patient we update their record;
      otherwise a new patient is created.
    </div>

    <v-row>
      <v-col cols="12" md="5">
        <v-card>
          <SectionHeader title="Patient" icon="mdi-account-outline" />
          <v-divider />
          <v-card-text>
            <v-combobox
              v-model="patientId"
              :items="patientResults"
              :loading="patientsLoading"
              item-title="patient_id"
              item-value="patient_id"
              label="Patient ID"
              :return-object="false"
              :no-filter="true"
              clearable
              hide-no-data
              @update:search="onSearchPatients"
            />
            <div class="text-caption mt-1">
              <v-icon size="14" :color="existing ? 'success' : 'warning'">
                {{ existing ? 'mdi-check' : 'mdi-plus' }}
              </v-icon>
              {{ existing ? `Updating ${existing.patient_id} — ${existing.name || ''}` : 'New patient will be created' }}
            </div>

            <v-divider class="my-3" />
            <v-text-field v-model="name" label="Name (optional)" />
            <v-row>
              <v-col cols="6"><v-select v-model="gender" :items="['male','female','other']" label="Gender" clearable /></v-col>
              <v-col cols="6"><v-text-field v-model="birthDate" label="Birth date (YYYY-MM-DD)" /></v-col>
            </v-row>
          </v-card-text>

          <SectionHeader title="Encounter" icon="mdi-file-document-outline" />
          <v-divider />
          <v-card-text>
            <v-text-field v-model="encounterId" label="Encounter ID (optional)" />
            <v-row>
              <v-col cols="6"><v-select v-model="encType" :items="ENCOUNTER_TYPES" label="Encounter type" /></v-col>
              <v-col cols="6"><v-text-field v-model="encDateTime" label="Encounter dateTime (ISO)" /></v-col>
            </v-row>
            <v-row>
              <v-col cols="6"><v-text-field v-model="department" label="Department" /></v-col>
              <v-col cols="6"><v-text-field v-model="provider" label="Provider" /></v-col>
            </v-row>
            <v-divider class="my-3" />
            <v-row>
              <v-col cols="4"><v-select v-model="format" :items="['text','json','fhir']" label="Format" /></v-col>
              <v-col cols="4"><v-text-field v-model="docId" label="Source document ID" hint="Idempotency key" persistent-hint /></v-col>
              <v-col cols="4"><v-text-field v-model="version" label="Version" /></v-col>
            </v-row>
            <v-text-field v-model="system" label="Source system" />
          </v-card-text>

          <v-card-actions class="px-4 pb-4">
            <v-btn color="primary" :loading="loading" prepend-icon="mdi-cloud-upload-outline" @click="submit">
              Submit
            </v-btn>
            <v-spacer />
            <v-menu>
              <template #activator="{ props: menuProps }">
                <v-btn v-bind="menuProps" variant="text" prepend-icon="mdi-file-document-outline">Load sample</v-btn>
              </template>
              <v-list density="compact">
                <v-list-item @click="fillAdmission">Admission note</v-list-item>
                <v-list-item @click="fillProgress">Progress note</v-list-item>
                <v-list-item @click="fillDischarge">Discharge summary</v-list-item>
                <v-list-item @click="fillFHIR">FHIR bundle</v-list-item>
              </v-list>
            </v-menu>
          </v-card-actions>
        </v-card>
      </v-col>

      <v-col cols="12" md="7">
        <v-card>
          <SectionHeader title="Content" icon="mdi-text-box-outline">
            <template #actions><v-chip size="x-small" variant="tonal">{{ content.length }} chars</v-chip></template>
          </SectionHeader>
          <v-divider />
          <v-card-text>
            <v-textarea
              v-model="content"
              rows="20"
              auto-grow
              variant="outlined"
              placeholder="Paste EMR text here (or JSON/FHIR bundle when format != text)"
              spellcheck="false"
            />
          </v-card-text>
        </v-card>

        <JobWatcher
          v-if="currentJobId"
          class="mt-4"
          :jobId="currentJobId"
          @done="onDone"
          @failed="onFailed"
          @retry="submit"
        />
      </v-col>
    </v-row>

    <!-- Post-submit confirmation dialog (issue #25).
         Default CTA returns the user to Patients so they can queue another
         note or watch the new job in the persistent popover. The secondary
         CTA opens the just-ingested patient in case the user wants to watch
         the per-stage progress inline. -->
    <JobConfirmationDialog
      v-model="confirmDialog"
      :jobId="currentJobId || ''"
      type="emr_ingest"
      :patientId="lastSubmittedPatientId"
      :encounterId="lastSubmittedEncounterId"
      headline="Document queued for processing"
      body="The EMR has been queued. You can submit another, head back to the patient list, or open the patient page to watch the ingest progress."
      primaryLabel="Back to patients"
      secondaryLabel="Open patient"
      @primary="onConfirmBackToList"
      @secondary="onConfirmOpenPatient"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ingest, listPatients } from '../api/client.js'
import { ENCOUNTER_TYPES } from '../constants/clinical.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from '../components/SectionHeader.vue'
import JobWatcher from '../components/JobWatcher.vue'
import JobConfirmationDialog from '../components/JobConfirmationDialog.vue'

const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const patientId = ref(route.query.patientId || '')
const name = ref('')
const gender = ref('male')
const birthDate = ref('')
const encounterId = ref('')
const encType = ref('admission')
const encDateTime = ref(new Date().toISOString())
const department = ref('')
const provider = ref('')
const format = ref('text')
const docId = ref(`doc-${Date.now()}`)
const version = ref('1')
const system = ref('UI')
const content = ref('')
const loading = ref(false)
const currentJobId = ref(null)
const confirmDialog = ref(false)
const lastSubmittedPatientId = ref('')
const lastSubmittedEncounterId = ref('')

const patientResults = ref([])
const patientsLoading = ref(false)
const existing = computed(() => patientResults.value.find((p) => p.patient_id === patientId.value))

let searchTimer
function onSearchPatients(q) {
  clearTimeout(searchTimer)
  if (!q) { patientResults.value = []; return }
  searchTimer = setTimeout(async () => {
    patientsLoading.value = true
    try { patientResults.value = await listPatients(q) } finally { patientsLoading.value = false }
  }, 250)
}

watch(existing, (e) => {
  if (e && !name.value) {
    name.value = e.name || ''
    gender.value = e.gender || ''
    birthDate.value = e.birth_date || ''
  }
})

function safeJson(s) { try { return JSON.parse(s) } catch { return s } }

async function submit() {
  if (!patientId.value) { ui.error('Patient ID required'); return }
  loading.value = true
  currentJobId.value = null
  try {
    const body = {
      patient: { patientId: patientId.value, name: name.value || null, gender: gender.value || null, birthDate: birthDate.value || null },
      encounter: { encounterId: encounterId.value || null, type: encType.value, dateTime: encDateTime.value, department: department.value || null, provider: provider.value || null },
      format: format.value,
      content: format.value === 'text' ? content.value : safeJson(content.value),
      source: { system: system.value, documentId: docId.value, version: version.value },
    }
    const res = await ingest(body)
    currentJobId.value = res.jobId
    lastSubmittedPatientId.value = patientId.value
    lastSubmittedEncounterId.value = encounterId.value || ''
    // Open the confirmation dialog so the user knows the work is queued and
    // can decide whether to head back to the list (default — for the "queue
    // a batch of notes" workflow) or open the patient page (to watch the
    // per-stage JobWatcher progress).
    confirmDialog.value = true
  } finally {
    loading.value = false
  }
}

function onDone(result) {
  ui.success('Document ingested')
  // No auto-redirect any more — the user already chose their next step
  // via the confirmation dialog. The completion just updates the inline
  // JobWatcher panel and the persistent popover.
  const pid = result?.patientId || patientId.value
  if (!confirmDialog.value && pid) router.push({ name: 'patient', params: { id: pid } })
}
function onFailed(err) { ui.error(`Ingest failed: ${err}`) }

function onConfirmBackToList() {
  router.push({ name: 'patients' })
}
function onConfirmOpenPatient() {
  if (lastSubmittedPatientId.value) {
    router.push({ name: 'patient', params: { id: lastSubmittedPatientId.value } })
  }
}

function fillAdmission() {
  content.value = `Admission note
Patient: Somchai Sample, 60M
CC: chest pain
HPI: Patient with known Type 2 diabetes mellitus and hypertension presents with retrosternal chest pain.
HbA1c 8.4 % on admission. BP 152/95. Glucose 220 mg/dL.

Assessment:
1. Acute myocardial infarction (rule out)
2. Type 2 diabetes mellitus, poorly controlled
3. Essential hypertension

Plan:
- Admit CCU for observation
- Start aspirin 81 mg daily
- Continue metformin 500 mg bid
- Add lisinopril 10 mg daily
- Cardiology consult
- Education on diabetes self-management`
  encType.value = 'admission'
  docId.value = 'doc-001'
  version.value = '1'
  format.value = 'text'
}
function fillProgress() {
  content.value = `Progress note Day 2
Patient stable on telemetry. No further chest pain.
HbA1c repeat 8.3 %. BP 132/82 after lisinopril. SpO2 98 %.
Troponin trending down.
Plan:
- Continue current medications
- Follow up cardiology
- Plan discharge tomorrow if labs stable`
  encType.value = 'progress_note'
  docId.value = 'doc-002'
  version.value = '1'
  encDateTime.value = new Date(Date.now() + 86400000).toISOString()
  format.value = 'text'
}
function fillDischarge() {
  content.value = `Discharge summary
Patient: Somchai Sample (HN123456)
Final diagnoses:
1. NSTEMI (acute non-ST elevation myocardial infarction)
2. Type 2 diabetes mellitus, suboptimally controlled
3. Essential hypertension
Discharge medications: Aspirin 81 mg daily, Atorvastatin 40 mg nightly, Metformin 500 mg bid, Lisinopril 10 mg daily.
Follow up: Cardiology 1 week, Endocrine 2 weeks.`
  encType.value = 'discharge_summary'
  docId.value = 'doc-003'
  version.value = '1'
  encDateTime.value = new Date(Date.now() + 2 * 86400000).toISOString()
  format.value = 'text'
}
function fillFHIR() {
  format.value = 'fhir'
  docId.value = 'fhir-001'
  encType.value = 'admission'
  patientId.value = 'HN789'
  name.value = 'Malee Demo'
  gender.value = 'female'
  birthDate.value = '1972-08-09'
  content.value = JSON.stringify({
    resourceType: 'Bundle',
    type: 'collection',
    entry: [
      { resource: { resourceType: 'Patient', id: 'HN789', name: [{ given: ['Malee'], family: 'Demo' }], gender: 'female', birthDate: '1972-08-09' } },
      { resource: { resourceType: 'Encounter', class: { display: 'admission' }, period: { start: encDateTime.value } } },
      { resource: { resourceType: 'Condition', code: { text: 'Community acquired pneumonia', coding: [{ system: 'http://hl7.org/fhir/sid/icd-10', code: 'J18.9' }] } } },
      { resource: { resourceType: 'Condition', code: { text: 'Asthma', coding: [{ system: 'http://hl7.org/fhir/sid/icd-10', code: 'J45.909' }] } } },
      { resource: { resourceType: 'MedicationRequest', status: 'active', medicationCodeableConcept: { text: 'Ceftriaxone 1g IV q24h' } } },
      { resource: { resourceType: 'Observation', code: { text: 'SpO2' }, valueQuantity: { value: 92, unit: '%' } } },
    ],
  }, null, 2)
}

onMounted(() => { if (patientId.value) onSearchPatients(patientId.value) })
</script>
