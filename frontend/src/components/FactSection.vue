<template>
  <div class="mb-3">
    <div class="d-flex align-center mb-1">
      <v-icon v-if="icon" size="18" class="mr-2" color="grey-darken-1">{{ icon }}</v-icon>
      <div class="text-subtitle-2">{{ title }}</div>
      <v-chip size="x-small" class="ml-2" variant="tonal">{{ items.length }}</v-chip>
    </div>
    <v-list density="compact" class="py-0">
      <v-list-item v-for="(it, i) in items" :key="it.id || `${title}-${i}`" class="px-2">
        <v-list-item-title class="text-body-2">
          {{ it.value || it.name || '(unnamed)' }}
          <span v-if="it.normalized_code" class="text-caption text-grey-darken-1 ml-1">
            · {{ it.normalized_code }}
          </span>
        </v-list-item-title>
        <v-list-item-subtitle v-if="subtitleFor(it)">{{ subtitleFor(it) }}</v-list-item-subtitle>
      </v-list-item>
    </v-list>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: '' },
  items: { type: Array, required: true },
})

function subtitleFor(it) {
  const extra = it.extra || {}
  // Observations: show "value unit" from extra.
  if (extra.value !== undefined) {
    const unit = extra.unit ? ` ${extra.unit}` : ''
    return `${extra.value}${unit}`
  }
  // Medications: show extra.action ("continue" / "start" / "stop").
  if (extra.action) return extra.action
  // Plans / procedures: lean on evidence text if present.
  if (it.evidence_text) return it.evidence_text
  return ''
}
</script>
