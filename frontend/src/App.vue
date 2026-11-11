<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { clock } from './format'
import { useAku } from './store'

const aku = useAku()
onMounted(() => {
  aku.connect()
  void aku.loadSettings()
})
</script>

<template>
  <div class="shell">
    <nav class="side">
      <div class="brand"><img src="/favicon.svg" alt="" /> Aku</div>
      <RouterLink class="nav" to="/" exact-active-class="router-link-active" active-class="">
        <span v-if="aku.status.recording" class="dot rec" />Record
      </RouterLink>
      <RouterLink class="nav" to="/sessions">Transcripts</RouterLink>
      <RouterLink class="nav" to="/settings">Settings</RouterLink>
      <div class="foot">
        <template v-if="!aku.connected"><span class="dot" />Service not reachable</template>
        <template v-else-if="aku.status.recording"><span class="dot rec" />{{ clock(aku.status.elapsed) }}</template>
        <template v-else><span class="dot ok" />Ready</template>
      </div>
    </nav>
    <main class="main">
      <RouterView />
    </main>
    <div class="toasts" aria-live="polite">
      <div v-for="t in aku.toasts" :key="t.id" class="toast" :class="t.kind">
        <div class="grow">{{ t.text }}</div>
        <button class="ghost" aria-label="Dismiss" @click="aku.dismiss(t.id)">✕</button>
      </div>
    </div>
  </div>
</template>
