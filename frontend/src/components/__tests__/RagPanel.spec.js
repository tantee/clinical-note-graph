import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import RagPanel from '../vector-demo/RagPanel.vue'

vi.mock('../../api/client.js', () => ({
  listPatients: vi.fn(),
  ragAsk: vi.fn(),
}))

import * as api from '../../api/client.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.listPatients.mockResolvedValue([
    { patient_id: 'HN-1', name: 'Alpha' },
  ])
})

const globalStubs = {
  stubs: {
    'v-row': { template: '<div><slot /></div>' },
    'v-col': { template: '<div><slot /></div>' },
    'v-card': { template: '<div><slot /></div>' },
    'v-card-text': { template: '<div><slot /></div>' },
    'v-divider': { template: '<hr />' },
    'v-autocomplete': {
      template: '<select @change="$emit(\'update:modelValue\', $event.target.value)"><option value="">--</option><option v-for="i in items" :key="i.patient_id" :value="i.patient_id">{{ i.display }}</option></select>',
      props: ['items', 'modelValue'],
    },
    'v-btn-toggle': { template: '<div><slot /></div>' },
    'v-btn': { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled', 'loading'] },
    'v-spacer': { template: '<span />' },
    'v-icon': { template: '<i><slot /></i>' },
    'v-textarea': { template: '<textarea :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" />', props: ['modelValue', 'disabled'] },
    'v-alert': { template: '<div role="alert"><slot /></div>' },
    'v-chip': { template: '<span><slot /></span>' },
    'v-list': { template: '<div><slot /></div>' },
    'v-list-item': { template: '<div><slot /></div>' },
    'v-list-item-title': { template: '<div><slot /></div>' },
    'v-list-item-subtitle': { template: '<div><slot /></div>' },
    EmptyState: { template: '<div data-test="empty"><slot /></div>' },
    SectionHeader: { template: '<div><slot /></div>' },
    CitationBadge: { template: '<span data-test="citation">[{{ citation.n }}]</span>', props: ['citation', 'patientId'] },
  },
}

async function makeWrapper() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div/>' } }],
  })
  router.push('/')
  await router.isReady()
  return mount(RagPanel, { global: { plugins: [router], ...globalStubs } })
}

describe('RagPanel.vue', () => {
  it('Ask button is disabled when no patient selected', async () => {
    const w = await makeWrapper()
    await flushPromises()
    const askBtn = w.findAll('button').find((b) => b.text().includes('Ask'))
    expect(askBtn.attributes('disabled')).toBeDefined()
  })

  it('renders answer + citation badges after a successful ragAsk', async () => {
    api.ragAsk.mockResolvedValue({
      patientId: 'HN-1', question: 'q',
      answer: 'It is hypertension [1].',
      citations: [
        { n: 1, refType: 'note', refId: 'p/n1.md', content: '...', score: 0.9, cited: true },
        { n: 2, refType: 'note', refId: 'p/n2.md', content: '...', score: 0.7, cited: false },
      ],
      modelUsed: 'mock', embeddingModel: 'mock-embed', latencyMs: 5,
    })
    const w = await makeWrapper()
    await flushPromises()
    // Manually drive the component state since the stubs don't fully simulate v-autocomplete:
    w.vm.patientId = 'HN-1'
    w.vm.question = 'What conditions?'
    await w.vm.submit()
    await flushPromises()
    expect(w.text()).toContain('hypertension')
    expect(w.findAll('[data-test="citation"]').length).toBeGreaterThan(0)
  })

  it('chat mode appends question + answer to history', async () => {
    api.ragAsk.mockResolvedValue({
      patientId: 'HN-1', question: 'q', answer: 'Yes.',
      citations: [], modelUsed: 'mock', embeddingModel: 'm', latencyMs: 1,
    })
    const w = await makeWrapper()
    await flushPromises()
    w.vm.patientId = 'HN-1'
    w.vm.mode = 'chat'
    w.vm.question = 'Hello?'
    await w.vm.submit()
    await flushPromises()
    expect(w.vm.history).toHaveLength(2)
    expect(w.vm.history[0].role).toBe('user')
    expect(w.vm.history[1].role).toBe('assistant')
  })
})
