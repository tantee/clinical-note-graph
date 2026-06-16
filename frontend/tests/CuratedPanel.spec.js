import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../src/api/client.js', () => ({
  getCurated: vi.fn(),
  createCurated: vi.fn(),
  updateCurated: vi.fn(),
  deleteCurated: vi.fn(),
  restoreCurated: vi.fn(),
}))

const ROW = {
  id: 'cur1', type: 'medication', displayValue: 'Paclitaxel',
  startDate: '2026-01-10', startQualifier: 'exact', stopDate: null,
  stopQualifier: 'ongoing', scheduleText: 'q3wk x 6 cycles', status: 'start',
  recordState: 'active', reviewStatus: 'ai_suggested', humanEditedFields: [],
}

// Mirror the stub approach from JobsPopover.spec.js so Vuetify components
// render as simple HTML elements — no Vuetify plugin needed.
const stubs = {
  'v-card': { template: '<div><slot/></div>' },
  'v-card-text': { template: '<div><slot/></div>' },
  'v-card-actions': { template: '<div><slot/></div>' },
  'v-divider': true,
  'v-spacer': true,
  'v-icon': true,
  'v-btn': {
    template: '<button :data-test="dataTest" :aria-label="ariaLabel" @click="$emit(\'click\')"><slot/></button>',
    props: ['dataTest', 'ariaLabel', 'icon', 'size', 'variant', 'color', 'prependIcon'],
    emits: ['click'],
  },
  'v-chip': { template: '<span><slot/></span>' },
  'v-list': { template: '<ul><slot/></ul>' },
  'v-list-item': {
    template: '<li><slot name="prepend"/><slot/><slot name="append"/></li>',
  },
  'v-list-item-title': { template: '<div><slot/></div>' },
  'v-list-item-subtitle': { template: '<div><slot/></div>' },
  'v-dialog': {
    template: '<div v-if="modelValue"><slot/></div>',
    props: ['modelValue', 'maxWidth'],
  },
  'v-text-field': {
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue', 'label'],
    emits: ['update:modelValue'],
  },
  'v-select': {
    template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot/></select>',
    props: ['modelValue', 'items', 'label'],
    emits: ['update:modelValue'],
  },
  SectionHeader: {
    template: '<div><slot name="actions"/><slot/></div>',
    props: ['title', 'icon', 'color'],
  },
  EmptyState: {
    template: '<div>{{ title }}</div>',
    props: ['icon', 'title', 'hint', 'iconColor'],
  },
}

describe('CuratedPanel', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => vi.restoreAllMocks())

  it('lists items from the API with a rendered date range', async () => {
    const { getCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    expect(getCurated).toHaveBeenCalledWith('HN1', 'medication', undefined)
    expect(w.text()).toContain('Paclitaxel')
    expect(w.text()).toContain('2026-01-10 → ongoing')
  })

  it('calls deleteCurated when a row is removed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { getCurated, deleteCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    deleteCurated.mockResolvedValue({ id: 'cur1', recordState: 'dismissed' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-delete"]').trigger('click')
    await flushPromises()
    expect(deleteCurated).toHaveBeenCalledWith('cur1')
  })

  it('does not call deleteCurated when confirm is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { getCurated, deleteCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    deleteCurated.mockResolvedValue({ id: 'cur1', recordState: 'dismissed' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-delete"]').trigger('click')
    await flushPromises()
    expect(deleteCurated).not.toHaveBeenCalled()
  })

  it('reloads when patientId prop changes', async () => {
    const { getCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    expect(getCurated).toHaveBeenCalledWith('HN1', 'medication', undefined)
    getCurated.mockClear()
    await w.setProps({ patientId: 'HN2' })
    await flushPromises()
    expect(getCurated).toHaveBeenCalledWith('HN2', 'medication', undefined)
  })

  it('submits a manual insert', async () => {
    const { getCurated, createCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [] })
    createCurated.mockResolvedValue({ ...ROW, origin: 'human' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-add"]').trigger('click')
    w.vm.form.displayValue = 'Warfarin'
    await w.vm.save()
    await flushPromises()
    expect(createCurated).toHaveBeenCalledWith('HN1', expect.objectContaining({
      type: 'medication', displayValue: 'Warfarin',
    }))
  })

  it('loads dismissed items and restores one', async () => {
    const { getCurated, restoreCurated } = await import('../src/api/client.js')
    const DISMISSED = { ...ROW, id: 'curD', displayValue: 'Old med', recordState: 'dismissed' }
    // active load (first call) returns the active row; dismissed load returns the dismissed one
    getCurated.mockImplementation((id, type, signal, state) =>
      Promise.resolve({ items: state === 'dismissed' ? [DISMISSED] : [ROW] }))
    restoreCurated.mockResolvedValue({ ...DISMISSED, recordState: 'active' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-toggle-dismissed"]').trigger('click')
    await flushPromises()
    expect(getCurated).toHaveBeenCalledWith('HN1', 'medication', undefined, 'dismissed')
    expect(w.text()).toContain('Old med')
    await w.find('[data-test="curated-restore"]').trigger('click')
    await flushPromises()
    expect(restoreCurated).toHaveBeenCalledWith('curD')
  })

  it('submits an edit via updateCurated without type', async () => {
    const { getCurated, updateCurated } = await import('../src/api/client.js')
    getCurated.mockResolvedValue({ items: [ROW] })
    updateCurated.mockResolvedValue({ ...ROW, reviewStatus: 'human_confirmed' })
    const Panel = (await import('../src/components/CuratedPanel.vue')).default
    const w = mount(Panel, {
      props: { patientId: 'HN1', type: 'medication', title: 'Medications' },
      global: { stubs, plugins: [createPinia()] },
    })
    await flushPromises()
    await w.find('[data-test="curated-edit"]').trigger('click')
    w.vm.form.startDate = '2025-12-25'
    await w.vm.save()
    await flushPromises()
    expect(updateCurated).toHaveBeenCalledWith('cur1', expect.objectContaining({ startDate: '2025-12-25' }))
    // type must NOT be in the patch
    expect(updateCurated.mock.calls[0][1]).not.toHaveProperty('type')
  })
})
