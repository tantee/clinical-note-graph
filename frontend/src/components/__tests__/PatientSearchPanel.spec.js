import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import PatientSearchPanel from '../vector-demo/PatientSearchPanel.vue'

vi.mock('../../api/client.js', () => ({
  searchPatientsByVector: vi.fn(),
}))

import * as api from '../../api/client.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const globalStubs = {
  stubs: {
    'v-row': { template: '<div><slot /></div>' },
    'v-col': { template: '<div><slot /></div>' },
    'v-card': { template: '<div :data-to="JSON.stringify(to)"><slot /></div>', props: ['to'] },
    'v-card-text': { template: '<div><slot /></div>' },
    'v-divider': { template: '<hr />' },
    'v-text-field': { template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />', props: ['modelValue'] },
    'v-alert': { template: '<div role="alert"><slot /></div>' },
    'v-chip': { template: '<span><slot /></span>' },
    'v-spacer': { template: '<span />' },
    EmptyState: { template: '<div data-test="empty">No matches</div>' },
    SectionHeader: { template: '<div><slot /></div>' },
  },
}

async function makeWrapper() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div/>' } },
      { path: '/patients/:id', name: 'patient', component: { template: '<div/>' } },
    ],
  })
  router.push('/')
  await router.isReady()
  return mount(PatientSearchPanel, { global: { plugins: [router], ...globalStubs } })
}

describe('PatientSearchPanel.vue', () => {
  it('renders ranked result cards after search', async () => {
    api.searchPatientsByVector.mockResolvedValue({
      query: 'diabetes',
      embeddingModel: 'mock-embed',
      latencyMs: 12,
      results: [
        { patientId: 'HN-1', name: 'Alpha', score: 0.91,
          snippets: [{ refType: 'note', refId: 'p/n.md', content: 'snippet', score: 0.91 }] },
        { patientId: 'HN-2', name: 'Beta', score: 0.74, snippets: [] },
      ],
    })
    const w = await makeWrapper()
    w.vm.q = 'diabetes'
    await w.vm.submit()
    await flushPromises()
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('Beta')
    expect(api.searchPatientsByVector).toHaveBeenCalledWith('diabetes', 10)
  })

  it('shows EmptyState when no results', async () => {
    api.searchPatientsByVector.mockResolvedValue({
      query: 'nothing', embeddingModel: 'm', latencyMs: 1, results: [],
    })
    const w = await makeWrapper()
    w.vm.q = 'nothing'
    await w.vm.submit()
    await flushPromises()
    expect(w.find('[data-test="empty"]').exists()).toBe(true)
  })
})
