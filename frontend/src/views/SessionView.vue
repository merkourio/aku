<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import TranscriptLines from '../components/TranscriptLines.vue'
import { clock, speakerSlot, statusLabel, when } from '../format'
import { useAku } from '../store'
import type { Line, SessionDetail } from '../types'

const props = defineProps<{ id: string }>()
const vFocus = { mounted: (el: HTMLElement) => el.focus() } // focus the fix input when the popover opens
const aku = useAku()
const router = useRouter()

const detail = ref<SessionDetail | null>(null)
const missing = ref(false)
const names = reactive<Record<string, string>>({})
const title = ref('')
const folder = ref('')
const count = ref<number | 'auto'>('auto')
const engine = ref<'local_large' | 'cloud' | 'cloud_diarize'>('local_large')
const confirm = ref<null | 'audio' | 'session'>(null)
const working = ref(false)
const player = new Audio()
const playing = ref<string | null>(null)
player.onended = player.onpause = () => (playing.value = null)
onBeforeUnmount(() => player.pause())

async function load() {
  try {
    const d = await api.session(props.id)
    detail.value = d
    missing.value = false
    if (d.status === 'recording' || d.status === 'processing') aku.seedLive(props.id, d.lines)
    for (const s of d.speakers) {
      // keep what the user is typing; otherwise show the saved or suggested name
      if (!(s.id in names) || names[s.id] === '') names[s.id] = s.name ?? s.suggested ?? ''
    }
    if (!title.value) title.value = d.title
    if (!folder.value) folder.value = d.output_path ? d.output_path.replace(/\/[^/]*$/, '') : (aku.settings?.output_folder ?? '')
    count.value = d.speakers_requested
  } catch {
    missing.value = true
  }
}

watch(() => props.id, () => { detail.value = null; title.value = ''; folder.value = ''; void load() }, { immediate: true })
watch(() => aku.changed[props.id], () => void load())

const job = computed(() => aku.status.jobs[props.id] ?? null)
const languageName = computed(() => {
  const code = detail.value?.language ?? 'en'
  return aku.settings?.languages.find((l) => l.code === code)?.name ?? code
})
const status = computed(() => detail.value?.status)
const ready = computed(() => status.value === 'naming' || status.value === 'finalised')
const isRecording = computed(() => aku.status.recording && aku.status.session === props.id)

const lines = computed<Line[]>(() => {
  const d = detail.value
  if (!d) return []
  if (ready.value) return d.lines
  // still recording or finishing: what the live view has, else what the server saved
  const live = aku.liveLines(props.id)
  return live.length ? live : d.lines
})

function play(key: string, url: string) {
  if (playing.value === key) {
    player.pause()
    return
  }
  player.src = url
  void player.play()
  playing.value = key
}

async function saveName(spk: string) {
  const d = detail.value
  const s = d?.speakers.find((x) => x.id === spk)
  if (!s || (s.name ?? '') === names[spk].trim()) return
  const updated = await aku.run(() => api.setSpeaker(props.id, spk, { name: names[spk] }))
  if (updated) detail.value = updated
}

async function setRemember(spk: string, on: boolean) {
  const updated = await aku.run(() => api.setSpeaker(props.id, spk, { name: names[spk], remember_voice: on }))
  if (updated) detail.value = updated
}

async function rerun() {
  await aku.run(() => api.diarize(props.id, count.value))
  for (const k of Object.keys(names)) delete names[k]
}

async function finalise() {
  working.value = true
  // names typed but not yet blurred, and suggestions the user left in place, are confirmed here
  for (const s of detail.value?.speakers ?? []) {
    if (names[s.id]?.trim() && names[s.id].trim() !== (s.name ?? '')) await saveName(s.id)
  }
  const done = await aku.run(() => api.finalize(props.id, title.value, folder.value))
  working.value = false
  if (done) {
    detail.value = done
    aku.toast(`Saved to ${done.output_path}`, 'info')
  }
}

async function improve() {
  await aku.run(() => api.improve(props.id, engine.value))
}

async function recover() {
  await aku.run(() => api.recover(props.id))
}

async function remove(kind: 'audio' | 'session') {
  confirm.value = null
  if (kind === 'audio') {
    await aku.run(() => api.deleteAudio(props.id))
    void load()
  } else if (await aku.run(() => api.deleteSession(props.id))) {
    void router.push('/sessions')
  }
}

// --- fix from transcript: select a misheard word, type the right one ---------------
const fix = reactive({ open: false, wrong: '', right: '', x: 0, y: 0 })

function onSelect() {
  const sel = window.getSelection()
  const text = sel?.toString().trim() ?? ''
  if (!sel || !text || text.length > 60 || text.includes('\n') || !ready.value) return
  const node = sel.anchorNode?.parentElement
  if (!node?.closest('.text')) return
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  const host = document.querySelector('.main') as HTMLElement
  const hostRect = host.getBoundingClientRect()
  fix.wrong = text
  fix.right = ''
  fix.x = Math.max(12, Math.min(rect.left - hostRect.left, hostRect.width - 340))
  fix.y = rect.bottom - hostRect.top + host.scrollTop + 8
  fix.open = true
}

async function saveFix() {
  if (!fix.right.trim()) return
  const ok = await aku.run(() => api.addRule(fix.wrong, fix.right, props.id))
  fix.open = false
  window.getSelection()?.removeAllRanges()
  if (ok) {
    aku.toast(`“${fix.wrong}” will be written as “${fix.right.trim()}” from now on.`, 'info')
    void load()
  }
}
</script>

<template>
  <div class="page" style="position: relative">
    <p class="small"><RouterLink to="/sessions" class="muted">← Transcripts</RouterLink></p>

    <div v-if="missing" class="panel empty">This session does not exist (any more).</div>

    <template v-else-if="detail">
      <div class="row spread">
        <div class="grow">
          <h1>{{ detail.title || 'Untitled call' }}</h1>
          <p class="muted small">
            {{ when(detail.created) }} · {{ clock(detail.duration) }} · {{ languageName }} · {{ detail.improved_with ?? detail.backend }}
          </p>
        </div>
        <span class="badge" :class="detail.status">{{ statusLabel[detail.status] ?? detail.status }}</span>
      </div>

      <div v-if="isRecording" class="panel">
        Still recording. <RouterLink to="/">Go to the recording</RouterLink> to stop it.
      </div>

      <div v-if="job" class="panel">
        <div class="row spread small">
          <span>{{ job.step }}</span>
          <span v-if="job.progress !== null" class="muted">{{ Math.round(job.progress * 100) }}%</span>
        </div>
        <div class="bar" :class="{ busy: job.progress === null }" style="margin-top: 8px">
          <i :style="{ width: `${Math.round((job.progress ?? 0) * 100)}%` }" />
        </div>
      </div>

      <div v-if="(status === 'interrupted' || status === 'error') && !job" class="panel">
        <p v-if="status === 'interrupted'">This recording was cut short (Aku was closed or crashed). The audio is safe.</p>
        <p v-else>Something went wrong while processing: {{ detail.error }}</p>
        <button v-if="detail.has_audio" class="primary" @click="recover">Recover: finish the transcript and separate speakers</button>
      </div>
      <p v-else-if="detail.error && ready" class="small" style="color: var(--accent)">{{ detail.error }}</p>

      <template v-if="ready">
        <!-- speakers -->
        <div class="panel">
          <div class="row spread">
            <h2 style="margin: 0">Who was on the call?</h2>
            <div class="row small">
              <span class="muted">Speakers on the other side:</span>
              <select v-model="count" style="width: auto">
                <option value="auto">Detect</option>
                <option v-for="n in 8" :key="n" :value="n">{{ n }}</option>
              </select>
              <button :disabled="!!job || !detail.has_audio" @click="rerun">Re-run</button>
            </div>
          </div>
          <p v-if="!detail.speakers.length" class="muted" style="margin-top: 10px">Nobody was heard on the other side.</p>
          <div v-for="s in detail.speakers" :key="s.id" class="row" style="margin-top: 12px; align-items: flex-start">
            <button :disabled="!detail.has_audio" :title="detail.has_audio ? 'Play a short clip of this voice' : 'Audio was deleted'" @click="play(s.id, api.clipUrl(id, s.id))">
              {{ playing === s.id ? '■' : '▶' }}
            </button>
            <div class="grow">
              <input
                v-model="names[s.id]" type="text" :placeholder="s.display" :aria-label="`Name for ${s.display}`"
                :style="{ borderLeft: `4px solid var(--s${speakerSlot(s.id)})` }"
                @blur="saveName(s.id)" @keyup.enter="($event.target as HTMLInputElement).blur()"
              />
              <div class="small muted" style="margin-top: 3px">
                {{ s.display }} · spoke {{ clock(s.duration) }}
                <template v-if="s.suggested && !s.name"> · sounds like <b>{{ s.suggested }}</b> (confirm by saving)</template>
              </div>
            </div>
            <label class="check small" style="padding-top: 8px" :title="'Keeps a voiceprint on this computer so Aku can suggest the name next time'">
              <input type="checkbox" :checked="s.remember" :disabled="!names[s.id]?.trim()" @change="setRemember(s.id, ($event.target as HTMLInputElement).checked)" />
              Remember this voice
            </label>
          </div>
          <p class="small muted" style="margin: 12px 0 0">
            Voices are only remembered when you tick the box, and stay on this computer. Ask people before saving theirs.
          </p>
        </div>

        <!-- finalise -->
        <div class="panel stack">
          <h2 style="margin: 0">{{ status === 'finalised' ? 'Saved transcript' : 'Save the transcript' }}</h2>
          <label class="field">Title <input v-model="title" type="text" placeholder="e.g. Weekly sync" /></label>
          <label class="field">Folder <input v-model="folder" type="text" spellcheck="false" /></label>
          <div class="row">
            <button class="primary" :disabled="working || !!job" @click="finalise">
              {{ status === 'finalised' ? 'Save again' : 'Finalise and save Markdown' }}
            </button>
            <span v-if="detail.output_path" class="small muted">Saved as {{ detail.output_path }}</span>
          </div>
        </div>
      </template>

      <!-- transcript -->
      <div class="panel" @mouseup="onSelect">
        <div class="row spread" style="margin-bottom: 10px">
          <h2 style="margin: 0">Transcript</h2>
          <span v-if="ready" class="small muted">Select a misheard word to fix it everywhere</span>
        </div>
        <TranscriptLines
          v-if="lines.length" :lines="lines" :playable="detail.has_audio && ready"
          @play="(l) => play(`line${l.id}`, api.audioUrl(id, l.start, l.end + 0.3))"
        />
        <div v-else class="empty">{{ ready ? 'Nothing was said in this recording.' : 'No lines yet.' }}</div>
      </div>

      <!-- quality pass + deletion -->
      <div v-if="ready" class="panel stack">
        <h2 style="margin: 0">Improve or clean up</h2>
        <div class="row">
          <select v-model="engine" style="width: auto" :disabled="!detail.has_audio">
            <option value="local_large">Larger local model ({{ aku.settings?.improve_model ?? 'large-v3' }})</option>
            <option value="cloud">OpenAI cloud</option>
            <option value="cloud_diarize">OpenAI cloud with its own speaker labels</option>
          </select>
          <button :disabled="!detail.has_audio || !!job || aku.status.recording" @click="improve">Improve transcript</button>
          <span class="small muted">Re-transcribes the whole recording and keeps the speaker names. The current version is kept as a backup.</span>
        </div>
        <p v-if="engine === 'cloud_diarize'" class="small muted" style="margin: 0">
          Uploads the whole recording of the other side to OpenAI ({{ aku.settings?.cloud_diarize_model }}), which also decides who said what.
          Names you gave are carried over. OpenAI retires this model on 26 Feb 2027.
        </p>
        <p v-else-if="engine === 'cloud'" class="small muted" style="margin: 0">Sends the audio to OpenAI in short chunks; speakers stay as they are.</p>
        <div class="row">
          <button class="danger" :disabled="!detail.has_audio || !!job" @click="confirm = 'audio'">Delete audio, keep transcript</button>
          <button class="danger" :disabled="!!job || isRecording" @click="confirm = 'session'">Delete this session</button>
          <span v-if="!detail.has_audio" class="small muted">The audio was deleted.</span>
        </div>
      </div>
    </template>

    <!-- fix popover -->
    <div v-if="fix.open" class="popover" :style="{ left: fix.x + 'px', top: fix.y + 'px' }">
      <div class="small" style="margin-bottom: 6px">Replace <b>“{{ fix.wrong }}”</b> with:</div>
      <input v-model="fix.right" v-focus type="text" placeholder="correct spelling" @keyup.enter="saveFix" @keyup.esc="fix.open = false" />
      <div class="row" style="margin-top: 8px; justify-content: flex-end">
        <button class="ghost" @click="fix.open = false">Cancel</button>
        <button class="primary" :disabled="!fix.right.trim()" @click="saveFix">Fix here and in future</button>
      </div>
    </div>

    <!-- confirm dialogs -->
    <div v-if="confirm" class="modal-back" @click.self="confirm = null">
      <div class="modal" role="dialog" aria-modal="true">
        <template v-if="confirm === 'audio'">
          <h2>Delete the audio?</h2>
          <p>The transcript stays. Without the audio you can no longer improve this transcript, re-run speaker separation or play clips. This cannot be undone.</p>
        </template>
        <template v-else>
          <h2>Delete this session?</h2>
          <p>Audio and transcript data are removed from Aku. A Markdown file you already saved stays where it is. This cannot be undone.</p>
        </template>
        <div class="row" style="justify-content: flex-end">
          <button @click="confirm = null">Cancel</button>
          <button class="primary" @click="remove(confirm)">{{ confirm === 'audio' ? 'Delete audio' : 'Delete session' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
