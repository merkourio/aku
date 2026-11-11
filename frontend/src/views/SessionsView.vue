<script setup lang="ts">
import { onMounted } from 'vue'
import { api } from '../api'
import { clock, statusLabel, when } from '../format'
import { useAku } from '../store'

const aku = useAku()
onMounted(() => void aku.loadSessions())

async function recover(id: string) {
  await aku.run(() => api.recover(id))
}
</script>

<template>
  <div class="page">
    <h1>Transcripts</h1>
    <p class="muted">Every recording, newest first.</p>

    <div v-if="!aku.sessions.length" class="panel empty">
      Nothing recorded yet. <RouterLink to="/">Record a call</RouterLink> to get started.
    </div>

    <RouterLink v-for="s in aku.sessions" :key="s.id" :to="`/sessions/${s.id}`" class="panel row spread" style="text-decoration: none">
      <div class="grow">
        <div style="font-weight: 600">{{ s.title || 'Untitled call' }}</div>
        <div class="muted small">{{ when(s.created) }} · {{ clock(s.duration) }}<template v-if="!s.has_audio"> · audio deleted</template></div>
        <div v-if="aku.status.jobs[s.id]" class="small muted" style="margin-top: 6px">
          {{ aku.status.jobs[s.id].step }}
          <div class="bar" :class="{ busy: aku.status.jobs[s.id].progress === null }" style="margin-top: 4px; max-width: 260px">
            <i :style="{ width: `${Math.round((aku.status.jobs[s.id].progress ?? 0) * 100)}%` }" />
          </div>
        </div>
      </div>
      <button v-if="(s.status === 'interrupted' || s.status === 'error') && !aku.status.jobs[s.id] && s.has_audio" @click.prevent="recover(s.id)">Recover</button>
      <span class="badge" :class="s.status">{{ statusLabel[s.status] ?? s.status }}</span>
    </RouterLink>
  </div>
</template>
