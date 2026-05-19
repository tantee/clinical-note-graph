<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom" :offset="8">
    <template #activator="{ props: a }">
      <v-text-field v-bind="a"
                    v-model="q" placeholder="Search patients…"
                    prepend-inner-icon="mdi-magnify"
                    variant="outlined" density="compact" hide-details
                    style="max-width: 320px;"
                    @update:model-value="onInput"
                    @focus="onFocus" />
    </template>
    <v-card min-width="320" max-width="400">
      <v-list density="compact">
        <v-list-item v-for="hit in results" :key="hit.patientId"
                     :to="{ name: 'patient', params: { id: hit.patientId } }"
                     @click="open = false">
          <v-list-item-title>{{ hit.name || hit.patientId }}</v-list-item-title>
          <v-list-item-subtitle class="text-caption">
            HN {{ hit.patientId }} · score {{ hit.score.toFixed(2) }}
            <span v-if="hit.snippets.length" class="ml-1">
              · {{ hit.snippets[0].content.slice(0, 60) }}…
            </span>
          </v-list-item-subtitle>
        </v-list-item>
        <v-list-item v-if="!busy && !results.length && q.length > 1">
          <v-list-item-subtitle>No matches</v-list-item-subtitle>
        </v-list-item>
      </v-list>
    </v-card>
  </v-menu>
</template>

<script setup>
import { ref } from 'vue'
import { searchPatientsByVector } from '../api/client.js'

const q = ref('')
const open = ref(false)
const busy = ref(false)
const results = ref([])

let debounceTimer = null
let abortController = null

function onFocus() {
  if (results.value.length) open.value = true
}

function onInput(v) {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!v || v.length < 2) {
    results.value = []
    open.value = false
    return
  }
  debounceTimer = setTimeout(() => fetch(v), 300)
}

async function fetch(query) {
  if (abortController) abortController.abort()
  abortController = new AbortController()
  busy.value = true
  try {
    const res = await searchPatientsByVector(query, 8, abortController.signal)
    results.value = res.results || []
    open.value = true
  } catch (e) {
    if (e.name !== 'CanceledError' && e.name !== 'AbortError') {
      results.value = []
    }
  } finally {
    busy.value = false
  }
}
</script>
