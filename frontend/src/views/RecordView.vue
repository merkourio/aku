<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import SourcePicker from '../components/SourcePicker.vue'
import TranscriptLines from '../components/TranscriptLines.vue'
import { clock } from '../format'
import { useAku } from '../store'
import type { Line, Sources, VocabSet } from '../types'

const aku = useAku()
const router = useRouter()

const sources = ref<Sources>({ input: null, output: null, scope: 'all', apps: [] })
const backend = ref<'local' | 'cloud'>('local')
const language = ref('en')
const sets = ref<VocabSet[]>([])
const activeSets = ref<string[]>([])
const speakers = ref<number | 'auto'>('auto')
const busy = ref(false)
const ready = ref(false)
const scroller = ref<HTMLElement | null>(null)

onMounted(async () => {
  await aku.loadSettings()
  const s = aku.settings
  if (s) {
    sources.value = { ...s.last_sources, apps: [...s.last_sources.apps] }
    backend.value = s.backend
    language.value = s.language
    activeSets.value = [...s.active_vocab_sets]
  }
  const v = await aku.run(() => api.vocab())
  if (v) sets.value = v.sets
  ready.value = true
})

const sid = computed(() => aku.status.session)

// Reloading the page mid-recording: fetch what was already transcribed.
watch(
  sid,
  async (id) => {
    if (id) {
      const d = await aku.run(() => api.session(id))
      if (d) aku.seedLive(id, d.lines)
    }
  },
  { immediate: true },
)

const lines = computed<Line[]>(() => (sid.value ? aku.liveLines(sid.value) : []))
const live = computed(() => aku.status.live)

// The count can be changed while people join: it applies to the live view straight away.
watch(speakers, (value) => {
  const id = sid.value
  if (id && live.value?.enabled) void aku.run(() => api.liveSpeakers(id, value))
})
watch(sid, (id) => {
  if (!id) speakers.value = 'auto'
})

watch(
  () => lines.value.length,
  async () => {
    const el = scroller.value
    if (!el) return
    const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    await nextTick()
    if (nearEnd) el.scrollTop = el.scrollHeight
  },
)

const canStart = computed(() => ready.value && !busy.value && (sources.value.scope === 'all' || sources.value.apps.length > 0))
const needsKey = computed(() => backend.value === 'cloud' && aku.settings && !aku.settings.has_openai_key)

async function start() {
  busy.value = true
  await aku.run(() => api.start({ ...sources.value, backend: backend.value, vocab_sets: activeSets.value, language: language.value }))
  busy.value = false
}

async function stop() {
  const id = sid.value
  if (!id) return
  busy.value = true
  const done = await aku.run(() => api.stop(id, speakers.value))
  busy.value = false
  if (done) void router.push(`/sessions/${id}`)
}

function level(track: string): string {
  // perceptual-ish: quiet speech should still move the meter
  const v = aku.status.levels[track] ?? 0
  return `${Math.min(100, Math.round(Math.sqrt(v) * 100))}%`
}
</script>

<template>
  <div class="page">
    <template v-if="!aku.status.recording">
      <h1>Record a call</h1>
      <p class="muted">Aku records your microphone and the computer's audio as two tracks and transcribes them while you talk.</p>

      <div class="panel">
        <SourcePicker v-if="ready" v-model="sources" />
        <p v-else class="muted" style="margin: 0">Loading your last choice…</p>
      </div>

      <div class="panel stack">
        <label class="field">
          Language spoken
          <select v-model="language">
            <option v-for="l in aku.settings?.languages ?? []" :key="l.code" :value="l.code">
              {{ l.code === 'auto' ? 'Automatic (detect)' : l.name }}
            </option>
          </select>
          <span v-if="language === 'auto'" class="hint">
            Aku detects the language as people speak, so a call can mix languages. Choose one language when you know it: that is a little more reliable.
          </span>
        </label>
        <label class="field">
          Transcription
          <select v-model="backend">
            <option value="local">On this computer ({{ aku.settings?.local_model ?? 'local model' }})</option>
            <option value="cloud">OpenAI cloud ({{ aku.settings?.cloud_model ?? 'whisper-1' }})</option>
          </select>
          <span v-if="backend === 'cloud'" class="hint">Audio is sent to OpenAI in short chunks. Speakers are still identified locally.</span>
        </label>
        <p v-if="needsKey" class="small" style="color: var(--accent)">
          No OpenAI API key yet. Add one in <RouterLink to="/settings">Settings</RouterLink>.
        </p>
        <div v-if="sets.length" class="field">
          <span style="font-weight: 500; font-size: 0.9rem">Vocabulary for this recording</span>
          <div class="row">
            <label v-for="s in sets" :key="s.name" class="check">
              <input v-model="activeSets" type="checkbox" :value="s.name" /> {{ s.name }}
              <span class="muted small">({{ s.terms.length }})</span>
            </label>
          </div>
        </div>
      </div>

      <div class="row" style="justify-content: center; margin-top: 24px">
        <button class="primary big" :disabled="!canStart || !!needsKey" @click="start">● Start recording</button>
      </div>
      <p class="muted small" style="text-align: center; margin-top: 10px">Tell the people on the call that you are recording.</p>
    </template>

    <template v-else>
      <div class="row spread">
        <div>
          <h1><span class="dot rec" />Recording {{ clock(aku.status.elapsed) }}</h1>
          <p v-if="aku.status.download" class="muted small">
            Downloading the {{ aku.status.download.model }} model (first time only): {{ Math.round(aku.status.download.progress * 100) }}%.
            Recording continues; the transcript catches up afterwards.
          </p>
          <p v-else class="muted small">
            {{ aku.status.pending ? `${aku.status.pending} chunk(s) waiting to be transcribed` : 'Transcript is up to date' }}
          </p>
        </div>
        <div class="row">
          <label class="field" style="flex-direction: row; align-items: center; gap: 8px" title="Change it any time, for example when someone joins">
            Other speakers so far
            <select v-model="speakers" style="width: auto">
              <option value="auto">Detect{{ live?.enabled && live.found ? ` (${live.found})` : '' }}</option>
              <option v-for="n in 8" :key="n" :value="n">{{ n }}</option>
            </select>
          </label>
          <button class="primary" :disabled="busy" @click="stop">■ Stop</button>
        </div>
      </div>

      <div class="panel" style="margin-top: 12px">
        <div class="row" style="gap: 18px">
          <div class="grow">
            <div class="small muted">You</div>
            <div class="bar"><i :style="{ width: level('mic') }" /></div>
          </div>
          <div class="grow">
            <div class="small muted">Them</div>
            <div class="bar"><i :style="{ width: level('system') }" /></div>
          </div>
        </div>
        <p v-if="aku.status.mic_silent" class="small" style="color: var(--warn); margin: 8px 0 0">
          Your microphone is sending pure silence. Is it muted (mute button on the mic), or is the wrong one selected?
        </p>
        <p v-for="(msg, track) in aku.status.capture_errors" :key="track" class="small" style="color: var(--accent); margin: 8px 0 0">
          {{ track === 'mic' ? 'Microphone' : 'Computer audio' }}: {{ msg }}
        </p>
      </div>

      <div ref="scroller" class="panel" style="max-height: calc(100vh - 290px); overflow-y: auto">
        <TranscriptLines v-if="lines.length" :lines="lines" />
        <div v-else class="empty">Listening… the first lines appear a few seconds after someone speaks.</div>
      </div>
    </template>
  </div>
</template>
