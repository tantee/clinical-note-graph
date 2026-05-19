import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// Mock the API module — listEncounters replaces the raw fetch the component uses
// to load encounter metadata (watch-out from task spec: raw fetch was replaced
// in Task 10 with listEncounters, so we mock the module, not global.fetch).
vi.mock('../../api/client.js', () => ({
  getLatestEncounterSummary: vi.fn(),
  getLatestEncounterCoding: vi.fn(),
  summarizeEncounter: vi.fn(),
  suggestEncounterCoding: vi.fn(),
  listEncounters: vi.fn(),
}))

import * as api from '../../api/client.js'

// Vuetify stubs — the vite config drops the vuetify plugin in test mode,
// so we stub v-* components the same way the existing spec files do.
const stubs = {
  'v-dialog': { template: '<div class="v-dialog"><slot/></div>', props: ['modelValue', 'fullscreen', 'transition', 'scrollable'] },
  'v-toolbar': { template: '<div class="v-toolbar"><slot/></div>', props: ['color', 'density'] },
  'v-toolbar-title': { template: '<div class="v-toolbar-title"><slot/></div>' },
  'v-tabs': { template: '<div class="v-tabs"><slot/></div>', props: ['modelValue', 'color', 'density'] },
  'v-tab': { template: '<div class="v-tab"><slot/></div>', props: ['value', 'prepend-icon'] },
  'v-window': { template: '<div class="v-window"><slot/></div>', props: ['modelValue'] },
  'v-window-item': { template: '<div class="v-window-item"><slot/></div>', props: ['value'] },
  'v-progress-circular': { template: '<div class="spinner"/>' },
  'v-alert': { template: '<div class="v-alert"><slot/></div>', props: ['type', 'variant'] },
  'v-btn': { template: '<button><slot/></button>', props: ['icon', 'variant', 'to', 'loading', 'color', 'prepend-icon'] },
  'v-menu': { template: '<div><slot name="activator" :props="{}"/><slot/></div>' },
  'v-list': { template: '<ul><slot/></ul>', props: ['density'] },
  'v-list-item': { template: '<li><slot/></li>', props: ['title', 'subtitle', 'prepend-icon'] },
  'v-list-subheader': { template: '<div class="subheader"><slot/></div>' },
  'v-spacer': { template: '<div/>' },
  'v-icon': { template: '<span/>', props: ['end'] },
  'v-row': { template: '<div class="v-row"><slot/></div>' },
  'v-col': { template: '<div class="v-col"><slot/></div>', props: ['cols', 'md'] },
  'v-card': { template: '<div class="v-card"><slot/></div>' },
  'v-card-text': { template: '<div class="v-card-text"><slot/></div>' },
  'v-card-actions': { template: '<div class="v-card-actions"><slot/></div>' },
  'v-divider': { template: '<hr/>' },
  'SummaryCard': { template: '<div class="summary-card"/>', props: ['value'] },
  'CodingCard': { template: '<div class="coding-card"/>', props: ['value'] },
  'SectionHeader': { template: '<div class="section-header">{{ title }}</div>', props: ['title', 'icon'] },
  'EmptyState': { template: '<div class="empty-state">{{ title }}</div>', props: ['icon', 'title'] },
  'GraphView': { template: '<div class="graph-view"/>', props: ['scope', 'patientId', 'encounterIds', 'height'] },
}

const ENCOUNTER_LIST = [
  {
    encounterId: 'E1',
    type: 'admission',
    dateTime: '2026-04-01T08:00:00+00:00',
    department: 'IM',
    provider: 'Dr A',
    docCount: 1,
    hasSummary: false,
    hasCoding: false,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  // Default: listEncounters returns a list with E1
  api.listEncounters.mockResolvedValue(ENCOUNTER_LIST)
})

async function makeWrapper(props) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div/>' } },
      { path: '/patient/:id', name: 'patient', component: { template: '<div/>' } },
      {
        path: '/patient/:id/encounter/:eid',
        name: 'encounter',
        component: { template: '<div/>' },
        props: true,
      },
    ],
  })
  router.push('/patient/HN1/encounter/E1')
  await router.isReady()

  const { default: EncounterDialog } = await import('../EncounterDialog.vue')
  return mount(EncounterDialog, {
    props: { patientId: 'HN1', eid: 'E1', ...props },
    global: { plugins: [router], stubs },
  })
}

describe('EncounterDialog.vue', () => {
  it('renders header from encounter list', async () => {
    api.getLatestEncounterSummary.mockResolvedValue(null)
    api.getLatestEncounterCoding.mockResolvedValue(null)
    const w = await makeWrapper()
    await flushPromises()
    // The toolbar title shows "type · dateTime" (department is no longer in the toolbar)
    expect(w.text()).toContain('admission')
  })

  it('shows "Regenerate summary" when latest summary exists', async () => {
    api.getLatestEncounterSummary.mockResolvedValue({
      id: 'ps-1',
      type: 'discharge_summary',
      markdown: '# hi',
      createdAt: '2026-05-01T00:00:00Z',
    })
    api.getLatestEncounterCoding.mockResolvedValue(null)
    const w = await makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('Regenerate summary')
  })

  it('shows error when encounter not found', async () => {
    api.getLatestEncounterSummary.mockResolvedValue(null)
    api.getLatestEncounterCoding.mockResolvedValue(null)
    // Override: listEncounters returns empty list → no match for E1
    api.listEncounters.mockResolvedValue([])
    const w = await makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('Encounter not found')
  })
})
