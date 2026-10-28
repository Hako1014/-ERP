<template>
  <aside class="app-sidebar" :class="{ 'is-collapsed': collapsed }">
    <div class="app-sidebar__brand">
      <el-icon class="app-sidebar__logo"><Shop /></el-icon>
      <span v-show="!collapsed">电商ERP</span>
    </div>

    <el-scrollbar class="app-sidebar__scroll">
      <el-menu
        :default-active="activePath"
        :collapse="collapsed"
        :collapse-transition="false"
        @select="navigateMenuItem"
        class="app-sidebar__menu"
      >
        <template v-for="item in visibleMenu" :key="item.name">
          <el-sub-menu v-if="item.children?.length" :index="item.path">
            <template #title>
              <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </template>
            <el-menu-item
              v-for="child in item.children"
              :key="child.name"
              :index="child.path"
            >
              <span class="app-sidebar__child-dot" />
              <span>{{ child.title }}</span>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="item.path">
            <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
            <template #title>{{ item.title }}</template>
          </el-menu-item>
        </template>
      </el-menu>

      <el-empty
        v-if="!visibleMenu.length"
        description="暂无可访问模块"
        :image-size="56"
      />
    </el-scrollbar>

    <footer class="app-sidebar__footer">
      <div class="app-sidebar__company" :title="companyName">
        <el-icon><OfficeBuilding /></el-icon>
        <span v-show="!collapsed">{{ companyName }}</span>
      </div>
      <div class="app-sidebar__footer-actions">
        <el-button
          text
          class="app-sidebar__footer-button"
          aria-label="打开帮助"
          title="帮助"
          @click="openHelp"
        >
          <el-icon><QuestionFilled /></el-icon>
          <span v-show="!collapsed">帮助</span>
        </el-button>

        <el-tooltip
          :content="collapsed ? '展开菜单' : '收起菜单'"
          placement="right"
          :disabled="!collapsed"
        >
          <el-button
            text
            class="app-sidebar__footer-button"
            :aria-label="collapsed ? '展开菜单' : '收起菜单'"
            @click="emit('toggle')"
          >
            <el-icon><Expand v-if="collapsed" /><Fold v-else /></el-icon>
            <span v-show="!collapsed">{{ collapsed ? '展开菜单' : '收起菜单' }}</span>
          </el-button>
        </el-tooltip>
      </div>
      <span v-show="!collapsed" class="app-sidebar__version">版本 {{ appVersion }}</span>
    </footer>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  Expand,
  Fold,
  OfficeBuilding,
  QuestionFilled,
  Shop,
} from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import packageJson from '../../../package.json'

import { filterMenuTree, flattenMenuItems, menuTree } from '@/router/menu'
import { isRegisteredAssistantPath } from '@/layouts/components/assistantShell'
import { useAuthStore } from '@/stores/auth'
import { useWorkbenchPanelStore } from '@/stores/workbenchPanels'

defineProps<{
  collapsed: boolean
}>()

const emit = defineEmits<{
  toggle: []
}>()

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const workbenchStore = useWorkbenchPanelStore()
const appVersion = packageJson.version

const activePath = computed(() => route.path)
const companyName = computed(() =>
  authStore.profile?.company_name ||
  (authStore.profileLoaded ? '公司信息不可用' : '正在加载公司信息…'),
)
const visibleMenu = computed(() =>
  filterMenuTree(menuTree, authStore.permissions),
)

function openHelp(): void {
  workbenchStore.open('help')
}

function navigateMenuItem(path: string): void {
  if (!flattenMenuItems(visibleMenu.value).some((item) => item.path === path)) {
    return
  }
  if (path !== '/assistant') {
    void router.push(path)
    return
  }

  const sourcePath =
    route.path !== '/assistant' &&
    isRegisteredAssistantPath(route.path, authStore.permissions)
      ? route.path
      : undefined
  void router.push({
    path,
    query: sourcePath ? { from: sourcePath } : undefined,
  })
}
</script>

<style scoped>
.app-sidebar {
  position: sticky;
  top: 0;
  flex: 0 0 232px;
  width: 232px;
  min-width: 232px;
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: #17202b;
  color: #d8e0ea;
  transition: width 160ms ease, min-width 160ms ease;
}

.app-sidebar.is-collapsed {
  flex-basis: 64px;
  width: 64px;
  min-width: 64px;
}

.app-sidebar__brand {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid rgb(255 255 255 / 10%);
  color: #fff;
  font-size: 17px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
}

.app-sidebar__logo {
  font-size: 22px;
  flex: 0 0 auto;
}

.app-sidebar__scroll {
  flex: 1 1 auto;
  min-height: 0;
}

.app-sidebar__footer {
  flex: 0 0 auto;
  padding: 10px 12px 8px;
  border-top: 1px solid rgb(255 255 255 / 10%);
  background: #17202b;
}

.app-sidebar__company {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  min-height: 30px;
  padding: 0 8px;
  color: #e2e8f0;
  font-size: 12px;
}

.app-sidebar__company .el-icon {
  flex: 0 0 auto;
  font-size: 16px;
}

.app-sidebar__company span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidebar__footer-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.app-sidebar__footer-button.el-button {
  flex: 1;
  justify-content: flex-start;
  min-width: 0;
  height: 34px;
  margin: 0;
  padding: 0 8px;
  color: #cbd5e1;
}

.app-sidebar__footer-button.el-button:hover,
.app-sidebar__footer-button.el-button:focus-visible {
  color: #fff;
  background: rgb(255 255 255 / 7%);
}

.app-sidebar__footer-button .el-icon {
  flex: 0 0 auto;
  font-size: 16px;
}

.app-sidebar__footer-button span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidebar__version {
  display: block;
  padding: 4px 8px 0;
  color: #94a3b8;
  font-size: 11px;
}

.app-sidebar__help-content p {
  margin: 8px 0 4px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.5;
}

.app-sidebar.is-collapsed .app-sidebar__footer {
  padding: 8px 6px;
}

.app-sidebar.is-collapsed .app-sidebar__company {
  justify-content: center;
  padding: 0;
}

.app-sidebar.is-collapsed .app-sidebar__footer-actions {
  flex-direction: column;
}

.app-sidebar.is-collapsed .app-sidebar__footer-button.el-button {
  flex: 0 0 36px;
  justify-content: center;
  padding: 0;
}

.app-sidebar__menu {
  border-right: 0;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #cbd5e1;
  --el-menu-hover-bg-color: rgb(255 255 255 / 7%);
  --el-menu-active-color: #f8fafc;
}

.app-sidebar__menu :deep(.el-menu-item.is-active) {
  background: #2563eb;
  color: #fff;
}

.app-sidebar__menu :deep(.el-sub-menu__title:hover),
.app-sidebar__menu :deep(.el-menu-item:hover) {
  color: #fff;
}

.app-sidebar__child-dot {
  width: 5px;
  height: 5px;
  margin-right: 10px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.6;
}
</style>
