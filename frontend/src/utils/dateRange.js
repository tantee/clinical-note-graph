// Renders a temporal endpoint or full range from a curated row's
// {start,stop}{Date,Qualifier}. Pure + framework-free so it is unit-testable.

export function formatEndpoint(date, qualifier) {
  switch (qualifier) {
    case 'ongoing':
      return 'ongoing'
    case 'unknown':
      return date ? String(date) : 'unknown'
    case 'before':
      return date ? `before ${date}` : 'before'
    case 'estimated':
      return date ? `~${date}` : 'estimated'
    case 'exact':
    default:
      return date ? String(date) : (qualifier || '')
  }
}

export function formatDateRange(row = {}) {
  const start = formatEndpoint(row.startDate, row.startQualifier || 'unknown')
  const stop = formatEndpoint(row.stopDate, row.stopQualifier || 'unknown')
  return `${start} → ${stop}`
}
