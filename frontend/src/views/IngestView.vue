<template>
  <div>
    <div class="mb-4">
      <h1 class="text-h5 font-weight-bold mb-1">Ingest EMR document</h1>
      <div class="text-body-2 text-grey-darken-1">
        Send any clinical document (text, JSON, or FHIR bundle). The server normalises it,
        calls AI for extraction, validates the JSON schema, and updates Postgres + Neo4j + the Markdown vault.
      </div>
    </div>

    <v-row>
      <v-col cols="12" md="5">
        <v-card>
          <SectionHeader title="Document metadata" icon="mdi-information-outline" />
          <v-divider />
          <v-card-text>
            <v-text-field v-model="patientId" label="Patient ID" prepend-inner-icon="mdi-account" required />
            <v-text-field v-model="name" label="Name (optional)" />
            <v-row>
              <v-col cols="6"><v-select v-model="gender" :items="['male', 'female', 'other']" label="Gender" clearable /></v-col>
              <v-col cols="6"><v-text-field v-model="birthDate" label="Birth date (YYYY-MM-DD)" /></v-col>
            </v-row>
            <v-divider class="my-3" />
            <v-text-field v-model="encounterId" label="Encounter ID (optional)" />
            <v-row>
              <v-col cols="6">
                <v-select v-model="encType" :items="ENCOUNTER_TYPES" label="Encounter type" />
              </v-col>
              <v-col cols="6">
                <v-text-field v-model="encDateTime" label="Encounter dateTime (ISO)" />
              </v-col>
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
          <v-divider />
          <v-card-actions class="px-4 pb-4">
            <v-btn color="primary" :loading="loading" prepend-icon="mdi-cloud-upload-outline" @click="submit">
              Send to backend
            </v-btn>
            <v-spacer />
            <v-menu>
              <template #activator="{ props }">
                <v-btn v-bind="props" variant="text" prepend-icon="mdi-file-document-outline">Load sample</v-btn>
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
          <SectionHeader title="EMR content" icon="mdi-text-box-outline">
            <template #actions>
              <v-chip size="x-small" variant="tonal">{{ contentSize }} chars</v-chip>
            </template>
          </SectionHeader>
          <v-divider />
          <v-card-text>
            <v-textarea
              v-model="content"
              rows="20"
              auto-grow
              variant="outlined"
              placeholder="Paste EMR text here, or JSON/FHIR bundle when format != text"
              spellcheck="false"
            />
          </v-card-text>
        </v-card>

        <v-card v-if="result" class="mt-4">
          <SectionHeader title="Result" :icon="result.detail ? 'mdi-alert-circle-outline' : 'mdi-check-circle-outline'" :color="result.detail ? 'error' : 'success'" />
          <v-divider />
          <v-card-text>
            <pre class="cng-raw">{{ JSON.stringify(result, null, 2) }}</pre>
            <v-btn v-if="result.patientId" color="primary" variant="tonal" class="mt-3" :to="{ name: 'patient', params: { id: result.patientId } }" prepend-icon="mdi-arrow-right">Open patient</v-btn>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ingest } from '../api/client.js'
import { ENCOUNTER_TYPES } from '../constants/clinical.js'
import { useUiStore } from '../stores/ui.js'
import SectionHeader from '../components/SectionHeader.vue'

const ui = useUiStore()

const patientId = ref('HN123456')
const name = ref('Somchai Sample')
const gender = ref('male')
const birthDate = ref('1965-04-12')
const encounterId = ref('')
const encType = ref('admission')
const encDateTime = ref(new Date().toISOString())
const department = ref('Internal Medicine')
const provider = ref('Dr. Demo')
const format = ref('text')
const docId = ref('doc-001')
const version = ref('1')
const system = ref('SampleHIS')
const content = ref('')
const loading = ref(false)
const result = ref(null)

const contentSize = computed(() => content.value?.length || 0)

async function submit() {
  loading.value = true
  result.value = null
  try {
    const parsedContent = format.value === 'text' ? content.value : safeJson(content.value)
    const body = {
      patient: { patientId: patientId.value, name: name.value, gender: gender.value, birthDate: birthDate.value || null },
      encounter: { encounterId: encounterId.value || null, type: encType.value, dateTime: encDateTime.value, department: department.value, provider: provider.value },
      format: format.value,
      content: parsedContent,
      source: { system: system.value, documentId: docId.value, version: version.value },
    }
    result.value = await ingest(body)
    ui.success('Document ingested successfully.')
  } catch (e) {
    result.value = { error: e.message, detail: e.response?.data }
  } finally {
    loading.value = false
  }
}
function safeJson(s) { try { return JSON.parse(s) } catch { return s } }

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
</script>
