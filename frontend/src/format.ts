export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm}:${ss}` : `${mm}:${ss}`
}

export function when(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export const statusLabel: Record<string, string> = {
  recording: 'Recording',
  processing: 'Processing',
  naming: 'Name speakers',
  finalised: 'Saved',
  interrupted: 'Interrupted',
  error: 'Needs attention',
}

/** A stable colour slot (0-5) per speaker id, so each voice keeps its colour. */
export function speakerSlot(speaker: string): number {
  if (speaker === 'me') return 0
  const n = Number(speaker.replace(/\D/g, ''))
  return Number.isFinite(n) && n > 0 ? ((n - 1) % 5) + 1 : 1
}
