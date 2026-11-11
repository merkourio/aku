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
    expect(aku.liveLines('a')).toHaveLength(1)
    expect(aku.liveLines('b')).toHaveLength(1)
  })

  it('relabel replaces the lines of one segment, splitting it by speaker', () => {
    const aku = useAku()
    const base = { start: 0, end: 4, speaker: 'them', name: 'Them', text: 'hello there yes hi' }
    aku.handle({ type: 'segment', session: 'a', segment: { ...base, id: 1, lag: 2 }, lines: [{ ...base, id: 1, segment: 1 }] })
    aku.handle({ type: 'segment', session: 'a', segment: { ...base, id: 2, start: 5, end: 6, lag: 2 } })
    aku.handle({
      type: 'relabel', session: 'a', segment: 1,
      lines: [
        { id: 1, segment: 1, start: 0, end: 2, speaker: 'S1', name: 'Speaker 1', text: 'hello there' },
        { id: 1.01, segment: 1, start: 2, end: 4, speaker: 'S2', name: 'Speaker 2', text: 'yes hi' },
      ],
    })
    expect(aku.liveLines('a').map((l) => l.name)).toEqual(['Speaker 1', 'Speaker 2', 'Them'])
  })

  it('lines fetched on page load do not overwrite what the socket delivered', () => {
    const aku = useAku()
    const line = { id: 1, segment: 1, start: 0, end: 1, speaker: 'S1', name: 'Speaker 1', text: 'fresh' }
    aku.handle({ type: 'relabel', session: 'a', segment: 1, lines: [line] })
    aku.seedLive('a', [{ ...line, text: 'stale' }, { ...line, id: 2, segment: 2, start: 3, text: 'older' }])
    expect(aku.liveLines('a').map((l) => l.text)).toEqual(['fresh', 'older'])
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
