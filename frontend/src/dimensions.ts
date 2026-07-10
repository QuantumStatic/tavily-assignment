// Column order + labels for the comparison table. One entry per scored backend Dimension
// (excludes `snapshot`, which is entity resolution, not a report section).
export const DIMENSIONS: { key: string; label: string }[] = [
  { key: 'legal', label: 'Legal' },
  { key: 'financial', label: 'Financial' },
  { key: 'safety', label: 'Safety' },
  { key: 'certifications', label: 'Certs' },
  { key: 'backlog', label: 'Backlog/Ops' },
  { key: 'news_positive', label: 'News +' },
  { key: 'news_negative', label: 'News −' },
]
