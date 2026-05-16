import { describe, expect, it } from 'vitest'
import { formatDate, formatRelative, shortenId, confidenceTier } from '../src/utils/format.js'

describe('format utilities', () => {
  it('formatDate handles ISO, Date, and falsy', () => {
    expect(formatDate('')).toBe('')
    expect(formatDate(null)).toBe('')
    expect(formatDate('2026-05-15T10:00:00Z')).toMatch(/2026/)
    expect(formatDate(new Date('2026-05-15T10:00:00Z'))).toMatch(/2026/)
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })

  it('formatRelative returns coarse buckets', () => {
    const justNow = new Date(Date.now() - 5_000).toISOString()
    expect(formatRelative(justNow)).toBe('just now')
    const minsAgo = new Date(Date.now() - 90_000).toISOString()
    expect(formatRelative(minsAgo)).toMatch(/min ago/)
  })

  it('shortenId truncates long ids', () => {
    expect(shortenId('abc')).toBe('abc')
    expect(shortenId('abcdef012345')).toContain('…')
  })

  it('confidenceTier buckets', () => {
    expect(confidenceTier(null)).toBe('unknown')
    expect(confidenceTier(0.95)).toBe('high')
    expect(confidenceTier(0.7)).toBe('medium')
    expect(confidenceTier(0.4)).toBe('low')
  })
})
