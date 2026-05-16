import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api/client.js', () => ({
  getJob: vi.fn(),
}))

const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-progress-linear': true,
  'v-icon': true,
  'v-chip': { template: '<span><slot/></span>' },
  'v-btn': { template: '<button @click="$emit(\'click\')"><slot/></button>' },
  'v-divider': true,
  'SectionHeader': { template: '<div><slot/></div>' },
}


describe('JobWatcher', () => {
  it('polls until completed then emits done', async () => {
    const { getJob } = await import('../src/api/client.js')
    getJob.mockReset()
    getJob
      .mockResolvedValueOnce({ status: 'running', progress: { stage_persisted: { at: 'now' } } })
      .mockResolvedValueOnce({ status: 'completed', result: { patientId: 'HN1' }, progress: {} })

    vi.useFakeTimers()
    const JobWatcher = (await import('../src/components/JobWatcher.vue')).default
    const w = mount(JobWatcher, { props: { jobId: 'abc', intervalMs: 10 }, global: { stubs } })

    await flushPromises()
    expect(getJob).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(15)
    await flushPromises()

    expect(getJob).toHaveBeenCalledTimes(2)
    const done = w.emitted('done')
    expect(done).toBeTruthy()
    expect(done[0][0]).toMatchObject({ patientId: 'HN1' })
    vi.useRealTimers()
  })

  it('emits failed and offers retry on failure', async () => {
    const { getJob } = await import('../src/api/client.js')
    getJob.mockReset()
    getJob.mockResolvedValueOnce({ status: 'failed', error: 'boom', progress: {} })

    const JobWatcher = (await import('../src/components/JobWatcher.vue')).default
    const w = mount(JobWatcher, { props: { jobId: 'fail1', intervalMs: 10 }, global: { stubs } })
    await flushPromises()
    expect(w.emitted('failed')).toBeTruthy()
    expect(w.html()).toContain('boom')
  })

  it('renders stages strip with stage_persisted marked done', async () => {
    const { getJob } = await import('../src/api/client.js')
    getJob.mockReset()
    getJob.mockResolvedValueOnce({ status: 'running', progress: { stage_persisted: { at: 'now' } } })

    const JobWatcher = (await import('../src/components/JobWatcher.vue')).default
    const w = mount(JobWatcher, { props: { jobId: 'x', intervalMs: 999 }, global: { stubs } })
    await flushPromises()
    expect(w.html()).toContain('persisted')   // stage label is shown (stage_persisted → "persisted")
    expect(w.find('.stage.done').exists()).toBe(true)
  })
})
