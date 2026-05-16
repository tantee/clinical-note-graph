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
})
