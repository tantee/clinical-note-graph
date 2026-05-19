import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import GraphView from '../GraphView.vue'

// Stub vis-network entirely — we're testing the Vue surface, not the canvas.
vi.mock('vis-network/standalone/esm/vis-network', () => ({
  Network: vi.fn(() => ({ fit: vi.fn(), destroy: vi.fn() })),
  DataSet: vi.fn((items) => items),
}))

vi.mock('../../api/client.js', () => ({
  getGraph: vi.fn(),
  listEncounters: vi.fn(),
}))

import * as api from '../../api/client.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.listEncounters.mockResolvedValue([])
})

const globalStubs = {
  stubs: {
    'v-card': { template: '<div><slot /></div>' },
    'v-chip-group': { template: '<div><slot /></div>' },
    'v-chip': { template: '<button :data-value="value"><slot /></button>', props: ['value'] },
    'v-btn': { template: '<button :aria-label="ariaLabel" @click="$emit(\'click\')"><slot /></button>', props: ['ariaLabel'] },
    'v-divider': { template: '<hr />' },
    'v-spacer': { template: '<span />' },
    'v-alert': { template: '<div role="alert"><slot /></div>' },
    'v-navigation-drawer': { template: '<div data-test="filter-drawer" v-if="modelValue"><slot /></div>', props: ['modelValue'] },
    'v-list': { template: '<div><slot /></div>' },
    'v-list-item': { template: '<div><slot /></div>' },
    'v-list-subheader': { template: '<div><slot /></div>' },
    'v-switch': { template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', !modelValue)" />', props: ['modelValue'] },
    'v-radio-group': { template: '<div><slot /></div>' },
    'v-radio': { template: '<label><input type="radio" :value="value" /><slot />{{ label }}</label>', props: ['value', 'label'] },
    'v-dialog': { template: '<div v-if="modelValue" data-test="pick-dialog"><slot /></div>', props: ['modelValue'] },
    'v-text-field': { template: '<input :value="modelValue" />', props: ['modelValue'] },
    'v-icon': { template: '<i><slot /></i>' },
    'v-card-text': { template: '<div><slot /></div>' },
    'v-card-actions': { template: '<div><slot /></div>' },
    'v-progress-circular': { template: '<span>loading</span>' },
    EmptyState: { template: '<div data-test="empty-state"><slot /></div>' },
  },
}

describe('GraphView.vue', () => {
  it('fetches graph on mount with patient scope defaults', async () => {
    api.getGraph.mockResolvedValue({ nodes: [], edges: [] })
    mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'patient' },
      global: globalStubs,
    })
    await flushPromises()
    expect(api.getGraph).toHaveBeenCalledTimes(1)
    const [pid, opts] = api.getGraph.mock.calls[0]
    expect(pid).toBe('HN-1')
    expect(opts.scope).toBe('patient')
    expect(opts.dedupe).toBe(true)
  })

  it('hides scope chip group when scope is encounter', async () => {
    api.getGraph.mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'encounter', encounterIds: ['E1'] },
      global: globalStubs,
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Encounter scope')
  })

  it('renders oversized banner when getGraph rejects with 422', async () => {
    const err = new Error('too large')
    err.response = { status: 422, data: { detail: { detail: 'Graph too large; narrow the scope', nodeCount: 783 } } }
    api.getGraph.mockRejectedValue(err)
    const wrapper = mount(GraphView, {
      props: { patientId: 'HN-1', scope: 'patient' },
      global: globalStubs,
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Graph too large')
    expect(wrapper.text()).toContain('783')
  })
})
