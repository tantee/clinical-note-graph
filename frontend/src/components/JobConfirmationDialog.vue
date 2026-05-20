<template>
  <v-dialog v-model="open" max-width="520" persistent>
    <v-card>
      <v-card-title class="text-subtitle-1">
        <v-icon class="mr-2" color="primary">mdi-progress-clock</v-icon>
        {{ headline }}
      </v-card-title>
      <v-card-text>
        <p class="text-body-2 mb-3">{{ body }}</p>

        <!-- Job metadata: copyable jobId + scope chips so the user can
             reference this run later (e.g. via Debug → AI calls). -->
        <div class="d-flex align-center mb-2">
          <span class="text-caption text-grey-darken-1 mr-2" style="min-width: 60px;">Job ID</span>
          <code class="text-body-2 mr-2">{{ jobId }}</code>
          <v-btn size="x-small" variant="text" icon="mdi-content-copy"
                 :loading="copying" aria-label="Copy job id"
                 @click="copyJobId" />
        </div>
        <div v-if="patientId" class="d-flex align-center mb-1">
          <span class="text-caption text-grey-darken-1 mr-2" style="min-width: 60px;">Patient</span>
          <v-chip size="x-small" variant="tonal">{{ patientId }}</v-chip>
        </div>
        <div v-if="encounterId" class="d-flex align-center mb-1">
          <span class="text-caption text-grey-darken-1 mr-2" style="min-width: 60px;">Encounter</span>
          <v-chip size="x-small" variant="tonal">{{ encounterId }}</v-chip>
        </div>
        <div class="d-flex align-center mt-3">
          <span class="text-caption text-grey-darken-1 mr-2" style="min-width: 60px;">Type</span>
          <v-chip size="x-small" variant="tonal" color="primary">{{ typeLabel }}</v-chip>
        </div>

        <v-alert
          type="info" variant="tonal" density="compact" class="mt-3"
          icon="mdi-information-outline"
        >
          Watch progress in the jobs button at the top of the page,
          or by going to <strong>Debug → Jobs</strong>.
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="onSecondary">{{ secondaryLabel }}</v-btn>
        <v-btn color="primary" @click="onPrimary">{{ primaryLabel }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUiStore } from '../stores/ui.js'
import { JOB_TYPE_LABELS } from '../constants/jobs.js'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  jobId: { type: String, required: true },
  type: { type: String, required: true },        // emr_ingest | patient_summary | ...
  patientId: { type: String, default: '' },
  encounterId: { type: String, default: '' },
  // Per-call-site overrides so each entry point can show the right copy +
  // route to the right place. Defaults match the ingest flow.
  headline: { type: String, default: 'Document queued for processing' },
  body: { type: String, default: 'Your work has been queued. You can keep working — we\'ll process it in the background.' },
  primaryLabel: { type: String, default: 'Back to patients' },
  secondaryLabel: { type: String, default: 'Stay on page' },
})
const emit = defineEmits(['update:modelValue', 'primary', 'secondary'])

const ui = useUiStore()
const copying = ref(false)
const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const typeLabel = computed(() => JOB_TYPE_LABELS[props.type] || props.type)

async function copyJobId() {
  copying.value = true
  try {
    await navigator.clipboard.writeText(props.jobId)
    ui.success('Job ID copied')
  } catch {
    ui.error('Copy failed — select the ID manually')
  } finally {
    copying.value = false
  }
}

function onPrimary() {
  emit('primary')
  open.value = false
}
function onSecondary() {
  emit('secondary')
  open.value = false
}
</script>
