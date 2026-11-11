export interface Device {
  name: string
  description: string
  default: boolean
}

export interface AppStream {
  key: string
  label: string
  streams: { media: string; state: string | null }[]
}

export interface Devices {
  inputs: Device[]
  outputs: Device[]
  apps: AppStream[]
}

export interface Sources {
  input: string | null
  output: string | null
  scope: 'all' | 'apps'
  apps: string[]
}

export interface JobInfo {
  kind: string
  step: string
  progress: number | null
}

export interface Status {
  recording: boolean
  session: string | null
  elapsed: number
  levels: Record<string, number>
  pending: number
  capture_errors: Record<string, string>
  mic_silent?: boolean
  jobs: Record<string, JobInfo>
  download?: { model: string; progress: number } | null
}

export type SessionStatus = 'recording' | 'processing' | 'naming' | 'finalised' | 'interrupted' | 'error'

export interface SessionSummary {
  id: string
  title: string
  created: string | null
  duration: number
  status: SessionStatus
  backend: string | null
  improved_with: string | null
  output_path: string | null
  has_audio: boolean
  error: string | null
  job: JobInfo | null
}

export interface Line {
  id: number
  start: number
  end: number
  speaker: string
  name: string
  text: string
}

export interface Speaker {
  id: string
  name: string | null
  suggested: string | null
  score: number | null
  duration: number
  clip: [number, number]
  remember: boolean
  saved: boolean
  lines: number
  display: string
}

export interface SessionDetail extends SessionSummary {
  sources: Sources | null
  vocab_sets: string[]
  speakers_requested: number | 'auto'
  diarizer: string | null
  user_name: string
  lines: Line[]
  speakers: Speaker[]
}

export interface Settings {
  user_name: string
  output_folder: string
  backend: 'local' | 'cloud'
  local_model: string
  improve_model: string
  cloud_model: string
  use_gpu: boolean
  language: string
  diarizer: 'auto' | 'pyannote' | 'basic'
  port: number
  notifications: boolean
  tray: boolean
  active_vocab_sets: string[]
  last_sources: Sources
  has_openai_key: boolean
  has_hf_token: boolean
  diarizer_in_use: string
  models: Record<string, boolean>
  whisper_ready: boolean
  version: string
}

export interface VocabSet {
  name: string
  terms: string[]
}

export interface Rule {
  id?: number
  wrong: string
  right: string
}

export interface Vocab {
  sets: VocabSet[]
  rules: Rule[]
}

export interface Voice {
  name: string
  samples: number
  updated: string
}

export type LiveEvent =
  | ({ type: 'status' } & Status)
  | { type: 'segment'; session: string; segment: Line & { lag: number | null } }
  | { type: 'session'; session: Partial<SessionSummary> & { id: string; deleted?: boolean } }
  | { type: 'diarization_progress'; session: string; progress: number; step: string }
  | { type: 'error'; session: string | null; message: string }
