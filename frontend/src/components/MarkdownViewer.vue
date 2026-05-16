<template>
  <v-card>
    <SectionHeader title="Note" icon="mdi-file-document-outline">
      <span class="text-caption text-grey-darken-1 ml-2">{{ path }}</span>
      <template #actions>
        <v-chip size="x-small" color="warning" variant="tonal">AI-assisted</v-chip>
      </template>
    </SectionHeader>
    <v-divider />
    <v-card-text>
      <div ref="bodyEl" class="cng-markdown" v-html="html" @click="onClick" />
      <template v-if="backlinks?.length">
        <v-divider class="my-3" />
        <div class="text-caption text-grey-darken-1 mb-1">Backlinks</div>
        <v-chip
          v-for="b in backlinks"
          :key="b"
          size="small"
          variant="tonal"
          class="ma-1"
          @click="emit('open', b)"
        >{{ b }}</v-chip>
      </template>
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { marked } from 'marked'
import SectionHeader from './SectionHeader.vue'

const props = defineProps({
  path: { type: String, required: true },
  content: { type: String, default: '' },
  backlinks: { type: Array, default: () => [] },
})
const emit = defineEmits(['open'])

const bodyEl = ref(null)

const html = computed(() => {
  const md = props.content || ''
  // Convert [[wikilinks]] into anchors with data-link for delegated handling.
  const linked = md.replace(/\[\[([^\]\|]+?)(?:\|([^\]]+))?\]\]/g, (_, target, label) => {
    const tgt = target.trim().endsWith('.md') ? target.trim() : target.trim() + '.md'
    const safeLabel = (label || target).trim().replace(/</g, '&lt;')
    return `<a href="#" data-link="${tgt.replace(/"/g, '&quot;')}">${safeLabel}</a>`
  })
  return marked.parse(linked)
})

function onClick(event) {
  const link = event.target.closest('a[data-link]')
  if (!link) return
  event.preventDefault()
  const target = link.dataset.link
  // Resolve target relative to the current note's directory.
  const base = props.path.split('/').slice(0, -1)
  // Wikilinks emitted by our backend are written relative to the patient root,
  // e.g. "problems/foo.md" inside a visit page at "patients/HN1/visits/x.md".
  // We need to climb out of the current subfolder back to the patient root.
  const patientRoot = base.slice(0, 2)
  const resolved = [...patientRoot, ...target.split('/')].join('/')
  emit('open', resolved)
}
</script>
