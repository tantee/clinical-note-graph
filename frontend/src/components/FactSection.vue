<template>
  <!-- Renders as a fragment of v-list-subheader + v-list-items so the parent
       <v-list> owns the alignment. The Background panel on the right side of
       EncounterDialog uses the same pattern, so both columns share native
       Vuetify list padding instead of fighting it. -->
  <v-list-subheader>
    {{ title }}
    <span class="text-grey-darken-1 ml-2">({{ items.length }})</span>
  </v-list-subheader>
  <v-list-item
    v-for="(it, i) in items"
    :key="it.id || `${title}-${i}`"
    :title="titleFor(it)"
    :subtitle="subtitleFor(it)"
  />
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  // Kept for backwards compatibility — subheader has no icon slot, so we
  // intentionally don't render it. Accept the prop so existing call sites
  // don't trigger a prop-type warning.
  icon: { type: String, default: '' },
  items: { type: Array, required: true },
})

function titleFor(it) {
  const base = it.value || it.name || '(unnamed)'
  return it.normalized_code ? `${base} · ${it.normalized_code}` : base
}

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
