<template>
  <v-card v-if="value" class="mt-4">
    <SectionHeader title="Coding suggestion" icon="mdi-medical-bag-outline">
      <template #actions><v-chip size="x-small" color="warning" variant="tonal">AI-assisted</v-chip></template>
    </SectionHeader>
    <v-divider />
    <v-card-text>
      <div v-if="value.primaryDiagnosis" class="mb-2">
        <span class="text-body-2 text-grey-darken-1">Primary</span><br />
        <strong>{{ value.primaryDiagnosis.condition }}</strong>
        <v-chip v-if="value.primaryDiagnosis.icd10" size="x-small" class="ml-2">ICD-10 {{ value.primaryDiagnosis.icd10 }}</v-chip>
        <v-chip v-if="value.primaryDiagnosis.snomed" size="x-small" class="ml-1">SNOMED {{ value.primaryDiagnosis.snomed }}</v-chip>
      </div>
      <div v-if="value.secondaryDiagnoses?.length">
        <div class="text-body-2 text-grey-darken-1 mb-1 mt-3">Secondary</div>
        <div v-for="(d, i) in value.secondaryDiagnoses" :key="i" class="mb-1">
          {{ d.condition }}
          <v-chip v-if="d.icd10" size="x-small" class="ml-1">ICD-10 {{ d.icd10 }}</v-chip>
          <v-chip v-if="d.snomed" size="x-small" class="ml-1">SNOMED {{ d.snomed }}</v-chip>
        </div>
      </div>
      <v-alert v-if="value.disclaimer" type="warning" variant="tonal" density="compact" class="mt-3">
        {{ value.disclaimer }}
      </v-alert>
    </v-card-text>
  </v-card>
</template>

<script setup>
import SectionHeader from './SectionHeader.vue'

defineProps({
  value: { type: Object, default: null },
})
</script>
