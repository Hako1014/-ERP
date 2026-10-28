<template>
  <FormDialog
    v-model:visible="visible"
    :title="editingId ? '编辑采购退货单' : '新增采购退货单'"
    width="min(1060px, 96vw)"
    :loading="loading"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="供应商" required>
        <el-select
          v-model="supplierId"
          filterable
          placeholder="请选择供应商"
          @change="handleSupplierChange"
        >
          <el-option
            v-for="item in supplierOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="采购订单">
        <el-select
          v-model="orderId"
          clearable
          filterable
          :loading="orderLoading"
          placeholder="建议关联已审核采购订单"
          @change="loadOrderItems"
        >
          <el-option
            v-for="item in availableOrders"
            :key="item.id"
            :label="`${item.order_no} · ${item.supplier_name || ''}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="退货仓库" required>
        <el-select v-model="warehouseId" filterable placeholder="请选择仓库">
          <el-option
            v-for="item in warehouseOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}${item.is_virtual ? ' · 虚拟仓' : ''}`"
            :value="item.id"
            :disabled="!item.selectable"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="自动出库">
        <el-switch v-model="autoOut" />
      </el-form-item>
      <el-form-item label="退货原因">
        <el-input v-model="returnReason" maxlength="128" />
      </el-form-item>
      <el-form-item label="退货描述">
        <el-input v-model="description" maxlength="255" />
      </el-form-item>
    </div>

    <p class="purchase-dialog-tip">
      选择采购订单后将加载已到货明细，可退数量以采购订单到货数量为准。
      未关联订单时需手动选择商品规格和退货单价。
    </p>

    <div class="purchase-items">
      <div
        v-for="(item, index) in items"
        :key="item.key"
        class="purchase-item"
      >
        <div class="purchase-item__header">
          <span class="purchase-item__title">退货明细 {{ index + 1 }}</span>
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
            :disabled="Boolean(orderId)"
            :remote-method="searchProducts"
            :loading="productLoading"
            placeholder="搜索商品"
            @change="(value: number) => handleProductChange(item, value)"
          >
            <el-option
              v-for="option in productOptions"
              :key="option.id"
              :label="`${option.code} · ${option.name}`"
              :value="option.id"
            />
            <el-option
              v-if="item.product_id && !productOptions.some((option) => option.id === item.product_id)"
              :label="item.product_name || `商品 #${item.product_id}`"
              :value="item.product_id"
            />
          </el-select>
          <el-select
            v-model="item.sku_id"
            clearable
            filterable
            :disabled="Boolean(orderId)"
            :loading="item.skuLoading"
            placeholder="必须选择规格"
          >
            <el-option
              v-for="sku in item.skuOptions"
              :key="sku.id"
              :label="sku.spec_code || sku.spec_name || `规格 #${sku.id}`"
              :value="sku.id"
            />
          </el-select>
          <el-input
            v-model="item.return_qty"
            inputmode="decimal"
            placeholder="退货数量"
          />
          <el-input
            v-model="item.unit_price"
            inputmode="decimal"
            placeholder="退货单价，可自动取价"
          />
        </div>
        <div v-if="item.maxQty" class="purchase-muted">
          订单到货数量：{{ item.maxQty }}
        </div>
      </div>
      <el-button :disabled="Boolean(orderId)" @click="addItem">
        新增退货明细
      </el-button>
    </div>
  </FormDialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  purchaseOrderApi,
  type PurchaseOrder,
  type PurchaseReturn,
  type PurchaseReturnPayload,
  type WarehouseOption,
} from '@/api/purchase'
import {
  productOptionsApi,
  type ProductOption,
  type ProductSkuOption,
} from '@/api/productOptions'
import type { PartyRecord } from '@/api/party'
import FormDialog from '@/components/FormDialog.vue'

interface ReturnItemDraft {
  key: string
  product_id?: number
  product_name?: string | null
  sku_id?: number | null
  skuOptions: ProductSkuOption[]
  skuLoading: boolean
  unit_id?: number | null
  return_qty: string
  unit_price: string
  maxQty?: string | number | null
}

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    supplierOptions?: PartyRecord[]
    orders?: PurchaseOrder[]
    warehouseOptions?: WarehouseOption[]
    loading?: boolean
  }>(),
  {
    supplierOptions: () => [],
    orders: () => [],
    warehouseOptions: () => [],
    loading: false,
  },
)

const emit = defineEmits<{
  submit: [payload: PurchaseReturnPayload]
}>()

const editingId = ref<number | null>(null)
const supplierId = ref<number | null>(null)
const orderId = ref<number | null>(null)
const warehouseId = ref<number | null>(null)
const autoOut = ref(false)
const returnReason = ref('')
const description = ref('')
const orderLoading = ref(false)
const productLoading = ref(false)
const productOptions = ref<ProductOption[]>([])
const items = ref<ReturnItemDraft[]>([])
let sequence = 0

const availableOrders = computed(() =>
  supplierId.value
    ? props.orders.filter((item) => item.supplier_id === supplierId.value)
    : props.orders,
)

function newItem(): ReturnItemDraft {
  sequence += 1
  return {
    key: `return-item-${sequence}`,
    product_id: undefined,
    sku_id: null,
    skuOptions: [],
    skuLoading: false,
    unit_id: null,
    return_qty: '',
    unit_price: '',
  }
}

function reset(): void {
  editingId.value = null
  supplierId.value = null
  orderId.value = null
  warehouseId.value = null
  autoOut.value = false
  returnReason.value = ''
  description.value = ''
  items.value = [newItem()]
}

function openCreate(supplier?: number, order?: number): void {
  reset()
  supplierId.value = supplier || null
  orderId.value = order || null
  visible.value = true
  void searchProducts('')
  if (orderId.value) {
    void loadOrderItems(orderId.value)
  }
}

function openEdit(record: PurchaseReturn): void {
  reset()
  editingId.value = record.id
  supplierId.value = record.supplier_id
  orderId.value = record.order_id || null
  warehouseId.value = record.warehouse_id || null
  autoOut.value = record.auto_out
  returnReason.value = record.return_reason || ''
  description.value = record.description || ''
  items.value = (record.items || []).map((item) => ({
    key: `return-item-${item.id}`,
    product_id: item.product_id,
    product_name: item.product_name,
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
    unit_id: item.unit_id || null,
    return_qty: String(item.return_qty),
    unit_price:
      item.unit_price === null || item.unit_price === undefined
        ? ''
        : String(item.unit_price),
  }))
  if (!items.value.length) {
    items.value = [newItem()]
  }
  visible.value = true
  void searchProducts('')
}

function handleSupplierChange(): void {
  if (
    orderId.value &&
    !availableOrders.value.some((item) => item.id === orderId.value)
  ) {
    orderId.value = null
  }
  if (
    warehouseId.value &&
    !props.warehouseOptions.some((item) => item.id === warehouseId.value)
  ) {
    warehouseId.value = null
  }
}

async function loadOrderItems(value?: number): Promise<void> {
  if (!value) {
    items.value = [newItem()]
    return
  }
  orderLoading.value = true
  try {
    const order = await purchaseOrderApi.get(value)
    supplierId.value = order.supplier_id
    if (order.warehouse_id) {
      warehouseId.value = order.warehouse_id
    }
    items.value = (order.items || []).map((item) => ({
      key: `order-return-${item.id}`,
      product_id: item.product_id,
      product_name: item.product_name,
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
      unit_id: item.unit_id || null,
      return_qty: Number(item.arrived_qty) > 0 ? String(item.arrived_qty) : '',
      unit_price:
        item.purchase_price === null || item.purchase_price === undefined
          ? ''
          : String(item.purchase_price),
      maxQty: item.arrived_qty,
    }))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购订单加载失败')
  } finally {
    orderLoading.value = false
  }
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
  item: ReturnItemDraft,
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
  if (!warehouseId.value) {
    ElMessage.warning('请选择退货仓库')
    return
  }
  const invalid = items.value.find(
    (item) =>
      !item.product_id ||
      !item.sku_id ||
      !item.return_qty ||
      Number(item.return_qty) <= 0,
  )
  if (invalid) {
    ElMessage.warning('请完善商品规格和退货数量')
    return
  }
  emit('submit', {
    supplier_id: supplierId.value,
    order_id: orderId.value,
    warehouse_id: warehouseId.value,
    auto_out: autoOut.value,
    return_reason: returnReason.value.trim() || null,
    description: description.value.trim() || null,
    items: items.value.map((item) => ({
      product_id: item.product_id as number,
      sku_id: item.sku_id as number,
      unit_id: item.unit_id || null,
      unit_price: item.unit_price || null,
      return_qty: String(item.return_qty),
    })),
  })
}

defineExpose({ openCreate, openEdit })
</script>
