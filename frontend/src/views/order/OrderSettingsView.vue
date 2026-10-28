<template>
  <div class="settings-page order-page">
    <SettingsPageHeader
      title="订单设置"
      description="查看系统当前启用的订单标识和状态流转口径。"
    />

    <section class="settings-page__panel">
      <h2 class="order-section__title">订单标识</h2>
      <el-table v-loading="loading" :data="flags" border>
        <el-table-column prop="code" label="标识编码" min-width="180" />
        <el-table-column prop="label" label="显示名称" min-width="180" />
        <el-table-column label="使用状态" width="110" align="center">
          <template #default>
            <StatusTag status="ENABLED" label="启用" tone="success" />
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="settings-page__panel">
      <h2 class="order-section__title">订单状态</h2>
      <el-table :data="statuses" border>
        <el-table-column prop="code" label="状态编码" min-width="140" />
        <el-table-column prop="label" label="显示名称" min-width="140" />
        <el-table-column prop="description" label="业务口径" min-width="320" />
      </el-table>
    </section>

    <section class="settings-page__panel">
      <h2 class="order-section__title">审核状态</h2>
      <el-table :data="auditStatuses" border>
        <el-table-column prop="code" label="状态编码" min-width="140" />
        <el-table-column prop="label" label="显示名称" min-width="140" />
        <el-table-column prop="description" label="业务口径" min-width="320" />
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { orderApi, type OrderFlag } from '@/api/order'
import StatusTag from '@/components/StatusTag.vue'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'
import '@/views/order/order.css'

const loading = ref(false)
const flags = ref<OrderFlag[]>([])

const statuses = [
  { code: 'NEW', label: '新订单', description: '新建或抓取后尚未审核通过，可编辑和提交审批。' },
  { code: 'PENDING', label: '待处理', description: '审批通过，等待打印、拣货和扫描发货。' },
  { code: 'SHIPPED', label: '已发货', description: '订单商品已全部完成出库和包裹发货。' },
  { code: 'DONE', label: '已完成', description: '订单履约流程结束。' },
  { code: 'CANCELLED', label: '已取消', description: '订单在履约前取消，不进入发货流程。' },
]

const auditStatuses = [
  { code: 'PENDING', label: '待审核', description: '审批流正在流转，尚未得到最终结果。' },
  { code: 'APPROVED', label: '已审核', description: '审批流全部通过，订单进入待履约状态。' },
  { code: 'REJECTED', label: '已驳回', description: '审批被驳回，可修改后重新提交。' },
]

async function loadFlags(): Promise<void> {
  loading.value = true
  try {
    flags.value = await orderApi.flags()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '订单标识加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadFlags)
</script>
