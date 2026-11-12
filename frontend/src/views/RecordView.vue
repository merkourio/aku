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
const translate = ref(true) // with a language chosen: write other languages in it too
const sets = ref<VocabSet[]>([])
const activeSets = ref<string[]>([])
const speakers = ref<number | 'auto'>('auto')
// Interview mode is chosen per recording and never remembered: a normal call is the default.
const interview = ref(false)
const computerAudio = ref(false) // in an interview: also record what the computer plays
const micSpeakers = ref<number | 'auto'>('auto')
const busy = ref(false)
const ready = ref(false)
const scroller = ref<HTMLElement | null>(null)

onMounted(async () => {
  await aku.loadSettings()
  const s = aku.settings
  if (s) {
    sources.value = { ...s.last_sources, apps: [...s.last_sources.apps] }
    if (sources.value.scope === 'none') sources.value.scope = 'all'
    backend.value = s.backend
    language.value = s.language
    translate.value = s.translate
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
watch(micSpeakers, (value) => {
  const id = sid.value
  if (id && live.value?.enabled && aku.status.interview) void aku.run(() => api.liveSpeakers(id, value, 'mic'))
})
watch(sid, (id) => {
  if (!id) speakers.value = micSpeakers.value = 'auto'
})
const micOnly = computed(() => interview.value && !computerAudio.value)
const myVoice = computed(() => (aku.settings?.my_voice.length ?? 0) > 0)

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

const languageName = computed(() => aku.settings?.languages.find((l) => l.code === language.value)?.name ?? language.value)
const canStart = computed(
  () => ready.value && !busy.value && (micOnly.value || sources.value.scope === 'all' || sources.value.apps.length > 0),
)
const needsKey = computed(() => backend.value === 'cloud' && aku.settings && !aku.settings.has_openai_key)

async function start() {
  busy.value = true
  const chosen: Sources = micOnly.value ? { ...sources.value, scope: 'none' } : sources.value
  await aku.run(() =>
    api.start({ ...chosen, backend: backend.value, vocab_sets: activeSets.value, language: language.value, translate: translate.value, interview: interview.value }),
  )
  busy.value = false
}

async function stop() {
  const id = sid.value
  if (!id) return
  busy.value = true
  const done = await aku.run(() => api.stop(id, speakers.value, micSpeakers.value))
  if (done) interview.value = computerAudio.value = false // the next recording is a normal call again
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

      <div class="panel stack">
        <label class="check" style="font-weight: 500">
          <input v-model="interview" type="checkbox" /> Interview mode: several people talk into this microphone
        </label>
        <template v-if="interview">
          <p class="muted small" style="margin: 0">
            Aku tells the voices at the microphone apart and you name them afterwards. Expect more mistakes than on a call,
            most of all when people talk at the same time. An external microphone placed between the speakers helps a lot.
          </p>
          <p v-if="!myVoice" class="small" style="color: var(--warn); margin: 0">
            Aku does not know your voice yet, so you will pick yourself among the speakers afterwards.
            <RouterLink to="/settings">Record your voice once in Settings</RouterLink> and it labels you by itself.
          </p>
          <label class="check">
            <input v-model="computerAudio" type="checkbox" /> Also record the computer's audio (someone joins by call)
          </label>
          <p v-if="computerAudio" class="small" style="color: var(--warn); margin: 0">
            Only if everyone in the room hears the call through headphones. Through loudspeakers, the microphone picks the
            caller up as well and their words appear twice.
          </p>
        </template>
      </div>

      <div class="panel">
        <SourcePicker v-if="ready" v-model="sources" :mic-only="micOnly" :room="interview" />
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
        <template v-if="language !== 'auto'">
          <label class="check">
            <input type="checkbox" :checked="!translate" @change="translate = !($event.target as HTMLInputElement).checked" />
            Keep other languages as they are spoken
          </label>
          <p class="muted small" style="margin: -6px 0 0">
            <template v-if="translate">
              Off: Aku writes everything as {{ languageName }}. What is said in another language is often translated,
              but not reliably: some passages stay in their own language.
            </template>
            <template v-else>
              On: what is said in another language is written in that language, not translated. {{ languageName }} is
              only assumed for very short remarks before Aku has heard which language is spoken.
            </template>
          </p>
        </template>
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
      <p class="muted small" style="text-align: center; margin-top: 10px">
        Tell the people {{ interview ? 'in the room' : 'on the call' }} that you are recording.
      </p>
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
          <label
            v-if="aku.status.interview" class="field" style="flex-direction: row; align-items: center; gap: 8px"
            title="Everyone talking into the microphone, you included. Change it any time."
          >
            People at the microphone
            <select v-model="micSpeakers" style="width: auto">
              <option value="auto">Detect{{ live?.enabled && live.mic?.found ? ` (${live.mic.found})` : '' }}</option>
              <option v-for="n in 8" :key="n" :value="n">{{ n }}</option>
            </select>
          </label>
          <label
            v-if="!aku.status.system_off" class="field" style="flex-direction: row; align-items: center; gap: 8px"
            title="Change it any time, for example when someone joins"
          >
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
            <div class="small muted">{{ aku.status.interview ? 'Microphone (room)' : 'You' }}</div>
            <div class="bar"><i :style="{ width: level('mic') }" /></div>
          </div>
          <div v-if="!aku.status.system_off" class="grow">
            <div class="small muted">Them</div>
            <div class="bar"><i :style="{ width: level('system') }" /></div>
          </div>
        </div>
        <p v-if="aku.status.mic_silent" class="small" style="color: var(--warn); margin: 8px 0 0">
          The microphone is sending pure silence. Is it muted (mute button on the mic), or is the wrong one selected?
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
