import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import PatientSearchInput from '../PatientSearchInput.vue'

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
    'v-menu': { template: '<div><slot name="activator" :props="{}" /><slot /></div>' },
    'v-text-field': { template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />', props: ['modelValue'] },
    'v-card': { template: '<div><slot /></div>' },
    'v-list': { template: '<div><slot /></div>' },
    'v-list-item': { template: '<div :data-to="JSON.stringify(to)"><slot /></div>', props: ['to'] },
    'v-list-item-title': { template: '<div><slot /></div>' },
    'v-list-item-subtitle': { template: '<div><slot /></div>' },
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
  return mount(PatientSearchInput, { global: { plugins: [router], ...globalStubs } })
}

describe('PatientSearchInput.vue', () => {
  it('debounces and calls searchPatientsByVector after 300ms', async () => {
    vi.useFakeTimers()
    api.searchPatientsByVector.mockResolvedValue({ results: [] })
    const w = await makeWrapper()
    w.vm.onInput('diabetes')
    expect(api.searchPatientsByVector).not.toHaveBeenCalled()
    vi.advanceTimersByTime(310)
    await flushPromises()
    expect(api.searchPatientsByVector).toHaveBeenCalledWith('diabetes', 8, expect.anything())
    vi.useRealTimers()
  })

  it('does not fetch for queries shorter than 2 chars', async () => {
    vi.useFakeTimers()
    const w = await makeWrapper()
    w.vm.onInput('a')
    vi.advanceTimersByTime(500)
    await flushPromises()
    expect(api.searchPatientsByVector).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})
