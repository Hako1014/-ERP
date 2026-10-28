<template>
  <el-drawer
    v-model="visible"
    :title="product?.name || '商品详情'"
    size="min(920px, 96vw)"
    destroy-on-close
  >
    <div v-loading="loading" class="product-detail">
      <div v-if="product" class="product-detail__summary">
        <el-image
          class="product-detail__image"
          :src="product.image || ''"
          fit="cover"
        >
          <template #error>
            <div class="product-detail__image-placeholder">
              <el-icon><Picture /></el-icon>
            </div>
          </template>
        </el-image>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="商品编码">
            {{ product.code }}
          </el-descriptions-item>
          <el-descriptions-item label="商品货号">
            {{ product.art_no || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="分类">
            {{ product.category_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="品牌">
            {{ product.brand_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="计量单位">
            {{ product.unit_name || '--' }}
          </el-descriptions-item>
          <el-descriptions-item label="网上售价">
            <MoneyText :value="product.online_price" />
          </el-descriptions-item>
          <el-descriptions-item label="上架状态">
            <StatusTag
              :status="product.listed ? 'ACTIVE' : 'DISABLED'"
              :label="product.listed ? '已上架' : '未上架'"
            />
          </el-descriptions-item>
          <el-descriptions-item label="禁用状态">
            <StatusTag
              :status="product.is_disabled ? 'DISABLED' : 'ACTIVE'"
              :label="product.is_disabled ? '已禁用' : '正常'"
            />
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <el-descriptions v-if="product" class="product-detail__prices" :column="3" border>
        <el-descriptions-item label="吊牌价">
          <MoneyText :value="product.tag_price" />
        </el-descriptions-item>
        <el-descriptions-item label="最低价">
          <MoneyText :value="product.min_price" />
        </el-descriptions-item>
        <el-descriptions-item label="成本价">
          <MoneyText :value="product.cost_price" />
        </el-descriptions-item>
        <el-descriptions-item label="重量(kg)">
          {{ product.weight ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="体积">
          {{ product.volume ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="税率(%)">
          {{ product.tax_rate ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="最小库存">
          {{ product.min_stock }}
        </el-descriptions-item>
        <el-descriptions-item label="最大库存">
          {{ product.max_stock }}
        </el-descriptions-item>
        <el-descriptions-item label="警戒库存">
          {{ product.warn_stock }}
        </el-descriptions-item>
      </el-descriptions>

      <section class="settings-page__panel">
        <div class="settings-page__toolbar">
          <h2>商品规格</h2>
          <el-button v-if="canManage" type="primary" @click="emit('create-sku')">
            新增规格
          </el-button>
        </div>
        <el-table :data="product?.skus || []" row-key="id">
          <el-table-column prop="spec_name" label="规格名称" min-width="130" />
          <el-table-column prop="spec_code" label="规格编码" min-width="130" />
          <el-table-column prop="barcode" label="条形码" min-width="130" />
          <el-table-column label="吊牌价" width="120" align="right">
            <template #default="{ row }">
              <MoneyText :value="row.tag_price" />
            </template>
          </el-table-column>
          <el-table-column label="销售价" width="120" align="right">
            <template #default="{ row }">
              <MoneyText :value="row.sale_price" />
            </template>
          </el-table-column>
          <el-table-column label="成本价" width="120" align="right">
            <template #default="{ row }">
              <MoneyText :value="row.cost_price" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <StatusTag
                :status="row.is_active ? 'ACTIVE' : 'DISABLED'"
                :label="row.is_active ? '启用' : '停用'"
              />
            </template>
          </el-table-column>
          <el-table-column
            v-if="canManage"
            label="操作"
            width="130"
            fixed="right"
          >
            <template #default="{ row }">
              <div class="settings-page__actions">
                <el-button link type="primary" @click="emit('edit-sku', row)">
                  编辑
                </el-button>
                <el-button link type="danger" @click="emit('delete-sku', row)">
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              description="暂无商品规格"
              action-text="新增规格"
              :show-action="canManage"
              @action="emit('create-sku')"
            />
          </template>
        </el-table>
      </section>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { Picture } from '@element-plus/icons-vue'

import type { ProductRecord, ProductSku } from '@/api/product'
import EmptyState from '@/components/EmptyState.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'

withDefaults(
  defineProps<{
    product: ProductRecord | null
    loading?: boolean
    canManage: boolean
  }>(),
  {
    loading: false,
  },
)

const visible = defineModel<boolean>('visible', { default: false })

const emit = defineEmits<{
  'create-sku': []
  'edit-sku': [sku: ProductSku]
  'delete-sku': [sku: ProductSku]
}>()
</script>

<style scoped>
.product-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.product-detail__summary {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 16px;
}

.product-detail__image,
.product-detail__image-placeholder {
  width: 180px;
  height: 180px;
}

.product-detail__image-placeholder {
  display: grid;
  place-items: center;
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
  color: var(--el-text-color-placeholder);
  font-size: 38px;
}

@media (max-width: 720px) {
  .product-detail__summary {
    grid-template-columns: 1fr;
  }
}
</style>
