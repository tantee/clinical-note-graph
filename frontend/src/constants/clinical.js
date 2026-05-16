// Mirrors backend/app/schemas/common.py — keep in sync.
export const REVIEW_STATUS = Object.freeze({
  AI_SUGGESTED: 'ai_suggested',
  HUMAN_CONFIRMED: 'human_confirmed',
  REJECTED: 'rejected',
})

export const REVIEW_STATUSES = Object.values(REVIEW_STATUS)

export const REVIEW_META = {
  [REVIEW_STATUS.AI_SUGGESTED]: { color: 'warning', icon: 'mdi-robot', label: 'AI suggested' },
  [REVIEW_STATUS.HUMAN_CONFIRMED]: { color: 'success', icon: 'mdi-check-circle', label: 'Confirmed' },
  [REVIEW_STATUS.REJECTED]: { color: 'error', icon: 'mdi-close-circle', label: 'Rejected' },
}

export const ENCOUNTER_TYPES = [
  'admission',
  'opd',
  'progress_note',
  'discharge_summary',
  'lab',
  'imaging',
  'operation_note',
  'other',
]

export const ENCOUNTER_META = {
  admission: { color: 'primary', icon: 'mdi-hospital-box' },
  opd: { color: 'secondary', icon: 'mdi-stethoscope' },
  progress_note: { color: 'info', icon: 'mdi-note-edit-outline' },
  discharge_summary: { color: 'success', icon: 'mdi-exit-run' },
  lab: { color: 'teal', icon: 'mdi-flask-outline' },
  imaging: { color: 'purple', icon: 'mdi-radiology-box-outline' },
  operation_note: { color: 'deep-orange', icon: 'mdi-scalpel' },
  other: { color: 'grey', icon: 'mdi-file-document-outline' },
}

export const FACT_TYPE_META = {
  condition: { icon: 'mdi-medical-bag', color: 'deep-orange' },
  medication: { icon: 'mdi-pill', color: 'green' },
  observation: { icon: 'mdi-chart-line', color: 'cyan' },
  procedure: { icon: 'mdi-scalpel', color: 'purple' },
  allergy: { icon: 'mdi-alert-octagon', color: 'red' },
  plan: { icon: 'mdi-clipboard-list-outline', color: 'brown' },
  diagnosis_candidate: { icon: 'mdi-magnify', color: 'indigo' },
  coding_candidate: { icon: 'mdi-barcode', color: 'blue-grey' },
}
