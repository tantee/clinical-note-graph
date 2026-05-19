<template>
  <v-row>
    <v-col cols="12" md="7">
      <v-card>
        <SectionHeader title="Find patients by free-text query" icon="mdi-account-search-outline" />
        <v-divider />
        <v-card-text>
          <v-text-field v-model="q" label="Query"
                        placeholder="e.g. uncontrolled diabetes on metformin"
                        prepend-inner-icon="mdi-magnify"
                        append-inner-icon="mdi-send-outline"
                        @keydown.enter="submit"
                        @click:append-inner="submit"
                        :loading="busy" />
          <v-alert v-if="error" type="error" variant="tonal" class="mt-2" closable
                   @click:close="error = ''">{{ error }}</v-alert>
        </v-card-text>
      </v-card>

      <div v-if="results.length" class="mt-4">
        <v-card v-for="hit in results" :key="hit.patientId" class="mb-3"
                :to="{ name: 'patient', params: { id: hit.patientId } }">
          <v-card-text>
            <div class="d-flex align-center">
              <strong>{{ hit.name || '(no name)' }}</strong>
              <v-chip size="x-small" class="ml-2">HN {{ hit.patientId }}</v-chip>
              <v-spacer />
              <v-chip size="x-small" color="primary" variant="tonal">
                score {{ hit.score.toFixed(3) }}
              </v-chip>
            </div>
            <div v-for="(s, i) in hit.snippets" :key="i" class="text-body-2 mt-2 text-grey-darken-2">
              <span class="text-caption text-grey-darken-1">[{{ s.refType }}]</span>
              {{ s.content }}
            </div>
          </v-card-text>
        </v-card>
      </div>
      <EmptyState v-else-if="!busy && submitted" icon="mdi-account-question-outline"
                  title="No matches" />
    </v-col>

    <v-col cols="12" md="5">
      <v-card>
        <SectionHeader title="Behind the scenes" icon="mdi-cog-outline" />
        <v-divider />
        <v-card-text class="text-body-2">
          <div>Embedding model: <code>{{ embeddingModel || '—' }}</code></div>
          <div>Latency: {{ latencyMs ? latencyMs + ' ms' : '—' }}</div>
          <div>Results returned: {{ results.length }}</div>
          <p class="text-caption text-grey-darken-1 mt-3">
            The query is embedded once via the same model used at ingest time.
            Cosine similarity is computed per chunk; results are grouped by
            patient and ranked by max-similarity. No LLM call.
          </p>
        </v-card-text>
      </v-card>
    </v-col>
  </v-row>
</template>

<script setup>
import { ref } from 'vue'
import { searchPatientsByVector } from '../../api/client.js'
import SectionHeader from '../SectionHeader.vue'
import EmptyState from '../EmptyState.vue'

const emit = defineEmits(['model'])

const q = ref('')
const busy = ref(false)
const submitted = ref(false)
const error = ref('')
const results = ref([])
const embeddingModel = ref('')
const latencyMs = ref(0)

async function submit() {
  if (!q.value.trim()) return
  busy.value = true
  submitted.value = true
  error.value = ''
  try {
    const res = await searchPatientsByVector(q.value.trim(), 10)
    results.value = res.results || []
    embeddingModel.value = res.embeddingModel
    latencyMs.value = res.latencyMs
    emit('model', res.embeddingModel)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || 'Search failed'
    results.value = []
  } finally {
    busy.value = false
  }
}
</script>
