import type { Devices, SessionDetail, SessionSummary, Settings, Sources, Status, Vocab, Voice } from './types'

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let detail = r.statusText || `Request failed (${r.status})`
    try {
      const data = await r.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

const enc = encodeURIComponent

export const api = {
  status: () => req<Status>('GET', '/status'),
  devices: () => req<Devices>('GET', '/devices'),
  sessions: () => req<SessionSummary[]>('GET', '/sessions'),
  session: (id: string) => req<SessionDetail>('GET', `/sessions/${enc(id)}`),
  start: (body: Partial<Sources> & { backend?: string; vocab_sets?: string[]; language?: string }) =>
    req<SessionSummary>('POST', '/sessions', body),
  stop: (id: string, speakers: number | 'auto') =>
    req<SessionSummary>('POST', `/sessions/${enc(id)}/stop`, { speakers }),
  diarize: (id: string, speakers: number | 'auto') =>
    req<SessionSummary>('POST', `/sessions/${enc(id)}/diarize`, { speakers }),
  setSpeaker: (id: string, spk: string, body: { name?: string; remember_voice?: boolean }) =>
    req<SessionDetail>('PUT', `/sessions/${enc(id)}/speakers/${enc(spk)}`, body),
  finalize: (id: string, title: string, output_folder: string) =>
    req<SessionDetail>('POST', `/sessions/${enc(id)}/finalize`, { title, output_folder: output_folder || null }),
  improve: (id: string, engine: 'local_large' | 'cloud') =>
    req<SessionSummary>('POST', `/sessions/${enc(id)}/improve`, { engine }),
  recover: (id: string) => req<SessionSummary>('POST', `/sessions/${enc(id)}/recover`),
  deleteAudio: (id: string) => req<SessionSummary>('DELETE', `/sessions/${enc(id)}/audio`),
  deleteSession: (id: string) => req<{ deleted: string }>('DELETE', `/sessions/${enc(id)}`),
  settings: () => req<Settings>('GET', '/settings'),
  saveSettings: (body: Record<string, unknown>) => req<Settings>('PUT', '/settings', body),
  vocab: () => req<Vocab>('GET', '/vocab'),
  saveVocab: (body: Vocab) => req<Vocab>('PUT', '/vocab', body),
  addRule: (wrong: string, right: string, session?: string, set?: string) =>
    req<Vocab>('POST', '/vocab/replace-rule', { wrong, right, session, set }),
  voices: () => req<Voice[]>('GET', '/voiceprints'),
  forgetVoice: (name: string) => req<{ forgotten: number }>('DELETE', `/voiceprints/${enc(name)}`),
  clipUrl: (id: string, spk: string) => `/sessions/${enc(id)}/speakers/${enc(spk)}/clip?t=${Date.now()}`,
  audioUrl: (id: string, start: number, end: number) =>
    `/sessions/${enc(id)}/audio?start=${start.toFixed(2)}&end=${end.toFixed(2)}&track=mix`,
}
