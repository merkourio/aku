<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import type { Devices, Sources } from '../types'

const sources = defineModel<Sources>({ required: true })
// micOnly: interview mode without computer audio, so only the microphone is chosen
const props = defineProps<{ micOnly?: boolean; room?: boolean }>()
const devices = ref<Devices>({ inputs: [], outputs: [], apps: [] })
let timer: number | undefined

async function refresh() {
  try {
    devices.value = await api.devices()
  } catch {
    /* the service is restarting; the next poll will catch up */
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 2500) // apps come and go: keep the list live
})
onUnmounted(() => window.clearInterval(timer))

// Apps that are playing now, plus chosen ones that are not (e.g. Teams before the call starts).
const apps = computed(() => {
  const live = devices.value.apps.map((a) => ({ key: a.key, label: a.label, playing: true }))
  const missing = sources.value.apps
    .filter((k) => !live.some((a) => a.key === k))
    .map((k) => ({ key: k, label: k, playing: false }))
  return [...live, ...missing]
})

function toggleApp(key: string, on: boolean) {
  const set = new Set(sources.value.apps)
  if (on) set.add(key)
  else set.delete(key)
  sources.value = { ...sources.value, apps: [...set] }
}
</script>

<template>
  <div class="stack">
    <label class="field">
      {{ props.room ? 'Microphone (everyone in the room)' : 'Microphone (you)' }}
      <select v-model="sources.input">
        <option :value="null">System default</option>
        <option v-for="d in devices.inputs" :key="d.name" :value="d.name">
          {{ d.description }}{{ d.default ? ' (current default)' : '' }}
        </option>
      </select>
    </label>

    <div v-if="!props.micOnly" class="field">
      <span style="font-weight: 500; font-size: 0.9rem">Computer audio (them)</span>
      <label class="check"><input v-model="sources.scope" type="radio" value="all" /> Everything playing on an output device</label>
      <label class="check"><input v-model="sources.scope" type="radio" value="apps" /> Only specific apps</label>
    </div>

    <label v-if="!props.micOnly && sources.scope === 'all'" class="field">
      Output device
      <select v-model="sources.output">
        <option :value="null">System default</option>
        <option v-for="d in devices.outputs" :key="d.name" :value="d.name">
          {{ d.description }}{{ d.default ? ' (current default)' : '' }}
        </option>
      </select>
    </label>

    <div v-else-if="!props.micOnly" class="stack" style="gap: 6px">
      <label v-for="a in apps" :key="a.key" class="check">
        <input type="checkbox" :checked="sources.apps.includes(a.key)" @change="toggleApp(a.key, ($event.target as HTMLInputElement).checked)" />
        {{ a.label }} <span v-if="!a.playing" class="muted small">(not playing right now; picked up when it starts)</span>
      </label>
      <p v-if="!apps.length" class="muted small">
        No app is playing audio right now. Start the call (or play something in the app) and it will show up here.
      </p>
    </div>
  </div>
</template>
