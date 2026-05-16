<template>
  <div>
    <div class="d-flex align-center mb-4">
      <div>
        <h1 class="text-h5 font-weight-bold mb-1">Patients</h1>
        <div class="text-body-2 text-grey-darken-1">Search and select a patient to view their longitudinal record.</div>
      </div>
      <v-spacer />
      <v-text-field
        v-model="q"
        density="compact"
        variant="outlined"
        prepend-inner-icon="mdi-magnify"
        placeholder="Search patientId or name"
        hide-details
        clearable
        @update:model-value="debouncedReload"
        style="max-width: 320px"
        aria-label="Search patients"
      />
      <v-btn class="ml-3" color="primary" prepend-icon="mdi-cloud-upload-outline" to="/ingest">Ingest EMR</v-btn>
    </div>

    <v-card v-if="loading || patients.length">
      <v-data-table
        :items="patients"
        :headers="headers"
        :loading="loading"
        item-value="patient_id"
        @click:row="(_, { item }) => goPatient(item.patient_id)"
        density="comfortable"
        :items-per-page="20"
        :items-per-page-options="[10, 20, 50]"
        hover
      >
        <template #item.patient_id="{ item }">
          <strong>{{ item.patient_id }}</strong>
        </template>
        <template #item.gender="{ item }">
          <v-chip v-if="item.gender" size="x-small" :color="genderColor(item.gender)" variant="tonal">
            {{ item.gender }}
          </v-chip>
        </template>
        <template #item.updated_at="{ item }">
          <span class="text-body-2">{{ formatDate(item.updated_at) }}</span>
          <div class="text-caption text-grey">{{ formatRelative(item.updated_at) }}</div>
        </template>
        <template #item.actions="{ item }">
          <v-btn size="small" variant="text" :to="{ name: 'patient', params: { id: item.patient_id } }" append-icon="mdi-arrow-right">Open</v-btn>
        </template>
      </v-data-table>
    </v-card>

    <v-card v-else>
      <EmptyState icon="mdi-account-multiple-outline" title="No patients yet"
                  hint="The patient list is empty.">
        <v-btn color="primary" to="/ingest" prepend-icon="mdi-cloud-upload-outline">Ingest a sample EMR</v-btn>
      </EmptyState>
    </v-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listPatients } from '../api/client.js'
import { formatDate, formatRelative } from '../utils/format.js'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const patients = ref([])
const loading = ref(false)
const q = ref('')

const headers = [
  { title: 'Patient ID', key: 'patient_id', sortable: true },
  { title: 'Name', key: 'name', sortable: true },
  { title: 'Gender', key: 'gender', sortable: false },
  { title: 'Birth date', key: 'birth_date' },
  { title: 'Updated', key: 'updated_at' },
  { title: '', key: 'actions', sortable: false, align: 'end' },
]

let debounceHandle = null
function debouncedReload() {
  clearTimeout(debounceHandle)
  debounceHandle = setTimeout(reload, 250)
}

async function reload() {
  loading.value = true
  try {
    patients.value = await listPatients(q.value)
  } catch {
    patients.value = []
  } finally {
    loading.value = false
  }
}

function goPatient(id) { router.push({ name: 'patient', params: { id } }) }
const genderColor = (g) => (g === 'female' ? 'pink' : g === 'male' ? 'blue' : 'grey')

onMounted(reload)
</script>
