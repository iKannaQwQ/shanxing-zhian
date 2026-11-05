import { createRouter, createWebHashHistory } from 'vue-router'
import UserLayout from './layouts/UserLayout.vue'
import AdminLayout from './layouts/AdminLayout.vue'
import HomeView from './views/user/HomeView.vue'
import OverviewView from './views/admin/OverviewView.vue'
import MonitorView from './views/admin/MonitorView.vue'
import EventsView from './views/admin/EventsView.vue'
import HistoryView from './views/admin/HistoryView.vue'
import DevicesView from './views/admin/DevicesView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: UserLayout,
      children: [{ path: '', name: 'home', component: HomeView }],
    },
    {
      path: '/admin',
      component: AdminLayout,
      children: [
        { path: '', name: 'admin', component: OverviewView },
        { path: 'monitor', name: 'admin-monitor', component: MonitorView },
        { path: 'events', name: 'admin-events', component: EventsView },
        { path: 'history', name: 'admin-history', component: HistoryView },
        { path: 'devices', name: 'admin-devices', component: DevicesView },
      ],
    },
  ],
})
