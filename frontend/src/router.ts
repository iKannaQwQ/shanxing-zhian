import { createRouter, createWebHashHistory } from 'vue-router'
import UserLayout from './layouts/UserLayout.vue'
import AdminLayout from './layouts/AdminLayout.vue'
import HomeView from './views/user/HomeView.vue'
import OverviewView from './views/admin/OverviewView.vue'
import MonitorView from './views/admin/MonitorView.vue'

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
      ],
    },
  ],
})
