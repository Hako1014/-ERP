<template>
  <div class="settings-page product-page">
    <SettingsPageHeader
      title="商品设置"
      description="维护商品分类、品牌、计量单位和规格基础资料。"
    />

    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="商品分类" name="categories">
        <CategoryPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="品牌管理" name="brands" lazy>
        <BrandPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="计量单位" name="units" lazy>
        <UnitPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="计量单位组" name="unit-groups" lazy>
        <UnitGroupPanel :can-manage="canManage" />
      </el-tab-pane>
      <el-tab-pane label="规格类型 / 明细" name="specs" lazy>
        <SpecPanel :can-manage="canManage" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import BrandPanel from '@/views/product/settings/BrandPanel.vue'
import CategoryPanel from '@/views/product/settings/CategoryPanel.vue'
import SpecPanel from '@/views/product/settings/SpecPanel.vue'
import UnitGroupPanel from '@/views/product/settings/UnitGroupPanel.vue'
import UnitPanel from '@/views/product/settings/UnitPanel.vue'
import '@/views/settings/settings.css'
import '@/views/product/product.css'

const authStore = useAuthStore()
const activeTab = ref('categories')
const canManage = computed(() => authStore.hasPermission('settings:manage'))
</script>
