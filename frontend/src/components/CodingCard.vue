<template>
  <v-card v-if="value" class="mt-4">
    <SectionHeader title="Coding suggestion" icon="mdi-tag-text-outline">
      <template #actions><v-chip size="x-small" color="warning" variant="tonal">AI-assisted</v-chip></template>
    </SectionHeader>
    <v-divider />
    <v-card-text>
      <!-- Primary -->
      <div v-if="value.primaryDiagnosis" class="mb-2">
        <span class="text-body-2 text-grey-darken-1">Primary</span><br />
        <strong>{{ value.primaryDiagnosis.condition }}</strong>
        <v-chip v-if="value.primaryDiagnosis.icd10" size="x-small" class="ml-2">ICD-10 {{ value.primaryDiagnosis.icd10 }}</v-chip>
        <v-chip v-if="value.primaryDiagnosis.snomed" size="x-small" class="ml-1">SNOMED {{ value.primaryDiagnosis.snomed }}</v-chip>
        <div v-if="value.primaryDiagnosis.rationale" class="text-caption text-grey-darken-1 mt-1">
          {{ value.primaryDiagnosis.rationale }}
        </div>
      </div>

      <!-- Secondary -->
      <div v-if="value.secondaryDiagnoses?.length">
        <div class="text-body-2 text-grey-darken-1 mb-1 mt-3">Secondary</div>
        <div v-for="(d, i) in value.secondaryDiagnoses" :key="`sec-${i}`" class="mb-1">
          {{ d.condition }}
          <v-chip v-if="d.icd10" size="x-small" class="ml-1">ICD-10 {{ d.icd10 }}</v-chip>
          <v-chip v-if="d.snomed" size="x-small" class="ml-1">SNOMED {{ d.snomed }}</v-chip>
        </div>
      </div>

      <!-- Complications + Comorbidities — previously hidden -->
      <div v-if="value.complications?.length">
        <div class="text-body-2 text-grey-darken-1 mb-1 mt-3">Complications</div>
        <div v-for="(d, i) in value.complications" :key="`com-${i}`" class="mb-1">
          {{ d.condition }}
          <v-chip v-if="d.icd10" size="x-small" class="ml-1">ICD-10 {{ d.icd10 }}</v-chip>
          <v-chip v-if="d.snomed" size="x-small" class="ml-1">SNOMED {{ d.snomed }}</v-chip>
        </div>
      </div>
      <div v-if="value.comorbidities?.length">
        <div class="text-body-2 text-grey-darken-1 mb-1 mt-3">Comorbidities</div>
        <div v-for="(d, i) in value.comorbidities" :key="`co-${i}`" class="mb-1">
          {{ d.condition }}
          <v-chip v-if="d.icd10" size="x-small" class="ml-1">ICD-10 {{ d.icd10 }}</v-chip>
          <v-chip v-if="d.snomed" size="x-small" class="ml-1">SNOMED {{ d.snomed }}</v-chip>
        </div>
      </div>

      <!-- Code candidates — previously hidden. The full list of suggested
           codes with the condition each one is for. -->
      <div v-if="value.codingCandidates?.length">
        <div class="text-body-2 text-grey-darken-1 mb-1 mt-3">
          Code candidates ({{ value.codingCandidates.length }})
        </div>
        <v-table density="compact">
          <thead>
            <tr>
              <th class="text-left">System</th>
              <th class="text-left">Code</th>
              <th class="text-left">Display</th>
              <th class="text-left">For</th>
              <th class="text-left">Conf</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, i) in value.codingCandidates" :key="`cand-${i}`">
              <td>{{ c.system }}</td>
              <td><code>{{ c.code }}</code></td>
              <td>{{ c.display }}</td>
              <td>{{ c.forCondition }}</td>
              <td>{{ c.confidence?.toFixed(2) }}</td>
            </tr>
          </tbody>
        </v-table>
      </div>

      <!-- Warnings — previously hidden. Often the most useful section
           when the model declines to assign codes outright. -->
      <div v-if="value.warnings?.length" class="mt-3">
        <div class="text-body-2 text-grey-darken-1 mb-1">
          AI warnings ({{ value.warnings.length }})
        </div>
        <v-alert
          v-for="(w, i) in value.warnings" :key="`warn-${i}`"
          type="warning" variant="tonal" density="compact" class="mb-2"
        >{{ w }}</v-alert>
      </div>

      <!-- Evidence — surface so reviewers can trace each diagnosis back
           to the source facts the AI saw. -->
      <div v-if="value.evidence?.length" class="mt-3">
        <div class="text-body-2 text-grey-darken-1 mb-1">
          Evidence ({{ value.evidence.length }})
        </div>
        <v-expansion-panels variant="accordion" multiple>
          <v-expansion-panel
            v-for="(e, i) in value.evidence" :key="`ev-${i}`"
            :title="e.condition || e.title || `Evidence ${i + 1}`"
          >
            <template #text>
              <div v-if="e.evidence" class="text-caption">{{ e.evidence }}</div>
              <pre v-else class="cng-mono" style="white-space:pre-wrap; font-size:0.8rem;">{{ JSON.stringify(e, null, 2) }}</pre>
            </template>
          </v-expansion-panel>
        </v-expansion-panels>
      </div>

      <!-- Empty-state: model returned but produced no codes anywhere -->
      <v-alert
        v-if="isEmpty"
        type="info" variant="tonal" density="compact" class="mt-3"
      >
        The model returned no diagnoses or code candidates for this
        patient. See the warnings above for why, or try regenerating
        with a stronger <code>AI_MODEL_CODING</code> (Gemini 2.5 Flash
        or Claude 3.5 Sonnet are more reliable at structured coding
        output than reasoning models on complex cases).
      </v-alert>

      <v-alert v-if="value.disclaimer" type="warning" variant="tonal" density="compact" class="mt-3">
        {{ value.disclaimer }}
      </v-alert>
    </v-card-text>
  </v-card>
</template>

<script setup>
import { computed } from 'vue'
import SectionHeader from './SectionHeader.vue'

const props = defineProps({
  value: { type: Object, default: null },
})

const isEmpty = computed(() => {
  if (!props.value) return false
  return (
    !props.value.primaryDiagnosis
    && !props.value.secondaryDiagnoses?.length
    && !props.value.complications?.length
    && !props.value.comorbidities?.length
    && !props.value.codingCandidates?.length
  )
})
</script>
