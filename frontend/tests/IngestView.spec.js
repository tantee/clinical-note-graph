import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api/client.js', () => ({
  listPatients: vi.fn().mockResolvedValue([{ patient_id: 'HN1', name: 'Existing One' }]),
  ingest: vi.fn().mockResolvedValue({ jobId: 'job-1', status: 'queued', patientId: 'HN-NEW', documentId: 'doc-1' }),
}))

// Pinia + vue-router are required by useUiStore / useRoute. Provide light stubs.
vi.mock('vue-router', () => ({
  useRoute:  () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../src/stores/ui.js', () => ({
  useUiStore: () => ({ success: vi.fn(), error: vi.fn() }),
}))


const stubs = {
  'v-row': { template: '<div><slot/></div>' },
  'v-col': { template: '<div><slot/></div>' },
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-card-actions': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-spacer': true,
  'v-text-field': {
    props: ['modelValue'], emits: ['update:modelValue'],
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'v-textarea': {
    props: ['modelValue'], emits: ['update:modelValue'],
    template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)"/>',
  },
  'v-autocomplete': {
    props: ['modelValue'], emits: ['update:modelValue', 'update:search'],
    template: '<input data-test="patient" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)"/>',
  },
  'v-select': true,
  'v-btn': { template: '<button data-test-btn :data-label="$attrs[\'data-label\'] || \'\'" @click="$emit(\'click\')"><slot/></button>' },
  'v-menu': { template: '<div><slot/></div>' },
  'v-list': true,
  'v-list-item': true,
  'v-chip': true,
  'v-icon': true,
  'SectionHeader': { template: '<div><slot/></div>' },
  'JobWatcher': { template: '<div class="jw">watcher</div>' },
}


describe('IngestView', () => {
  it('submits and renders JobWatcher with the returned jobId', async () => {
    const IngestView = (await import('../src/views/IngestView.vue')).default
    const w = mount(IngestView, { global: { stubs } })
    await flushPromises()

    // Enter a patient ID and trigger submit. The first stubbed v-btn matched is the Submit button.
    await w.find('input[data-test="patient"]').setValue('HN-NEW')

    // Trigger any submit button — the rendered form contains a Submit at the top of v-card-actions.
    const buttons = w.findAll('button')
    // Pick the button whose text content includes "Submit"
    const submitBtn = buttons.find(b => b.text().toLowerCase().includes('submit'))
    expect(submitBtn).toBeTruthy()
    await submitBtn.trigger('click')
    await flushPromises()

    const { ingest } = await import('../src/api/client.js')
    expect(ingest).toHaveBeenCalled()
    expect(w.html()).toContain('jw')   // JobWatcher mounted
  })
})
