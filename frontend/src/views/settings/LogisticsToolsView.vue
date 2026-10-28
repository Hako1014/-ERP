<template>
  <div class="settings-page">
    <SettingsPageHeader
      title="物流工具"
      description="维护物流方式、运费模板、区域匹配规则和打印模板。"
    />
    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="物流方式" name="methods">
        <LogisticsMethodPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="运费模板" name="freight" lazy>
        <FreightTemplatePanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="区域物流" name="areas" lazy>
        <AreaRulePanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="打印模板" name="prints" lazy>
        <PrintTemplatePanel :can-manage="canManage" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import AreaRulePanel from '@/views/settings/logistics/AreaRulePanel.vue'
import FreightTemplatePanel from '@/views/settings/logistics/FreightTemplatePanel.vue'
import LogisticsMethodPanel from '@/views/settings/logistics/LogisticsMethodPanel.vue'
import PrintTemplatePanel from '@/views/settings/logistics/PrintTemplatePanel.vue'
import '@/views/settings/settings.css'

const authStore = useAuthStore()
const activeTab = ref('methods')
const canManage = computed(() => authStore.hasPermission('settings:manage'))
</script>
