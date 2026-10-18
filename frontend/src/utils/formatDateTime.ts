/** A date and time together, the one format used everywhere a timestamp is
 * shown with both: "18 Oct 2026, 02:30 pm". */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
