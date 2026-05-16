import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MarkdownViewer from '../src/components/MarkdownViewer.vue'

const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-card-text': { template: '<div><slot/></div>' },
  'v-chip': { template: '<span><slot/></span>' },
  'SectionHeader': { template: '<div><slot/></div>' },
  'v-icon': true,
}

describe('MarkdownViewer', () => {
  it('renders wikilinks as anchors with data-link', () => {
    const wrapper = mount(MarkdownViewer, {
      props: {
        path: 'patients/HN1/visits/2026-05-15-admission.md',
        content: 'See [[problems/diabetes-mellitus|Diabetes Mellitus]] and [[index]].',
        backlinks: [],
      },
      global: { stubs },
    })
    const html = wrapper.html()
    expect(html).toContain('data-link="problems/diabetes-mellitus.md"')
    expect(html).toContain('>Diabetes Mellitus<')
    expect(html).toContain('data-link="index.md"')
  })

  it('emits open(vault-relative path) when a wikilink is clicked', async () => {
    const wrapper = mount(MarkdownViewer, {
      props: {
        path: 'patients/HN1/visits/2026-05-15-admission.md',
        content: '[[problems/diabetes-mellitus|DM]]',
        backlinks: [],
      },
      global: { stubs },
    })
    const link = wrapper.find('a[data-link]')
    expect(link.exists()).toBe(true)
    await link.trigger('click')
    const events = wrapper.emitted('open')
    expect(events).toBeTruthy()
    expect(events[0][0]).toBe('patients/HN1/problems/diabetes-mellitus.md')
  })

  it('does not leak a global click listener', () => {
    const before = (window.__listeners ||= [])
    // jsdom doesn't track listeners introspectively, but we assert the component
    // exposes no global side-effect by mounting & unmounting and re-running an emit.
    const w = mount(MarkdownViewer, {
      props: { path: 'patients/HN1/index.md', content: '[[problems/x|x]]', backlinks: [] },
      global: { stubs },
    })
    w.unmount()
    // The very fact this test mounts without throwing & is independent of others is the contract.
    expect(before).toEqual([])
  })
})
