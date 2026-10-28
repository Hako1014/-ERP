<template>
  <div class="settings-page stock-page">
    <SettingsPageHeader
      title="库存功能选项"
      description="维护企业级库存同步、店铺独享库存、浮动成本和低于预警下架策略。"
    >
      <template #actions>
        <el-button
          v-if="canManage"
          type="primary"
          :loading="saving"
          @click="save"
        >
          保存设置
        </el-button>
      </template>
    </SettingsPageHeader>

    <section v-loading="loading" class="settings-page__panel">
      <el-form label-width="190px" class="stock-options-form">
        <el-form-item label="默认参与线上库存同步">
          <el-switch v-model="form.sync_stock_default" :disabled="!canManage" />
          <div class="stock-muted">
            新建仓库时默认继承此选项；平台真实同步由后续平台对接任务执行。
          </div>
        </el-form-item>
        <el-form-item label="启用店铺独享库存">
          <el-switch v-model="form.shop_stock_enabled" :disabled="!canManage" />
          <div class="stock-muted">
            未配置独享额度的店铺不共享仓库剩余库存。
          </div>
        </el-form-item>
        <el-form-item label="启用浮动成本">
          <el-switch
            v-model="form.floating_cost_enabled"
            :disabled="!canManage"
          />
          <div class="stock-muted">
            浮动成本按期间入库加权平均，用于出库成本核算。
          </div>
        </el-form-item>
        <el-form-item label="低于预警时禁止下架">
          <el-switch
            v-model="form.off_shelf_below_warning"
            :disabled="!canManage"
          />
          <div class="stock-muted">
            开启后，可用库存低于预警值的商品不允许执行出库下架。
          </div>
        </el-form-item>
      </el-form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { stockSettingApi, type StockSettingPayload } from '@/api/stock'
import { useAuthStore } from '@/stores/auth'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/stock/stock.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('stock:manage'))
const loading = ref(false)
const saving = ref(false)
const form = reactive<StockSettingPayload>({
  sync_stock_default: false,
  shop_stock_enabled: false,
  floating_cost_enabled: false,
  off_shelf_below_warning: false,
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await stockSettingApi.get()
    Object.assign(form, {
      sync_stock_default: result.sync_stock_default,
      shop_stock_enabled: result.shop_stock_enabled,
      floating_cost_enabled: result.floating_cost_enabled,
      off_shelf_below_warning: result.off_shelf_below_warning,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存设置加载失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await stockSettingApi.update({ ...form })
    ElMessage.success('库存设置已保存')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '库存设置保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.stock-options-form {
  max-width: 760px;
}
</style>
