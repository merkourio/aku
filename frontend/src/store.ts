import { defineStore } from 'pinia'
import { api } from './api'
import type { Line, LiveEvent, SessionSummary, Settings, Status } from './types'

const idle: Status = { recording: false, session: null, elapsed: 0, levels: {}, pending: 0, capture_errors: {}, jobs: {} }

export interface Toast {
  id: number
  kind: 'error' | 'info'
  text: string
}

let socket: WebSocket | null = null
let toastId = 0

export const useAku = defineStore('aku', {
  state: () => ({
    connected: false,
    status: { ...idle } as Status,
    sessions: [] as SessionSummary[],
    settings: null as Settings | null,
    // Live transcript per session: the lines of each transcribed segment. A segment's lines are
    // replaced when live speaker separation relabels it.
    live: {} as Record<string, Record<number, Line[]>>,
    changed: {} as Record<string, number>, // bumped when a session changes on the server
    toasts: [] as Toast[],
  }),
  getters: {
    liveLines: (state) => (session: string): Line[] =>
      Object.values(state.live[session] ?? {})
        .flat()
        .sort((a, b) => a.start - b.start || a.id - b.id),
  },
  actions: {
    /** Lines fetched from the server (page opened mid-call): fill in segments not seen over the socket. */
    seedLive(session: string, lines: Line[]) {
      const bySegment = (this.live[session] ??= {})
      const fetched: Record<number, Line[]> = {}
      for (const l of lines) (fetched[l.segment ?? l.id] ??= []).push(l)
      for (const [seg, group] of Object.entries(fetched)) if (!(seg in bySegment)) bySegment[Number(seg)] = group
    },
    toast(text: string, kind: Toast['kind'] = 'error') {
      const id = ++toastId
      this.toasts.push({ id, kind, text })
      setTimeout(() => this.dismiss(id), kind === 'error' ? 12000 : 5000)
    },
    dismiss(id: number) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
    /** Run an action; show its error as a toast instead of throwing. */
    async run<T>(fn: () => Promise<T>): Promise<T | undefined> {
      try {
        return await fn()
      } catch (e) {
        this.toast(e instanceof Error ? e.message : String(e))
        return undefined
      }
    },
    async loadSessions() {
      const list = await this.run(() => api.sessions())
      if (list) this.sessions = list
    },
    async loadSettings() {
      const s = await this.run(() => api.settings())
      if (s) this.settings = s
    },
    handle(event: LiveEvent) {
      if (event.type === 'status') {
        const { type: _type, ...status } = event
        this.status = status
      } else if (event.type === 'segment') {
        const bySegment = (this.live[event.session] ??= {})
        if (!(event.segment.id in bySegment)) bySegment[event.segment.id] = event.lines ?? [event.segment]
      } else if (event.type === 'relabel') {
        ;(this.live[event.session] ??= {})[event.segment] = event.lines
      } else if (event.type === 'session') {
        const s = event.session
        this.changed[s.id] = (this.changed[s.id] ?? 0) + 1
        if (s.deleted) {
          this.sessions = this.sessions.filter((x) => x.id !== s.id)
        } else {
          const i = this.sessions.findIndex((x) => x.id === s.id)
          if (i >= 0) this.sessions[i] = { ...this.sessions[i], ...s }
          else this.sessions.unshift(s as SessionSummary)
        }
      } else if (event.type === 'error') {
        this.toast(event.message)
      }
    },
    connect() {
      if (socket) return
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/live`
      const ws = new WebSocket(url)
      socket = ws
      ws.onopen = () => {
        this.connected = true
        void this.loadSessions()
      }
      ws.onmessage = (m) => this.handle(JSON.parse(m.data) as LiveEvent)
      ws.onclose = () => {
        this.connected = false
        socket = null
        setTimeout(() => this.connect(), 1500)
      }
      ws.onerror = () => ws.close()
    },
  },
})
