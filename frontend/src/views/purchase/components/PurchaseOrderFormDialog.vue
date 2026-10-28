<template>
  <FormDialog
    v-model:visible="visible"
    :title="dialogTitle"
    width="min(1120px, 96vw)"
    :loading="loading"
    :submit-text="mode === 'from-plan' ? '生成订单' : '保存订单'"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="供应商" required>
        <el-select
          v-model="supplierId"
          filterable
          :disabled="mode === 'from-plan'"
          placeholder="请选择供应商"
        >
          <el-option
            v-for="item in supplierOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="入库仓库">
        <el-select
          v-model="warehouseId"
          clearable
          filterable
          placeholder="可稍后指定"
        >
          <el-option
            v-for="item in warehouseOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
            :disabled="!item.selectable"
          />
        </el-select>
      </el-form-item>
      <el-form-item class="is-full-width" label="采购描述">
        <el-input
          v-model="description"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
    </div>

    <p v-if="mode === 'from-plan'" class="purchase-dialog-tip">
      采购计划：{{ plan?.plan_no }}。转单数量不得超过计划剩余数量。
    </p>

    <div v-if="mode !== 'from-plan'" class="purchase-items">
      <div
        v-for="(item, index) in items"
        :key="item.key"
        class="purchase-item"
      >
        <div class="purchase-item__header">
          <span class="purchase-item__title">采购明细 {{ index + 1 }}</span>
          <el-button
            link
            type="danger"
            :disabled="items.length === 1"
            @click="removeItem(index)"
          >
            移除
          </el-button>
        </div>
        <div class="purchase-item__grid">
          <el-select
            v-model="item.product_id"
            filterable
            remote
            reserve-keyword
            :remote-method="searchProducts"
            :loading="productLoading"
            placeholder="输入商品编码或名称搜索"
            @change="(value: number) => handleProductChange(item, value)"
          >
            <el-option
              v-for="option in productOptions"
              :key="option.id"
              :label="`${option.code} · ${option.name}`"
              :value="option.id"
            />
          </el-select>
          <el-select
            v-model="item.sku_id"
            clearable
            filterable
            :loading="item.skuLoading"
            placeholder="商品级 / 选择规格"
          >
            <el-option
              v-for="sku in item.skuOptions"
              :key="sku.id"
              :label="skuLabel(sku)"
              :value="sku.id"
            />
          </el-select>
          <el-input
            v-model="item.qty"
            inputmode="decimal"
            placeholder="采购数量"
          />
          <el-input
            v-model="item.purchase_price"
            inputmode="decimal"
            placeholder="采购价，可由后端取价"
          />
        </div>
      </div>
      <el-button @click="addItem">新增采购明细</el-button>
    </div>

    <el-table
      v-else
      :data="planItems"
      border
      row-key="key"
      max-height="420"
    >
      <el-table-column label="商品" min-width="200">
        <template #default="{ row }">
          <div>{{ row.product_name || '--' }}</div>
          <div class="purchase-muted">{{ row.product_code || '--' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="规格" min-width="140">
        <template #default="{ row }">
          {{ row.spec_code || row.spec_name || '商品级' }}
        </template>
      </el-table-column>
      <el-table-column label="计划数量" width="110" align="right">
        <template #default="{ row }">{{ row.planned_qty }}</template>
      </el-table-column>
      <el-table-column label="已采购" width="100" align="right">
        <template #default="{ row }">{{ row.purchased_qty }}</template>
      </el-table-column>
      <el-table-column label="剩余可转" width="110" align="right">
        <template #default="{ row }">{{ row.remaining_qty }}</template>
      </el-table-column>
      <el-table-column label="本次转单数量" width="160">
        <template #default="{ row }">
          <el-input v-model="row.qty" inputmode="decimal" />
        </template>
      </el-table-column>
      <el-table-column label="采购价" width="130" align="right">
        <template #default="{ row }">
          <MoneyText :value="row.purchase_price" />
        </template>
      </el-table-column>
    </el-table>

    <section class="purchase-detail-section">
      <h3 class="purchase-detail-section__title">附件</h3>
      <AttachmentUpload
        v-model="attachmentItems"
        :max-count="50"
        list-type="text"
      />
    </section>
  </FormDialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  purchaseOrderApi,
  type PurchaseOrder,
  type PurchaseOrderCreatePayload,
  type PurchaseOrderFromPlanPayload,
  type PurchasePlan,
  type PurchasePlanItem,
  type WarehouseOption,
} from '@/api/purchase'
import {
  productOptionsApi,
  type ProductOption,
  type ProductSkuOption,
} from '@/api/productOptions'
import type { PartyRecord } from '@/api/party'
import AttachmentUpload from '@/components/business/AttachmentUpload.vue'
import type { AttachmentItem } from '@/components/business/types'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'

interface OrderItemDraft {
  key: string
  product_id?: number
  sku_id?: number | null
  skuOptions: ProductSkuOption[]
  skuLoading: boolean
  qty: string
  purchase_price: string
}

interface PlanItemDraft extends PurchasePlanItem {
  key: string
  qty: string
}

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    supplierOptions?: PartyRecord[]
    warehouseOptions?: WarehouseOption[]
    loading?: boolean
  }>(),
  {
    supplierOptions: () => [],
    warehouseOptions: () => [],
    loading: false,
  },
)

const emit = defineEmits<{
  submit: [
    payload: PurchaseOrderCreatePayload | PurchaseOrderFromPlanPayload,
  ]
}>()

const mode = ref<'create' | 'edit' | 'from-plan'>('create')
const editingId = ref<number | null>(null)
const supplierId = ref<number | null>(null)
const warehouseId = ref<number | null>(null)
const description = ref('')
const attachmentItems = ref<AttachmentItem[]>([])
const items = ref<OrderItemDraft[]>([])
const plan = ref<PurchasePlan | null>(null)
const planItems = ref<PlanItemDraft[]>([])
const productOptions = ref<ProductOption[]>([])
const productLoading = ref(false)
let itemSequence = 0

const dialogTitle = computed(() => {
  if (mode.value === 'edit') {
    return '编辑采购订单'
  }
  if (mode.value === 'from-plan') {
    return '采购计划转采购订单'
  }
  return '新建采购订单'
})

function newItem(): OrderItemDraft {
  itemSequence += 1
  return {
    key: `order-item-${itemSequence}`,
    product_id: undefined,
    sku_id: null,
    skuOptions: [],
    skuLoading: false,
    qty: '',
    purchase_price: '',
  }
}

function reset(): void {
  mode.value = 'create'
  editingId.value = null
  supplierId.value = null
  warehouseId.value = null
  description.value = ''
  attachmentItems.value = []
  items.value = [newItem()]
  plan.value = null
  planItems.value = []
}

function openCreate(): void {
  reset()
  visible.value = true
  void searchProducts('')
}

function openEdit(order: PurchaseOrder): void {
  reset()
  mode.value = 'edit'
  editingId.value = order.id
  supplierId.value = order.supplier_id
  warehouseId.value = order.warehouse_id || null
  description.value = order.description || ''
  attachmentItems.value = (order.attachments || []).map((url, index) => ({
    uid: `existing-${index}`,
    name: url.split('/').pop() || `附件 ${index + 1}`,
    url,
  }))
  items.value = (order.items || []).map((item) => ({
    key: `order-item-${item.id}`,
    product_id: item.product_id,
    sku_id: item.sku_id || null,
    skuOptions: item.sku_id
      ? [
          {
            id: item.sku_id,
            product_id: item.product_id,
            spec_code: item.spec_code,
            spec_name: item.spec_name,
          },
        ]
      : [],
    skuLoading: false,
    qty: String(item.qty),
    purchase_price:
      item.purchase_price === null || item.purchase_price === undefined
        ? ''
        : String(item.purchase_price),
  }))
  if (!items.value.length) {
    items.value = [newItem()]
  }
  visible.value = true
  void searchProducts('')
}

function openFromPlan(planRecord: PurchasePlan): void {
  reset()
  mode.value = 'from-plan'
  plan.value = planRecord
  supplierId.value = planRecord.supplier_id
  planItems.value = (planRecord.items || [])
    .filter((item) => Number(item.remaining_qty) > 0)
    .map((item) => ({
      ...item,
      key: `plan-item-${item.id}`,
      qty: String(item.remaining_qty),
    }))
  visible.value = true
}

async function searchProducts(keyword: string): Promise<void> {
  productLoading.value = true
  try {
    const result = await productOptionsApi.listProducts(keyword, 1, 50)
    productOptions.value = result.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品搜索失败')
  } finally {
    productLoading.value = false
  }
}

async function handleProductChange(
  item: OrderItemDraft,
  productId?: number,
): Promise<void> {
  item.sku_id = null
  item.skuOptions = []
  if (!productId) {
    return
  }
  item.skuLoading = true
  try {
    item.skuOptions = await productOptionsApi.listSkus(productId)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格加载失败')
  } finally {
    item.skuLoading = false
  }
}

function skuLabel(sku: ProductSkuOption): string {
  return sku.spec_code || sku.spec_name || `规格 #${sku.id}`
}

function addItem(): void {
  items.value.push(newItem())
}

function removeItem(index: number): void {
  if (items.value.length > 1) {
    items.value.splice(index, 1)
  }
}

function submit(): void {
  if (!supplierId.value) {
    ElMessage.warning('请选择供应商')
    return
  }

  if (mode.value === 'from-plan') {
    if (!plan.value) {
      return
    }
    const invalid = planItems.value.find(
      (item) =>
        !item.qty ||
        Number(item.qty) <= 0 ||
        Number(item.qty) > Number(item.remaining_qty),
    )
    if (invalid) {
      ElMessage.warning('转单数量必须大于 0 且不能超过计划剩余数量')
      return
    }
    emit('submit', {
      plan_id: plan.value.id,
      warehouse_id: warehouseId.value,
      description: description.value.trim() || null,
      attachments: attachmentItems.value.map((item) => item.url),
      items: planItems.value.map((item) => ({
        plan_item_id: item.id,
        qty: String(item.qty),
      })),
    })
    return
  }

  const invalid = items.value.find(
    (item) => !item.product_id || !item.qty || Number(item.qty) <= 0,
  )
  if (invalid) {
    ElMessage.warning('请完善商品、规格和采购数量')
    return
  }

  emit('submit', {
    supplier_id: supplierId.value,
    warehouse_id: warehouseId.value,
    description: description.value.trim() || null,
    attachments: attachmentItems.value.map((item) => item.url),
    items: items.value.map((item) => ({
      product_id: item.product_id as number,
      sku_id: item.sku_id || null,
      qty: String(item.qty),
      purchase_price: item.purchase_price || null,
    })),
  })
}

async function loadEditor(id: number): Promise<PurchaseOrder> {
  return purchaseOrderApi.get(id)
}

defineExpose({
  openCreate,
  openEdit,
  openFromPlan,
  loadEditor,
})
</script>
