<template>
  <v-card class="fill-height">
    <SectionHeader :title="title" :icon="icon" :color="color" />
    <v-divider />
    <v-list v-if="items.length" density="compact">
      <v-list-item v-for="item in items" :key="item.id || titleFn(item)">
        <v-list-item-title>{{ titleFn(item) }}</v-list-item-title>
        <v-list-item-subtitle v-if="codeFn && codeFn(item)">{{ codeFn(item) }}</v-list-item-subtitle>
        <template v-if="statusFn" #append>
          <v-chip size="x-small" :color="meta(statusFn(item)).color" variant="tonal" :prepend-icon="meta(statusFn(item)).icon">
            {{ meta(statusFn(item)).label }}
          </v-chip>
        </template>
      </v-list-item>
    </v-list>
    <EmptyState v-else icon="mdi-tray" :title="empty" />
  </v-card>
</template>

<script setup>
import SectionHeader from './SectionHeader.vue'
import EmptyState from './EmptyState.vue'
import { REVIEW_META } from '../constants/clinical.js'

defineProps({
  title: String,
  icon: String,
  color: String,
  items: { type: Array, default: () => [] },
  empty: { type: String, default: 'No items' },
  titleFn: { type: Function, required: true },
  codeFn: Function,
  statusFn: Function,
})

const meta = (s) => REVIEW_META[s] || { color: 'grey', icon: 'mdi-help-circle-outline', label: s || 'unknown' }
</script>
