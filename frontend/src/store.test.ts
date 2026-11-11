import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { clock, speakerSlot } from './format'
import { useAku } from './store'

describe('format', () => {
  it('formats session time like the Markdown export', () => {
    expect(clock(3.9)).toBe('00:03')
    expect(clock(65)).toBe('01:05')
    expect(clock(3700)).toBe('1:01:40')
  })
  it('gives each speaker a stable colour slot', () => {
    expect(speakerSlot('me')).toBe(0)
    expect(speakerSlot('S1')).toBe(1)
    expect(speakerSlot('S6')).toBe(1)
    expect(speakerSlot('them')).toBe(1)
  })
})

describe('live events', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('status events replace the status', () => {
    const aku = useAku()
    aku.handle({ type: 'status', recording: true, session: 'a', elapsed: 3, levels: { mic: 0.2 }, pending: 1, capture_errors: {}, jobs: {} })
    expect(aku.status.recording).toBe(true)
    expect(aku.status.session).toBe('a')
    expect('type' in aku.status).toBe(false)
  })

  it('segments collect per session, once each', () => {
    const aku = useAku()
    const segment = { id: 1, start: 0, end: 1, speaker: 'me', name: 'Taylor', text: 'hi', lag: 2 }
    aku.handle({ type: 'segment', session: 'a', segment })
    aku.handle({ type: 'segment', session: 'a', segment })
    aku.handle({ type: 'segment', session: 'b', segment: { ...segment, id: 2 } })
    expect(aku.live.a).toHaveLength(1)
    expect(aku.live.b).toHaveLength(1)
  })

  it('session events update, add and remove list entries', () => {
    const aku = useAku()
    aku.handle({ type: 'session', session: { id: 'a', status: 'recording', title: '' } })
    aku.handle({ type: 'session', session: { id: 'a', status: 'naming' } })
    expect(aku.sessions).toHaveLength(1)
    expect(aku.sessions[0].status).toBe('naming')
    expect(aku.changed.a).toBe(2)
    aku.handle({ type: 'session', session: { id: 'a', deleted: true } })
    expect(aku.sessions).toHaveLength(0)
  })

  it('errors become toasts', () => {
    const aku = useAku()
    aku.handle({ type: 'error', session: null, message: 'No OpenAI API key set.' })
    expect(aku.toasts[0].text).toBe('No OpenAI API key set.')
  })
})
