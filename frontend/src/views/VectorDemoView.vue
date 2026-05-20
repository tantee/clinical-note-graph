<template>
  <div>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5 font-weight-bold">Vector DB demo</h1>
      <v-chip size="small" color="info" variant="tonal" class="ml-3">
        pgvector · {{ embeddingModel || 'embedding model loading…' }}
      </v-chip>
    </div>

    <v-tabs v-model="tab" color="primary" density="comfortable">
      <v-tab value="rag" prepend-icon="mdi-comment-question-outline">RAG (Q&amp;A)</v-tab>
      <v-tab value="search" prepend-icon="mdi-account-search-outline">Patient search</v-tab>
    </v-tabs>
    <v-window v-model="tab" class="mt-4">
      <v-window-item value="rag" eager>
        <RagPanel @model="embeddingModel = $event" />
      </v-window-item>
      <v-window-item value="search">
        <PatientSearchPanel @model="embeddingModel = $event" />
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import RagPanel from '../components/vector-demo/RagPanel.vue'
import PatientSearchPanel from '../components/vector-demo/PatientSearchPanel.vue'

const tab = ref('rag')
const embeddingModel = ref('')
</script>
