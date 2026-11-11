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
    live: {} as Record<string, Line[]>, // lines that arrived over the socket, per session
    changed: {} as Record<string, number>, // bumped when a session changes on the server
    toasts: [] as Toast[],
  }),
  actions: {
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
        const lines = (this.live[event.session] ??= [])
        if (!lines.some((l) => l.id === event.segment.id)) lines.push(event.segment)
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
