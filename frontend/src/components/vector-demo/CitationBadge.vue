<template>
  <v-chip size="x-small" variant="tonal" color="primary"
          class="ml-1"
          :title="tooltipText"
          @click="open">
    [{{ citation.n }}]
  </v-chip>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  citation: { type: Object, required: true },  // { n, refType, refId, content, score, cited }
  patientId: { type: String, required: true },
})

const router = useRouter()

const tooltipText = computed(() => {
  const c = props.citation
  return `${c.refType}: ${c.refId}\nscore ${c.score.toFixed(3)}\n${c.content}`
})

function open() {
  if (props.citation.refType === 'note') {
    router.push({
      name: 'patient',
      params: { id: props.patientId },
      query: { note: props.citation.refId },
    })
  } else {
    // 'fact' refs are synthesized — no direct document link in v1.
    router.push({ name: 'patient', params: { id: props.patientId } })
  }
}
</script>
