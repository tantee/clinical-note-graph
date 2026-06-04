import { describe, expect, it } from 'vitest'
import { formatEndpoint, formatDateRange } from '../src/utils/dateRange.js'

describe('formatEndpoint', () => {
  it('exact shows the date', () => {
    expect(formatEndpoint('2024-02-07', 'exact')).toBe('2024-02-07')
  })
  it('estimated prefixes with ~', () => {
    expect(formatEndpoint('2025-09-01', 'estimated')).toBe('~2025-09-01')
  })
  it('before with a date', () => {
    expect(formatEndpoint('2024-03-01', 'before')).toBe('before 2024-03-01')
  })
  it('ongoing ignores any date', () => {
    expect(formatEndpoint(null, 'ongoing')).toBe('ongoing')
    expect(formatEndpoint('2024-01-01', 'ongoing')).toBe('ongoing')
  })
  it('unknown with no date', () => {
    expect(formatEndpoint(null, 'unknown')).toBe('unknown')
  })
})

describe('formatDateRange', () => {
  it('estimated start to ongoing', () => {
    expect(
      formatDateRange({ startDate: '2025-09-01', startQualifier: 'estimated', stopQualifier: 'ongoing' })
    ).toBe('~2025-09-01 → ongoing')
  })
  it('exact to exact', () => {
    expect(
      formatDateRange({
        startDate: '2024-01-01', startQualifier: 'exact',
        stopDate: '2024-02-07', stopQualifier: 'exact',
      })
    ).toBe('2024-01-01 → 2024-02-07')
  })
  it('before start to exact stop', () => {
    expect(
      formatDateRange({
        startQualifier: 'before', stopDate: '2024-03-01', stopQualifier: 'exact',
      })
    ).toBe('before → 2024-03-01')
  })
  it('unknown start to ongoing collapses to a single label', () => {
    expect(
      formatDateRange({ startQualifier: 'unknown', stopQualifier: 'ongoing' })
    ).toBe('unknown → ongoing')
  })
})
