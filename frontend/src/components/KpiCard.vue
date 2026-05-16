<template>
  <v-card>
    <v-card-text>
      <div class="text-caption text-grey-darken-1">{{ label }}</div>
      <div class="text-h5 font-weight-bold mt-1" :class="color ? `text-${color}` : ''">{{ value }}</div>
    </v-card-text>
  </v-card>
</template>
<script setup>
defineProps({ label: String, value: [String, Number], color: String })
</script>
