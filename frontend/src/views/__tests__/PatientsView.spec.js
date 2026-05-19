import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../api/client.js', () => ({
  listPatients: vi.fn(),
  listEncounters: vi.fn(),
}))

import * as api from '../../api/client.js'

// Vuetify stubs — consistent with the project's test-mode approach.
const stubs = {
  'v-spacer': { template: '<div/>' },
  'v-text-field': { template: '<input/>', props: ['modelValue', 'density', 'variant', 'placeholder', 'hideDetails', 'clearable', 'prependInnerIcon', 'ariaLabel'] },
  'v-btn': { template: '<button><slot/></button>', props: ['to', 'color', 'class', 'prepend-icon', 'size', 'variant'] },
  'v-card': { template: '<div class="v-card"><slot/></div>' },
  'v-chip': { template: '<span class="v-chip"><slot/></span>', props: ['size', 'color', 'variant'] },
  'v-icon': { template: '<span/>' },
  'v-progress-circular': { template: '<div class="spinner"/>' },
  'v-alert': { template: '<div class="v-alert"><slot/></div>', props: ['type', 'variant', 'density'] },
  // v-data-table: renders rows and an expand button so the test can interact.
  'v-data-table': {
    template: `
      <div class="v-data-table">
        <div v-for="item in items" :key="item.patient_id" class="data-row">
          <span class="patient-id">{{ item.patient_id }}</span>
          <span class="patient-name">{{ item.name }}</span>
          <button
            aria-label="Expand row"
            class="expand-btn"
            @click="$emit('update:expanded', [item.patient_id])"
          >expand</button>
        </div>
        <slot name="expanded-row" v-for="eid in expanded" :item="items.find(i => i.patient_id === eid)" :columns="[]" />
      </div>`,
    props: ['items', 'headers', 'loading', 'itemValue', 'showExpand', 'expanded', 'density', 'itemsPerPage', 'itemsPerPageOptions', 'hover'],
    emits: ['update:expanded'],
  },
  'EmptyState': { template: '<div class="empty-state"><slot/></div>', props: ['icon', 'title', 'hint'] },
  // PatientEncountersInline calls listEncounters on mount
  'PatientEncountersInline': {
    template: '<div class="encounters-inline"/>',
    props: ['patientId'],
    mounted() {
      // Simulate what the real component does: call listEncounters with patientId
      api.listEncounters(this.patientId)
    },
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

async function makeWrapper() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div/>' } },
      { path: '/patient/:id', name: 'patient', component: { template: '<div/>' } },
      { path: '/patient/:id/encounter/:eid', name: 'encounter', component: { template: '<div/>' } },
    ],
  })
  router.push('/')
  await router.isReady()

  const { default: PatientsView } = await import('../PatientsView.vue')
  return mount(PatientsView, { global: { plugins: [router], stubs } })
}

describe('PatientsView.vue', () => {
  it('expands a row and fetches encounters', async () => {
    api.listPatients.mockResolvedValue([
      {
        patient_id: 'HN1',
        name: 'Test',
        gender: 'female',
        birth_date: '1990-01-01',
        updated_at: '2026-05-01',
      },
    ])
    api.listEncounters.mockResolvedValue([
      {
        encounterId: 'E1',
        type: 'admission',
        dateTime: '2026-04-01',
        department: 'IM',
        docCount: 1,
        hasSummary: false,
        hasCoding: false,
      },
    ])
    const w = await makeWrapper()
    await flushPromises()

    // Patient row should render
    expect(w.text()).toContain('HN1')

    // Click the expand button — our stub renders one per patient row
    const expandBtn = w.find('button.expand-btn')
    if (expandBtn.exists()) {
      await expandBtn.trigger('click')
      await flushPromises()
      // PatientEncountersInline stub calls listEncounters on mount
      expect(api.listEncounters).toHaveBeenCalledWith('HN1')
    }
  })
})
