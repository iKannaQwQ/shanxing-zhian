import { createRouter, createWebHashHistory } from 'vue-router'
import UserLayout from './layouts/UserLayout.vue'
import AdminLayout from './layouts/AdminLayout.vue'
import HomeView from './views/user/HomeView.vue'
import OverviewView from './views/admin/OverviewView.vue'

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
      children: [{ path: '', name: 'admin', component: OverviewView }],
    },
  ],
})
