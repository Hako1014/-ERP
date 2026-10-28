import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'

import { setUnauthorizedHandler } from '@/api/client'
import { router } from '@/router'
import { installRouterGuards } from '@/router/guards'
import { useAuthStore } from '@/stores/auth'
import '@/styles/base.css'
import App from './App.vue'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
installRouterGuards(router, pinia)
app.use(router)
app.use(ElementPlus)

const authStore = useAuthStore(pinia)
setUnauthorizedHandler(() => {
  authStore.clearSession()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.push({
      path: '/login',
      query: { redirect: current.fullPath },
    })
  }
})

app.mount('#app')
