import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
import RecordView from './views/RecordView.vue'
import SessionView from './views/SessionView.vue'
import SessionsView from './views/SessionsView.vue'
import SettingsView from './views/SettingsView.vue'

// Hash routes, so UI paths never collide with API paths like /sessions.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: RecordView },
    { path: '/sessions', component: SessionsView },
    { path: '/sessions/:id', component: SessionView, props: true },
    { path: '/settings', component: SettingsView },
  ],
})

createApp(App).use(createPinia()).use(router).mount('#app')
