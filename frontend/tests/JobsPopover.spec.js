import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../src/api/client.js', () => ({
  listJobs: vi.fn(),
  requeueJob: vi.fn(),
}))

const stubs = {
  'v-menu': {
    // Render the menu activator + content side by side so we can assert
    // both the badge state and the inner job list in one mount.
    template: '<div><slot name="activator" :props="{}"/><slot/></div>',
  },
  'v-card': { template: '<div><slot/></div>' },
  'v-card-title': { template: '<div><slot/></div>' },
  'v-card-actions': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-spacer': true,
  'v-icon': true,
  'v-chip': { template: '<span><slot/></span>' },
  'v-btn': {
    template: '<button :aria-label="ariaLabel" @click="$emit(\'click\')"><slot/></button>',
    props: ['ariaLabel'],
  },
  // v-badge wraps its child icon and renders `content` as a floating
  // overlay. The trigger only mounts the badge when hasActive is true,
  // so this stub mirrors that with a class hook for the assertions.
  'v-badge': {
    template: '<span class="badge" :data-content="content"><slot/></span>',
    props: ['content', 'color', 'location'],
  },
  'v-list': { template: '<ul><slot/></ul>' },
  'v-list-item': {
    template: '<li><slot name="prepend"/><slot/><slot name="append"/></li>',
  },
  'v-list-item-title': { template: '<div><slot/></div>' },
  'v-list-item-subtitle': { template: '<div><slot/></div>' },
}

describe('JobsPopover', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows the active count in the trigger button when jobs are running', async () => {
    const { listJobs } = await import('../src/api/client.js')
    listJobs.mockReset()
    listJobs.mockImplementation((params) => {
      if (params?.status === 'pending,running') {
        return Promise.resolve([
          { job_id: 'j1', type: 'patient_coding', status: 'running', patient_id: 'HN1', progress: {} },
          { job_id: 'j2', type: 'emr_ingest', status: 'pending', patient_id: 'HN2', progress: {} },
        ])
      }
      return Promise.resolve([])
    })

    const Popover = (await import('../src/components/JobsPopover.vue')).default
    const w = mount(Popover, { global: { stubs, plugins: [createPinia()] } })
    await flushPromises()
    // Count "2" appears in the badge wrapping the activator icon.
    const badge = w.find('button[aria-label="Active jobs"] .badge')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-content')).toBe('2')
    const html = w.html()
    expect(html).toContain('Patient coding')
    expect(html).toContain('EMR ingest')
  })

  it('renders the empty state when no jobs are active or failed', async () => {
    const { listJobs } = await import('../src/api/client.js')
    listJobs.mockReset()
    listJobs.mockResolvedValue([])

    const Popover = (await import('../src/components/JobsPopover.vue')).default
    const w = mount(Popover, { global: { stubs, plugins: [createPinia()] } })
    await flushPromises()
    expect(w.html()).toContain('No active jobs')
    // No badge in the activator when there are no active jobs — the
    // outline-only icon renders by itself.
    const badge = w.find('button[aria-label="Active jobs"] .badge')
    expect(badge.exists()).toBe(false)
  })

  it('renders the failed section separately when there are recent failures', async () => {
    const { listJobs } = await import('../src/api/client.js')
    listJobs.mockReset()
    listJobs.mockImplementation((params) => {
      if (params?.status === 'failed') {
        return Promise.resolve([
          { job_id: 'f1', type: 'emr_ingest', status: 'failed', patient_id: 'HN-FAIL', progress: {} },
        ])
      }
      return Promise.resolve([])
    })

    const Popover = (await import('../src/components/JobsPopover.vue')).default
    const w = mount(Popover, { global: { stubs, plugins: [createPinia()] } })
    await flushPromises()
    expect(w.html()).toContain('Recently failed (1)')
    expect(w.html()).toContain('HN-FAIL')
  })
})
