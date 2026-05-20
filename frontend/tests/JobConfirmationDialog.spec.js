import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const stubs = {
  'v-dialog': { template: '<div><slot/></div>' },
  'v-card': { template: '<div><slot/></div>' },
  'v-card-title': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-card-actions': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-spacer': true,
  'v-icon': true,
  'v-chip': { template: '<span><slot/></span>' },
  'v-alert': { template: '<div><slot/></div>' },
  'v-btn': {
    template: '<button :aria-label="ariaLabel" @click="$emit(\'click\')"><slot/></button>',
    props: ['ariaLabel'],
  },
}

describe('JobConfirmationDialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders job id + patient id + type label', async () => {
    const Dialog = (await import('../src/components/JobConfirmationDialog.vue')).default
    const w = mount(Dialog, {
      props: {
        modelValue: true,
        jobId: 'job-abc-123',
        type: 'patient_summary',
        patientId: 'HN-DEMO-1',
      },
      global: { stubs, plugins: [createPinia()] },
    })
    const html = w.html()
    expect(html).toContain('job-abc-123')
    expect(html).toContain('HN-DEMO-1')
    expect(html).toContain('Patient summary')  // JOB_TYPE_LABELS lookup
  })

  it('emits primary / secondary + closes on click', async () => {
    const Dialog = (await import('../src/components/JobConfirmationDialog.vue')).default
    const w = mount(Dialog, {
      props: {
        modelValue: true,
        jobId: 'j1', type: 'emr_ingest', patientId: 'HN1',
        primaryLabel: 'Back to patients',
        secondaryLabel: 'Open patient',
      },
      global: { stubs, plugins: [createPinia()] },
    })
    const buttons = w.findAll('button')
    // First non-copy button is "Open patient" (secondary), second is
    // "Back to patients" (primary). The first button rendered is the
    // copy-icon button with aria-label="Copy job id" — skip it.
    const copy = buttons.find((b) => b.attributes('aria-label') === 'Copy job id')
    expect(copy).toBeTruthy()
    const action = buttons.filter((b) => b.attributes('aria-label') !== 'Copy job id')
    expect(action.length).toBe(2)
    await action[1].trigger('click')  // primary "Back to patients"
    expect(w.emitted('primary')).toBeTruthy()
    expect(w.emitted('update:modelValue')).toBeTruthy()
    expect(w.emitted('update:modelValue')[0]).toEqual([false])
  })

  it('shows the encounter chip when encounterId is provided', async () => {
    const Dialog = (await import('../src/components/JobConfirmationDialog.vue')).default
    const w = mount(Dialog, {
      props: {
        modelValue: true,
        jobId: 'j1', type: 'encounter_summary',
        patientId: 'HN1', encounterId: 'E1',
      },
      global: { stubs, plugins: [createPinia()] },
    })
    expect(w.html()).toContain('E1')
    expect(w.html()).toContain('Encounter summary')
  })
})
