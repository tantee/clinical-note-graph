<template>
  <div class="cng-empty">
    <v-icon :size="40" :color="iconColor || 'grey-darken-1'">{{ icon || 'mdi-tray' }}</v-icon>
    <div class="text-subtitle-1 mt-2">{{ title }}</div>
    <div v-if="hint" class="text-body-2 text-grey-darken-1">{{ hint }}</div>
    <div class="mt-3"><slot /></div>
  </div>
</template>

<script setup>
defineProps({
  icon: String,
  iconColor: String,
  title: { type: String, required: true },
  hint: String,
})
</script>
