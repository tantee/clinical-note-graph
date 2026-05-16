export function formatDate(value, { fallback = '' } = {}) {
  if (value === null || value === undefined || value === '') return fallback
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString()
}

export function formatRelative(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  const diff = (Date.now() - d.getTime()) / 1000
  const abs = Math.abs(diff)
  if (abs < 60) return 'just now'
  if (abs < 3600) return `${Math.round(diff / 60)} min ago`
  if (abs < 86400) return `${Math.round(diff / 3600)} h ago`
  return `${Math.round(diff / 86400)} d ago`
}

export function shortenId(id, head = 6, tail = 4) {
  if (!id) return ''
  if (id.length <= head + tail + 1) return id
  return `${id.slice(0, head)}…${id.slice(-tail)}`
}

export function confidenceTier(c) {
  if (c == null) return 'unknown'
  if (c >= 0.8) return 'high'
  if (c >= 0.6) return 'medium'
  return 'low'
}
