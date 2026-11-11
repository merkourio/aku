<script setup lang="ts">
import { clock, speakerSlot } from '../format'
import type { Line } from '../types'

defineProps<{ lines: Line[]; playable?: boolean }>()
defineEmits<{ play: [line: Line] }>()
</script>

<template>
  <div class="transcript">
    <div v-for="line in lines" :key="line.id" class="line" :data-line="line.id">
      <button v-if="playable" class="time" :title="'Play from ' + clock(line.start)" @click="$emit('play', line)">
        {{ clock(line.start) }}
      </button>
      <span v-else class="time" style="cursor: default">{{ clock(line.start) }}</span>
      <div>
        <span class="who" :class="'s' + speakerSlot(line.speaker)">{{ line.name }}:</span>
        <span class="text"> {{ line.text }}</span>
      </div>
    </div>
  </div>
</template>
