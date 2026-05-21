import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// Mock the API module — getEncounterFacts is the new single-call source of
// truth for the dialog's left column (encounter, thisEncounter, background,
// documents). The older path that went through listEncounters is gone.
vi.mock('../../api/client.js', () => ({
  getEncounterFacts: vi.fn(),
  getLatestEncounterSummary: vi.fn(),
  getLatestEncounterCoding: vi.fn(),
  summarizeEncounter: vi.fn(),
  suggestEncounterCoding: vi.fn(),
  // Powering the new Notes / EMR-vs-facts / AI-output tabs added in the
  // tab-strip refactor — default mocks return empty so the existing
  // assertions that focus on the toolbar stay valid.
  getNotes: vi.fn(),
  getNote: vi.fn(),
  getDocument: vi.fn(),
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
  'FactSection': { template: '<div class="fact-section">{{ title }} ({{ items.length }})</div>', props: ['title', 'icon', 'items'] },
  'MarkdownViewer': { template: '<div class="markdown-viewer"/>', props: ['path', 'content', 'backlinks'] },
  'v-chip': { template: '<span><slot/></span>', props: ['size', 'variant', 'color'] },
  'v-data-table': { template: '<table/>', props: ['headers', 'items'] },
}

const FACTS_E1 = {
  encounter: {
    encounterId: 'E1',
    patientId: 'HN1',
    type: 'admission',
    dateTime: '2026-04-01T08:00:00+00:00',
    department: 'IM',
    provider: 'Dr A',
  },
  thisEncounter: {
    problems: [], medications: [], observations: [], procedures: [],
    plans: [], allergies: [], diagnoses: [], codingCandidates: [],
  },
  background: { chronicProblems: [], homeMedications: [], knownAllergies: [] },
  documents: [],
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  // Default: getEncounterFacts returns the E1 shape above.
  api.getEncounterFacts.mockResolvedValue(FACTS_E1)
  // Notes / document fetches fire on mount as part of fetchAll(); stub
  // empty defaults so the existing tests don't have to mock them
  // explicitly. Tests that exercise Notes/EMR-vs-facts override locally.
  api.getNotes.mockResolvedValue({ files: [] })
  api.getNote.mockResolvedValue({ path: '', content: '', backlinks: [] })
  api.getDocument.mockResolvedValue({ document: {}, facts: [], aiOutput: null })
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
    // The facts endpoint returns 404 when the encounter doesn't belong to
    // this patient. axios rejects with an error carrying response.status.
    api.getEncounterFacts.mockRejectedValue({ response: { status: 404 } })
    const w = await makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('Encounter not found')
  })

  it('renders thisEncounter facts in the left column without a summary', async () => {
    api.getLatestEncounterSummary.mockResolvedValue(null)
    api.getLatestEncounterCoding.mockResolvedValue(null)
    api.getEncounterFacts.mockResolvedValue({
      ...FACTS_E1,
      thisEncounter: {
        ...FACTS_E1.thisEncounter,
        problems: [{ id: 'f-1', value: 'Pneumonia', normalized_code: 'J18.9' }],
        observations: [{ id: 'f-2', value: 'HbA1c', extra: { value: '7.4', unit: '%' } }],
      },
    })
    const w = await makeWrapper()
    await flushPromises()
    // FactSection stub renders "<title> (<count>)" — assert both sections
    // appeared with the right counts so we know thisEncounter actually
    // reached the template, not just the empty state.
    const txt = w.text()
    expect(txt).toContain('Problems (1)')
    expect(txt).toContain('Observations (1)')
    expect(txt).not.toContain('This encounter has no extracted facts')
  })
})
