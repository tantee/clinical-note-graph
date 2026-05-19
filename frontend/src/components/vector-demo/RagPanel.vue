<template>
  <v-row>
    <v-col cols="12" md="7">
      <v-card>
        <SectionHeader title="Ask about a patient" icon="mdi-comment-question-outline" />
        <v-divider />
        <v-card-text>
          <v-autocomplete v-model="patientId" :items="patients"
                          item-title="display" item-value="patient_id"
                          label="Patient" prepend-icon="mdi-account" clearable
                          density="compact" />

          <div class="d-flex align-center mt-2 mb-2">
            <v-btn-toggle v-model="mode" mandatory density="compact" color="primary" variant="outlined">
              <v-btn value="one_shot" prepend-icon="mdi-message-text">One-shot</v-btn>
              <v-btn value="chat" prepend-icon="mdi-forum-outline">Chat</v-btn>
            </v-btn-toggle>
            <v-spacer />
            <v-btn v-if="mode === 'chat' && history.length" size="small" variant="text"
                   @click="history = []">Clear chat</v-btn>
          </div>

          <div v-if="mode === 'chat'" class="rag-history mb-2">
            <div v-for="(turn, i) in history" :key="i" class="rag-turn">
              <v-icon size="small" class="mr-2">
                {{ turn.role === 'user' ? 'mdi-account' : 'mdi-robot' }}
              </v-icon>
              <span class="text-body-2">{{ turn.content }}</span>
            </div>
          </div>

          <v-textarea v-model="question" label="Question" rows="2" auto-grow
                      :disabled="!patientId"
                      @keydown.ctrl.enter.exact="submit"
                      hint="Ctrl+Enter to submit" persistent-hint />
          <v-btn class="mt-2" color="primary" prepend-icon="mdi-send-outline"
                 :loading="busy" :disabled="!patientId || !question.trim()" @click="submit">
            Ask
          </v-btn>
          <v-alert v-if="error" type="error" variant="tonal" class="mt-3" closable
                   @click:close="error = ''">{{ error }}</v-alert>
        </v-card-text>
      </v-card>

      <v-card v-if="answer" class="mt-4">
        <SectionHeader title="Answer" icon="mdi-text-box-outline">
          <template #actions>
            <v-chip size="x-small" variant="tonal" color="warning">AI-assisted</v-chip>
            <v-chip size="x-small" variant="tonal" class="ml-1">
              {{ modelUsed }} · {{ latencyMs }}ms
            </v-chip>
          </template>
        </SectionHeader>
        <v-divider />
        <v-card-text>
          <div class="cng-markdown" v-html="renderedAnswer" />
          <v-divider class="my-3" />
          <div class="d-flex align-center flex-wrap text-caption text-grey-darken-1">
            <v-icon size="small" class="mr-1">mdi-link-variant</v-icon>
            <span>Citations:</span>
            <CitationBadge v-for="c in citedCitations" :key="c.n"
                           :citation="c" :patient-id="patientId" />
            <span v-if="!citedCitations.length" class="ml-1">none cited</span>
          </div>
        </v-card-text>
      </v-card>
    </v-col>

    <v-col cols="12" md="5">
      <v-card>
        <SectionHeader title="Behind the scenes" icon="mdi-cog-outline" />
        <v-divider />
        <v-card-text>
          <div class="text-caption text-grey-darken-1 mb-2">
            Top-K = {{ topK }} chunks retrieved by cosine similarity
            (embedding model: {{ embeddingModel || '—' }})
          </div>
          <v-list density="compact">
            <v-list-item v-for="c in citations" :key="c.n">
              <template #prepend>
                <v-chip size="x-small" :color="c.cited ? 'primary' : 'grey'" variant="tonal">
                  [{{ c.n }}]
                </v-chip>
              </template>
              <v-list-item-title class="text-body-2">
                {{ c.refType }}: {{ c.refId }}
              </v-list-item-title>
              <v-list-item-subtitle class="text-caption">
                score {{ c.score.toFixed(3) }} · {{ c.content.slice(0, 100) }}…
              </v-list-item-subtitle>
            </v-list-item>
            <EmptyState v-if="!citations.length" icon="mdi-database-search-outline"
                        title="Ask a question to see retrieved chunks" />
          </v-list>
        </v-card-text>
      </v-card>
    </v-col>
  </v-row>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'
import { listPatients, ragAsk } from '../../api/client.js'
import { parseCitedIndices } from '../../utils/citations.js'
import SectionHeader from '../SectionHeader.vue'
import EmptyState from '../EmptyState.vue'
import CitationBadge from './CitationBadge.vue'

const emit = defineEmits(['model'])

const patients = ref([])
const patientId = ref('')
const mode = ref('one_shot')
const history = ref([])
const question = ref('')
const busy = ref(false)
const error = ref('')

const answer = ref('')
const citations = ref([])
const modelUsed = ref('')
const embeddingModel = ref('')
const latencyMs = ref(0)
const topK = 8

const renderedAnswer = computed(() => marked.parse(answer.value || ''))
const citedIndices = computed(() => parseCitedIndices(answer.value))
const citedCitations = computed(() =>
  citations.value.filter((c) => citedIndices.value.has(c.n)),
)

onMounted(async () => {
  try {
    const list = await listPatients()
    patients.value = (list || []).map((p) => ({
      ...p,
      display: `${p.patient_id}${p.name ? ' — ' + p.name : ''}`,
    }))
  } catch {
    patients.value = []
  }
})

async function submit() {
  if (!patientId.value || !question.value.trim()) return
  busy.value = true
  error.value = ''
  const userTurn = { role: 'user', content: question.value.trim() }
  try {
    const body = {
      patientId: patientId.value,
      question: question.value.trim(),
      mode: mode.value,
      history: mode.value === 'chat' ? history.value : [],
      topK,
    }
    const res = await ragAsk(body)
    answer.value = res.answer
    citations.value = res.citations
    modelUsed.value = res.modelUsed
    embeddingModel.value = res.embeddingModel
    latencyMs.value = res.latencyMs
    emit('model', res.embeddingModel)
    if (mode.value === 'chat') {
      history.value = [
        ...history.value,
        userTurn,
        { role: 'assistant', content: res.answer },
      ]
    }
    question.value = ''
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || 'Failed to get answer'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.rag-history { max-height: 240px; overflow-y: auto; padding: 8px; background: rgba(127,127,127,0.04); border-radius: 6px; }
.rag-turn { padding: 4px 0; display: flex; align-items: flex-start; }
</style>
