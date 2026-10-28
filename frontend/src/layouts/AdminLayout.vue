<template>
  <div class="admin-layout">
    <AppSidebar :collapsed="collapsed" @toggle="collapsed = !collapsed" />
    <div class="admin-layout__body">
      <AppHeader :collapsed="collapsed" @toggle="collapsed = !collapsed" />
      <div class="admin-layout__breadcrumb">
        <AppBreadcrumb />
      </div>
      <main
        class="admin-layout__content"
        :class="{ 'is-assistant-workspace': isAssistantWorkspace }"
      >
        <router-view />
        <AssistantFloat />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AssistantFloat from '@/layouts/components/AssistantFloat.vue'
import AppBreadcrumb from '@/layouts/components/AppBreadcrumb.vue'
import AppHeader from '@/layouts/components/AppHeader.vue'
import AppSidebar from '@/layouts/components/AppSidebar.vue'
import { useAuthStore } from '@/stores/auth'

const collapsed = ref(false)
const authStore = useAuthStore()
const route = useRoute()
const isAssistantWorkspace = computed(() => route.name === 'assistant')

let wasNarrowViewport = false

function syncSidebarForViewport(): void {
  const isNarrowViewport = window.innerWidth <= 900
  if (isNarrowViewport && !wasNarrowViewport) {
    collapsed.value = true
  }
  wasNarrowViewport = isNarrowViewport
}

onMounted(() => {
  void authStore.ensureProfile().catch(() => undefined)
  syncSidebarForViewport()
  window.addEventListener('resize', syncSidebarForViewport)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncSidebarForViewport)
})
</script>

<style scoped>
.admin-layout {
  min-height: 100vh;
  display: flex;
  background: #f4f6f8;
}

.admin-layout__body {
  flex: 1;
  min-width: 0;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.admin-layout__breadcrumb {
  min-height: 44px;
  display: flex;
  align-items: center;
  padding: 0 22px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.admin-layout__content {
  flex: 1;
  min-width: 0;
  padding: 18px;
}

.admin-layout__content.is-assistant-workspace {
  position: relative;
  min-height: 0;
  overflow: hidden;
  padding: 0;
  background: var(--el-bg-color);
}

@media (max-width: 900px) {
  .admin-layout__content {
    padding: 12px;
  }

  .admin-layout__content.is-assistant-workspace {
    padding: 0;
  }

  .admin-layout__breadcrumb {
    padding: 0 14px;
  }
}
</style>
