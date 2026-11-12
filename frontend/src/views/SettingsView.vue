<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'
import { useAku } from '../store'
import type { Rule, Settings, Voice } from '../types'

const aku = useAku()
const form = ref<Settings | null>(null)
const openaiKey = ref('')
const hfToken = ref('')
const voices = ref<Voice[]>([])
const sets = ref<{ name: string; text: string }[]>([])
const rules = ref<Rule[]>([])
const newSet = ref('')
const newRule = reactive({ wrong: '', right: '' })
const quick = reactive<Record<string, string>>({})
const saved = ref('')

async function load() {
  await aku.loadSettings()
  form.value = aku.settings ? JSON.parse(JSON.stringify(aku.settings)) : null
  const v = await aku.run(() => api.vocab())
  if (v) {
    sets.value = v.sets.map((s) => ({ name: s.name, text: s.terms.join('\n') }))
    rules.value = v.rules
  }
  voices.value = (await aku.run(() => api.voices())) ?? []
}
onMounted(load)

// While the desktop's shortcut dialog is open, keep checking whether it has been answered.
const poll = window.setInterval(async () => {
  if (form.value?.shortcut.state !== 'binding') return
  const s = await api.settings().catch(() => null)
  if (s && form.value) form.value.shortcut = s.shortcut
}, 1500)
onUnmounted(() => window.clearInterval(poll))

async function changeShortcut() {
  await aku.run(() => api.configureShortcut())
}

function flash(text: string) {
  saved.value = text
  setTimeout(() => (saved.value = ''), 2500)
}

async function save(extra: Record<string, unknown> = {}) {
  const f = form.value
  if (!f) return
  const body = {
    user_name: f.user_name, output_folder: f.output_folder, backend: f.backend, local_model: f.local_model,
    improve_model: f.improve_model, cloud_model: f.cloud_model, use_gpu: f.use_gpu, diarizer: f.diarizer, language: f.language, translate: f.translate,
    notifications: f.notifications, tray: f.tray, live_diarization: f.live_diarization, global_shortcut: f.global_shortcut, active_vocab_sets: f.active_vocab_sets, ...extra,
  }
  const s = await aku.run(() => api.saveSettings(body))
  if (s) {
    aku.settings = s
    form.value = JSON.parse(JSON.stringify(s))
    flash('Saved')
  }
}

async function saveKey(name: 'openai_api_key' | 'hf_token', value: string) {
  await save({ [name]: value })
  openaiKey.value = hfToken.value = ''
}

async function saveVocab() {
  const body = {
    sets: sets.value.map((s) => ({ name: s.name, terms: s.text.split('\n').map((t) => t.trim()).filter(Boolean) })),
    rules: rules.value,
  }
  const v = await aku.run(() => api.saveVocab(body))
  if (v) {
    sets.value = v.sets.map((s) => ({ name: s.name, text: s.terms.join('\n') }))
    rules.value = v.rules
    await aku.loadSettings()
    if (form.value && aku.settings) form.value.active_vocab_sets = [...aku.settings.active_vocab_sets]
    flash('Vocabulary saved')
  }
}

function addSet() {
  const name = newSet.value.trim()
  if (!name || sets.value.some((s) => s.name.toLowerCase() === name.toLowerCase())) return
  sets.value.push({ name, text: '' })
  newSet.value = ''
  void saveVocab()
}

function removeSet(name: string) {
  sets.value = sets.value.filter((s) => s.name !== name)
  void saveVocab()
}

function quickAdd(name: string) {
  const term = (quick[name] ?? '').trim()
  const set = sets.value.find((s) => s.name === name)
  if (!term || !set) return
  set.text = set.text ? `${set.text}\n${term}` : term
  quick[name] = ''
  void saveVocab()
}

function addRule() {
  if (!newRule.wrong.trim() || !newRule.right.trim()) return
  rules.value.push({ wrong: newRule.wrong.trim(), right: newRule.right.trim() })
  newRule.wrong = newRule.right = ''
  void saveVocab()
}

function removeRule(rule: Rule) {
  rules.value = rules.value.filter((r) => r !== rule)
  void saveVocab()
}

async function forget(name: string) {
  if (await aku.run(() => api.forgetVoice(name))) voices.value = voices.value.filter((v) => v.name !== name)
}

// --- my own voice (interview mode) -------------------------------------------------
const ENROL_SECONDS = 30
const enrolLeft = ref<number | null>(null) // seconds still to read; 0 = working on it
let enrolTimer: number | undefined
onUnmounted(() => window.clearInterval(enrolTimer))
const myVoice = computed(() => form.value?.my_voice ?? [])
const methodName = (space: string) => (space === 'community-1' ? 'pyannote' : 'built-in')

async function enrol() {
  enrolLeft.value = ENROL_SECONDS
  enrolTimer = window.setInterval(() => {
    if (enrolLeft.value) enrolLeft.value -= 1
  }, 1000)
  const voice = await aku.run(() => api.enrolMyVoice(ENROL_SECONDS))
  window.clearInterval(enrolTimer)
  enrolLeft.value = null
  if (voice && form.value) {
    form.value.my_voice = voice
    if (aku.settings) aku.settings.my_voice = voice
    flash('Your voice is saved')
  }
}

async function forgetMe() {
  if ((await aku.run(() => api.forgetMyVoice())) && form.value) {
    form.value.my_voice = []
    if (aku.settings) aku.settings.my_voice = []
  }
}

function toggleActive(name: string, on: boolean) {
  const f = form.value
  if (!f) return
  f.active_vocab_sets = on ? [...new Set([...f.active_vocab_sets, name])] : f.active_vocab_sets.filter((n) => n !== name)
  void save()
}
</script>

<template>
  <div class="page">
    <div class="row spread">
      <h1>Settings</h1>
      <span v-if="saved" class="badge finalised">{{ saved }}</span>
    </div>

    <template v-if="form">
      <div class="panel stack">
        <h2 style="margin: 0">You and your transcripts</h2>
        <label class="field">Your name in transcripts <input v-model="form.user_name" type="text" @change="save()" /></label>
        <label class="field">
          Default folder for finished transcripts
          <input v-model="form.output_folder" type="text" spellcheck="false" @change="save()" />
          <span class="hint">A full path, for example a folder in your Obsidian vault. You can change it per transcript when you finalise.</span>
        </label>
        <label class="check"><input v-model="form.notifications" type="checkbox" @change="save()" /> Desktop notification when recording starts and stops</label>
        <label class="check"><input v-model="form.tray" type="checkbox" @change="save()" /> Tray icon (takes effect after restarting Aku)</label>
      </div>

      <div class="panel stack">
        <h2 style="margin: 0">Hotkey</h2>
        <label class="check">
          <input v-model="form.global_shortcut" type="checkbox" @change="save()" />
          Start and stop recording with a global shortcut
        </label>
        <p v-if="form.shortcut.state === 'binding'" class="small" style="color: var(--warn); margin: 0">
          Your desktop is asking you to confirm the shortcut. Answer its dialog to finish.
        </p>
        <div v-else-if="form.shortcut.state === 'bound'" class="row small">
          <span>Shortcut: <b>{{ form.shortcut.trigger || 'set in your desktop settings' }}</b></span>
          <button @click="changeShortcut">Change…</button>
        </div>
        <p v-else-if="form.shortcut.state === 'unavailable'" class="small muted" style="margin: 0">
          This desktop does not offer global shortcuts to apps. Bind the command <code>aku toggle</code> to a key in its keyboard settings instead.
        </p>
        <p v-else-if="form.shortcut.state === 'error'" class="small" style="color: var(--accent); margin: 0">{{ form.shortcut.error }}</p>
        <p v-else class="muted small" style="margin: 0">
          The key starts a recording with your last settings, or stops the running one. Your desktop shows a dialog to confirm the key the first time.
        </p>
      </div>

      <div class="panel stack">
        <h2 style="margin: 0">Transcription</h2>
        <p v-if="!form.whisper_ready" class="small" style="color: var(--accent); margin: 0">
          whisper.cpp is not built yet. Run <code>scripts/build-whisper.sh</code>, or use the cloud backend.
        </p>
        <label class="field">
          Default language
          <select v-model="form.language" @change="save()">
            <option v-for="l in form.languages" :key="l.code" :value="l.code">
              {{ l.code === 'auto' ? 'Automatic (detect)' : l.name }}
            </option>
          </select>
          <span class="hint">You can change it per recording. The hotkey uses the language of your last recording.</span>
        </label>
        <label v-if="form.language !== 'auto'" class="check">
          <input type="checkbox" :checked="!form.translate" @change="form.translate = !($event.target as HTMLInputElement).checked; save()" />
          Keep other languages as they are spoken
        </label>
        <p v-if="form.language !== 'auto'" class="muted small" style="margin: -6px 0 0">
          Off: everything is written as the chosen language, so other languages are often translated, but not reliably.
          On: speech in another language is written in that language. You can change it per recording.
        </p>
        <label class="field">
          Default engine
          <select v-model="form.backend" @change="save()">
            <option value="local">On this computer (private)</option>
            <option value="cloud">OpenAI cloud</option>
          </select>
        </label>
        <label class="field">
          Local model
          <select v-model="form.local_model" @change="save()">
            <option v-for="(have, m) in form.models" :key="m" :value="m">{{ m }}{{ have ? '' : ' (downloads on first use)' }}</option>
          </select>
          <span class="hint">large-v3-turbo is the best fit for this laptop. Smaller models are faster but less accurate.</span>
        </label>
        <label class="field">
          Model for "Improve transcript"
          <select v-model="form.improve_model" @change="save()">
            <option v-for="(have, m) in form.models" :key="m" :value="m">{{ m }}{{ have ? '' : ' (downloads on first use)' }}</option>
          </select>
        </label>
        <label class="check"><input v-model="form.use_gpu" type="checkbox" @change="save()" /> Use the GPU (Vulkan)</label>
        <label class="field">
          Cloud model
          <select v-model="form.cloud_model" @change="save()">
            <option value="whisper-1">whisper-1 (word timings: splits lines where the speaker changes)</option>
            <option value="gpt-4o-transcribe">gpt-4o-transcribe (text only: each chunk goes to one speaker)</option>
            <option value="gpt-4o-mini-transcribe">gpt-4o-mini-transcribe (text only, cheaper)</option>
          </select>
        </label>
        <label class="field">
          OpenAI API key
          <div class="row">
            <input v-model="openaiKey" class="grow" style="width: auto" type="password" autocomplete="off" :placeholder="form.has_openai_key ? 'A key is saved. Paste a new one to replace it.' : 'sk-…'" />
            <button :disabled="!openaiKey.trim()" @click="saveKey('openai_api_key', openaiKey)">Save key</button>
            <button v-if="form.has_openai_key" class="danger" @click="saveKey('openai_api_key', '')">Remove</button>
          </div>
          <span class="hint">Stored in the system keyring, never in a file. With the cloud engine, call audio is sent to OpenAI: check your company's policy before using it on work calls.</span>
        </label>
      </div>

      <div class="panel stack">
        <h2 style="margin: 0">Telling speakers apart</h2>
        <label class="field">
          Method
          <select v-model="form.diarizer" @change="save()">
            <option value="auto">Automatic (pyannote when a token is set, otherwise built-in)</option>
            <option value="pyannote">pyannote community-1</option>
            <option value="basic">Built-in (no account needed)</option>
          </select>
          <span class="hint">In use now: {{ form.diarizer_in_use === 'pyannote' ? 'pyannote community-1' : 'built-in' }}.</span>
        </label>
        <label class="check">
          <input v-model="form.live_diarization" type="checkbox" @change="save()" />
          Show speakers during the call
        </label>
        <p class="muted small" style="margin: -6px 0 0">
          Uses the built-in method and some extra CPU while recording. You can set or change the number of speakers mid-call.
          The full pass after the call still decides the final labels.
        </p>
        <label class="field">
          Hugging Face token (for pyannote)
          <div class="row">
            <input v-model="hfToken" class="grow" style="width: auto" type="password" autocomplete="off" :placeholder="form.has_hf_token ? 'A token is saved. Paste a new one to replace it.' : 'hf_…'" />
            <button :disabled="!hfToken.trim()" @click="saveKey('hf_token', hfToken)">Save token</button>
            <button v-if="form.has_hf_token" class="danger" @click="saveKey('hf_token', '')">Remove</button>
          </div>
          <span class="hint">
            Create a free token at huggingface.co/settings/tokens and accept the conditions on the
            pyannote/speaker-diarization-community-1 model page. Everything still runs on this computer.
          </span>
        </label>
      </div>

      <div class="panel stack">
        <h2 style="margin: 0">Vocabulary</h2>
        <p class="muted small" style="margin: 0">
          Names and terms Aku should spell correctly, one per line. Tick the sets to use by default; you can change that per recording.
        </p>
        <div v-for="s in sets" :key="s.name" class="stack" style="gap: 6px; border-top: 1px solid var(--line); padding-top: 12px">
          <div class="row spread">
            <label class="check" style="font-weight: 600">
              <input type="checkbox" :checked="form.active_vocab_sets.includes(s.name)" @change="toggleActive(s.name, ($event.target as HTMLInputElement).checked)" />
              {{ s.name }}
            </label>
            <button class="ghost danger small" @click="removeSet(s.name)">Remove set</button>
          </div>
          <textarea v-model="s.text" spellcheck="false" :aria-label="`Terms in ${s.name}`" @change="saveVocab" />
          <div class="row">
            <input v-model="quick[s.name]" class="grow" style="width: auto" type="text" placeholder="Quick add a term" @keyup.enter="quickAdd(s.name)" />
            <button :disabled="!(quick[s.name] ?? '').trim()" @click="quickAdd(s.name)">Add</button>
          </div>
        </div>
        <div class="row">
          <input v-model="newSet" class="grow" style="width: auto" type="text" placeholder="New set name, e.g. ExampleCo work" @keyup.enter="addSet" />
          <button :disabled="!newSet.trim()" @click="addSet">Add set</button>
        </div>

        <h2 style="margin: 12px 0 0">Automatic corrections</h2>
        <p class="muted small" style="margin: 0">Applied to every transcript. You can also add one by selecting a word in a transcript.</p>
        <div v-for="r in rules" :key="r.wrong" class="row">
          <span class="grow">“{{ r.wrong }}” → <b>{{ r.right }}</b></span>
          <button class="ghost danger small" @click="removeRule(r)">Remove</button>
        </div>
        <div class="row">
          <input v-model="newRule.wrong" class="grow" style="width: auto" type="text" placeholder="Heard as, e.g. Collect" />
          <span>→</span>
          <input v-model="newRule.right" class="grow" style="width: auto" type="text" placeholder="Write as, e.g. ExampleCo" @keyup.enter="addRule" />
          <button :disabled="!newRule.wrong.trim() || !newRule.right.trim()" @click="addRule">Add</button>
        </div>
      </div>

      <div class="panel stack" data-panel="my-voice">
        <h2 style="margin: 0">Your voice</h2>
        <p class="muted small" style="margin: 0">
          For interview mode, where several people talk into one microphone: Aku uses a sample of your voice to label your
          lines with your name. It stays on this computer.
        </p>
        <p v-if="myVoice.length" style="margin: 0">
          Saved for the {{ myVoice.map((v) => methodName(v.space)).join(' and the ') }} method.
        </p>
        <p v-else class="muted" style="margin: 0">Not recorded yet.</p>
        <div v-if="enrolLeft !== null" class="stack" style="gap: 8px">
          <p v-if="enrolLeft > 0" style="margin: 0"><span class="dot rec" /><b>Recording: keep reading aloud for {{ enrolLeft }} more seconds.</b></p>
          <p v-else style="margin: 0"><b>Working on it…</b></p>
          <p style="margin: 0; line-height: 1.6">
            Read this in your normal speaking voice, at the distance you usually sit from the microphone. The words do not
            matter, only how you sound: Aku listens to my voice so that it can tell me apart from the other people in the
            room. I am reading at a calm, steady pace, the way I talk in a meeting or an interview. If I run out of text
            before the time is up, I simply start again from the top, or talk about what I did today, until the recording stops.
          </p>
        </div>
        <div v-else class="row">
          <button class="primary" :disabled="aku.status.recording || !!aku.status.enrolling" @click="enrol">
            {{ myVoice.length ? 'Record again' : `Record my voice (${ENROL_SECONDS} seconds)` }}
          </button>
          <button v-if="myVoice.length" class="danger" @click="forgetMe">Forget my voice</button>
          <span class="small muted">Uses the microphone of your last recording. You read a short text aloud.</span>
        </div>
        <p v-if="form.diarizer_in_use === 'pyannote' && myVoice.length && !myVoice.some((v) => v.space === 'community-1')" class="small" style="color: var(--warn); margin: 0">
          You now use pyannote, and your voice was recorded for the built-in method only. Record it again.
        </p>
      </div>

      <div class="panel stack">
        <h2 style="margin: 0">Saved voices</h2>
        <p class="muted small" style="margin: 0">
          Voiceprints of people you chose to remember. They never leave this computer. Forgetting one stops Aku suggesting that name.
        </p>
        <p v-if="!voices.length" class="muted">No voices saved.</p>
        <div v-for="v in voices" :key="v.name" class="row">
          <span class="grow">{{ v.name }} <span class="muted small">· {{ v.samples }} sample{{ v.samples === 1 ? '' : 's' }}</span></span>
          <button class="danger" @click="forget(v.name)">Forget</button>
        </div>
      </div>

      <p class="muted small">Aku {{ form.version }}</p>
    </template>
  </div>
</template>
