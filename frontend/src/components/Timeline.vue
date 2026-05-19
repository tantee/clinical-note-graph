<template>
  <v-card>
    <SectionHeader title="Timeline" icon="mdi-timeline-clock-outline" />
    <v-divider />
    <v-card-text>
      <v-timeline density="comfortable" side="end" align="start" v-if="sorted.length">
        <v-timeline-item
          v-for="e in sorted"
          :key="e.encounter_id"
          :dot-color="meta(e.type).color"
          :icon="meta(e.type).icon"
          size="small"
        >
          <template #opposite>
            <div class="text-body-2">{{ formatDate(e.date_time) }}</div>
            <div class="text-caption text-grey">{{ formatRelative(e.date_time) }}</div>
          </template>
          <v-card variant="outlined" class="cursor-pointer"
                  @click="$emit('select', e)"
                  @dblclick="$emit('open', e)">
            <v-card-text class="py-3">
              <div class="d-flex align-center mb-1">
                <span class="text-subtitle-2 mr-2">{{ humanType(e.type) }}</span>
                <v-chip size="x-small" variant="tonal">{{ e.document_count }} docs · {{ e.fact_count }} facts</v-chip>
              </div>
              <div v-if="e.department" class="text-caption">Dept: {{ e.department }}</div>
              <div v-if="e.provider" class="text-caption">Provider: {{ e.provider }}</div>
            </v-card-text>
          </v-card>
        </v-timeline-item>
      </v-timeline>
      <EmptyState v-else icon="mdi-timeline-text-outline" title="No encounters yet"
                  hint="Ingest an EMR document on the Ingest page to start the timeline." />
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed } from 'vue'
import SectionHeader from './SectionHeader.vue'
import EmptyState from './EmptyState.vue'
import { ENCOUNTER_META } from '../constants/clinical.js'
import { formatDate, formatRelative } from '../utils/format.js'

const props = defineProps({ encounters: { type: Array, default: () => [] } })
defineEmits(['select', 'open'])

const sorted = computed(() =>
  (props.encounters || []).slice().sort((a, b) => new Date(a.date_time) - new Date(b.date_time)),
)
const meta = (t) => ENCOUNTER_META[t] || ENCOUNTER_META.other
const humanType = (t) => t?.replaceAll('_', ' ') ?? 'visit'
</script>

<style scoped>
.cursor-pointer { cursor: pointer; }
.cursor-pointer:hover { background: rgba(127,127,127,0.06); }
</style>
