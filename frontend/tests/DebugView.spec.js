import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api/client.js', () => ({
  getDebugSummary: vi.fn().mockResolvedValue({ total_calls: 5, total_tokens: 1200, total_cost_usd: 0.0123, avg_latency_ms: 1500, failures: 1 }),
  getDebugByModel: vi.fn().mockResolvedValue([
    { model: 'gpt-4o-mini', calls: 4, prompt_tokens: 800, completion_tokens: 400, cost_usd: 0.0123, avg_latency_ms: 1500 },
  ]),
  getDebugByDay: vi.fn().mockResolvedValue([]),
  listAiCalls: vi.fn().mockResolvedValue([]),
  listJobs: vi.fn().mockResolvedValue([]),
}))

const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-row': { template: '<div><slot/></div>' },
  'v-col': { template: '<div><slot/></div>' },
  'v-tabs': { template: '<div><slot/></div>' },
  'v-tab': true,
  'v-window': { template: '<div><slot/></div>' },
  'v-window-item': { template: '<div><slot/></div>' },
  'v-data-table': true, 'v-chip': true, 'v-icon': true, 'v-btn': true, 'v-select': true,
  'v-text-field': true, 'v-divider': true, 'v-alert': true, 'v-spacer': true,
  'v-navigation-drawer': true, 'v-pre': true,
  'SectionHeader': { template: '<div><slot/></div>' },
  'BarChart': { template: '<canvas/>' },
  'KpiCard': { template: '<div class="kpi"><span class="kpi-label">{{ label }}</span><span class="kpi-value">{{ value }}</span></div>', props: ['label','value','color'] },
}


describe('DebugView', () => {
  it('renders KPI cards from /api/debug/summary', async () => {
    const DebugView = (await import('../src/views/DebugView.vue')).default
    const w = mount(DebugView, { global: { stubs } })
    await flushPromises()
    expect(w.html()).toContain('5')              // total_calls
    expect(w.html()).toMatch(/1,500|1500/)       // avg_latency_ms (1500 ms)
  })

  it('renders an AI calls table when the calls tab is selected', async () => {
    const { listAiCalls } = await import('../src/api/client.js')
    listAiCalls.mockResolvedValueOnce([
      {
        id: 'c1', created_at: '2026-05-15', model: 'gpt-4o-mini', call_type: 'extract',
        prompt_tokens: 100, completion_tokens: 50, total_tokens: 150, latency_ms: 1200,
        cost_usd: 0.001, error: null, patient_id: 'HN1', job_id: null,
      },
    ])
    const DebugView = (await import('../src/views/DebugView.vue')).default
    const localStubs = {
      ...stubs,
      'v-data-table': {
        template: '<table><tbody><tr v-for="r in items" :key="r.id"><td>{{ r.model }}</td></tr></tbody></table>',
        props: ['headers', 'items', 'loading'],
      },
      'v-navigation-drawer': { template: '<aside><slot/></aside>' },
    }
    const w = mount(DebugView, { global: { stubs: localStubs } })
    await flushPromises()

    const setup = w.vm.$.setupState
    setup.tab = 'calls'
    await setup.loadCalls()
    await flushPromises()

    expect(w.html()).toContain('gpt-4o-mini')
  })
})
