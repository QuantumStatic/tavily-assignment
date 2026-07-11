/** Shared project/vendor name matcher: trim + lowercase + substring. Used by the
 *  sidebar search and the dashboard project filter so matching can't drift. */
export function filterByName<T extends { name: string }>(items: T[], query: string): T[] {
  const q = query.trim().toLowerCase()
  if (q === '') return items
  return items.filter((i) => i.name.toLowerCase().includes(q))
}
