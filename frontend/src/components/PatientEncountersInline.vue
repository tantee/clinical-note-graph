<template>
  <div v-if="loading" class="d-flex justify-center pa-4"><v-progress-circular indeterminate size="20" /></div>
  <v-alert v-else-if="!encounters.length" type="info" variant="tonal" density="compact">No encounters yet.</v-alert>
  <v-table v-else density="compact">
    <thead>
      <tr>
        <th>Date</th><th>Type</th><th>Dept</th><th>Docs</th><th>Summary</th><th>Coding</th><th></th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="e in encounters" :key="e.encounterId">
        <td>{{ e.dateTime ? new Date(e.dateTime).toLocaleString() : '' }}</td>
        <td>{{ e.type }}</td>
        <td>{{ e.department || '' }}</td>
        <td>{{ e.docCount }}</td>
        <td><v-icon v-if="e.hasSummary" color="success" size="small">mdi-check</v-icon><span v-else>—</span></td>
        <td><v-icon v-if="e.hasCoding" color="success" size="small">mdi-check</v-icon><span v-else>—</span></td>
        <td>
          <v-btn size="x-small" variant="text"
                 :to="{ name: 'encounter', params: { id: patientId, eid: e.encounterId } }">View encounter</v-btn>
        </td>
      </tr>
    </tbody>
  </v-table>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listEncounters } from '../api/client.js'
const props = defineProps({ patientId: { type: String, required: true } })
const loading = ref(true)
const encounters = ref([])
onMounted(async () => {
  try { encounters.value = await listEncounters(props.patientId) } catch { encounters.value = [] }
  loading.value = false
})
</script>
