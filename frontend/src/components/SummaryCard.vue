<template>
  <v-card v-if="value" class="mt-4">
    <SectionHeader title="AI summary" icon="mdi-text-box-outline">
      <span class="text-caption text-grey-darken-1 ml-2">
        ({{ value.type }}{{ value.createdAt ? ' · ' + new Date(value.createdAt).toLocaleString() : '' }})
      </span>
      <template #actions>
        <v-chip v-if="value.vaultPath" size="x-small" variant="tonal" prepend-icon="mdi-folder-outline" class="mr-1">
          {{ value.vaultPath }}
        </v-chip>
        <v-chip size="x-small" color="warning" variant="tonal">AI-assisted</v-chip>
      </template>
    </SectionHeader>
    <v-divider />
    <v-card-text>
      <div class="cng-markdown" v-html="rendered" />
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import SectionHeader from './SectionHeader.vue'

const props = defineProps({
  value: { type: Object, default: null },
})
const rendered = computed(() => marked.parse(props.value?.markdown || ''))
</script>
