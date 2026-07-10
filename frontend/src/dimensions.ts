// Column order + labels for the comparison table. One entry per scored backend Dimension
// (excludes `snapshot`, which is entity resolution, not a report section).
// `help` is shown in a tooltip on the column's "?" badge. Scores are always 0-10 where
// 10 = strong / low risk and 0 = serious concerns.
export const DIMENSIONS: { key: string; label: string; help: string }[] = [
  { key: 'legal', label: 'Legal',
    help: 'Litigation, lawsuits, regulatory fines and investigations. 10 = clean record; 0 = serious legal problems.' },
  { key: 'financial', label: 'Financial',
    help: 'Revenue, profitability, debt and funding health. 10 = financially sound; 0 = distress signals.' },
  { key: 'safety', label: 'Safety',
    help: 'Safety incidents, product recalls, workplace accidents and violations. 10 = strong safety record; 0 = serious safety issues.' },
  { key: 'certifications', label: 'Certs',
    help: 'Quality and management certifications (ISO, accreditations). 10 = well-certified; 0 = none found or expired.' },
  { key: 'backlog', label: 'Backlog/Ops',
    help: 'Order backlog, project pipeline and execution (from earnings calls when the vendor is public). 10 = healthy backlog; 0 = weak or declining.' },
  { key: 'news', label: 'News',
    help: 'Recent independent coverage — wins and pain signals. 10 = positive momentum; 0 = adverse coverage.' },
]

export const VERDICT_HELP =
  'Overall due-diligence score — the average of the dimension scores. 10 = strong, low risk; 0 = serious concerns.'
