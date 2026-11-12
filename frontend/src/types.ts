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
  scope: 'all' | 'apps' | 'none' // none: interview mode without computer audio
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
  interview?: boolean
  system_off?: boolean
  enrolling?: boolean
  live?: {
    enabled: boolean
    speakers: number | 'auto'
    found: number
    mic?: { speakers: number | 'auto'; found: number } | null
  } | null
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
  language: string
  translate?: boolean
  interview?: boolean
  output_path: string | null
  has_audio: boolean
  error: string | null
  job: JobInfo | null
}

export interface Line {
  id: number
  segment?: number
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
  track: 'system' | 'mic'
  me: boolean
}

export interface SessionDetail extends SessionSummary {
  sources: Sources | null
  vocab_sets: string[]
  speakers_requested: number | 'auto'
  mic_speakers_requested: number | 'auto'
  system_off: boolean
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
  cloud_diarize_model: string
  use_gpu: boolean
  language: string
  translate: boolean
  diarizer: 'auto' | 'pyannote' | 'basic'
  live_diarization: boolean
  global_shortcut: boolean
  shortcut: { state: 'off' | 'binding' | 'bound' | 'unavailable' | 'error'; trigger: string | null; error: string | null }
  port: number
  notifications: boolean
  tray: boolean
  active_vocab_sets: string[]
  last_sources: Sources
  has_openai_key: boolean
  has_hf_token: boolean
  diarizer_in_use: string
  models: Record<string, boolean>
  languages: { code: string; name: string }[]
  whisper_ready: boolean
  my_voice: MyVoice[]
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

/** The owner's own voice, per speaker-separation method it is enrolled for. */
export interface MyVoice {
  space: string
  samples: number
  updated: string
}

export interface Voice {
  name: string
  samples: number
  updated: string
}

export type LiveEvent =
  | ({ type: 'status' } & Status)
  | { type: 'segment'; session: string; segment: Line & { lag: number | null }; lines?: Line[] }
  | { type: 'relabel'; session: string; segment: number; lines: Line[] }
  | { type: 'session'; session: Partial<SessionSummary> & { id: string; deleted?: boolean } }
  | { type: 'diarization_progress'; session: string; progress: number; step: string }
  | { type: 'error'; session: string | null; message: string }
