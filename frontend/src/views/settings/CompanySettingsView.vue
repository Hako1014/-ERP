<template>
  <div class="settings-page">
    <SettingsPageHeader
      title="公司设置"
      description="维护公司主体、组织架构、员工登录账户和 IP 访问规则。"
    />

    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="公司信息" name="company">
        <CompanyInfoPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="部门设置" name="departments" lazy>
        <DepartmentPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="职务设置" name="positions" lazy>
        <PositionPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="员工管理" name="staff" lazy>
        <StaffPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="IP 权限" name="ip-rules" lazy>
        <IpRulePanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane v-if="canManage" label="AI 助手" name="ai-assistant" lazy>
        <AssistantGovernancePanel />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import CompanyInfoPanel from '@/views/settings/company/CompanyInfoPanel.vue'
import DepartmentPanel from '@/views/settings/company/DepartmentPanel.vue'
import IpRulePanel from '@/views/settings/company/IpRulePanel.vue'
import PositionPanel from '@/views/settings/company/PositionPanel.vue'
import StaffPanel from '@/views/settings/company/StaffPanel.vue'
import AssistantGovernancePanel from '@/views/settings/company/AssistantGovernancePanel.vue'
import '@/views/settings/settings.css'

const authStore = useAuthStore()
const activeTab = ref('company')
const canManage = computed(() => authStore.hasPermission('settings:manage'))
</script>
