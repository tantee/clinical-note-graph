<template>
  <div class="cng-card-section-title text-subtitle-1 pa-4 pb-2">
    <v-icon v-if="icon" :color="color">{{ icon }}</v-icon>
    <span>{{ title }}</span>
    <slot name="meta" />
    <v-spacer />
    <slot name="actions" />
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  icon: String,
  color: { type: String, default: 'primary' },
})
</script>
