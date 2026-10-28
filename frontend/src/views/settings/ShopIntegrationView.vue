<template>
  <div class="settings-page">
    <SettingsPageHeader
      title="店铺对接"
      description="维护电商平台、店铺参数、同步开关与 API 密钥。真实平台抓单将在第三方对接任务中启用。"
    />
    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="平台管理" name="platforms">
        <PlatformPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="店铺管理" name="shops" lazy>
        <ShopPanel :can-manage="canManage" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PlatformPanel from '@/views/settings/shop/PlatformPanel.vue'
import ShopPanel from '@/views/settings/shop/ShopPanel.vue'
import '@/views/settings/settings.css'

const authStore = useAuthStore()
const activeTab = ref('platforms')
const canManage = computed(() => authStore.hasPermission('settings:manage'))
</script>
