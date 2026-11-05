import { createRouter, createWebHashHistory } from 'vue-router'
import LiveView from './views/LiveView.vue'
import AdminView from './views/AdminView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'live', component: LiveView, meta: { title: '实时状态' } },
    { path: '/admin', name: 'admin', component: AdminView, meta: { title: '管理后台' } },
  ],
})
