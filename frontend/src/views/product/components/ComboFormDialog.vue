<template>
  <FormDialog
    v-model:visible="visible"
    :title="editing ? '编辑组合商品' : '新增组合商品'"
    :width="980"
    :loading="saving"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="组合编码" required>
        <el-input v-model="form.combo_no" maxlength="64" />
      </el-form-item>
      <el-form-item label="组合名称" required>
        <el-input v-model="form.name" maxlength="128" />
      </el-form-item>
      <el-form-item label="主商品" required>
        <el-select
          v-model="form.main_product_id"
          filterable
          placeholder="选择主商品"
          @change="handleMainProductChange"
        >
          <el-option
            v-for="item in productOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="主商品规格">
        <el-select
          v-model="form.main_sku_id"
          clearable
          filterable
          :loading="mainSkuLoading"
          placeholder="不选表示商品级"
        >
          <el-option
            v-for="item in mainSkuOptions"
            :key="item.id"
            :label="item.spec_code || item.spec_name || `SKU #${item.id}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="启用状态">
        <el-switch v-model="form.is_enabled" />
      </el-form-item>
      <el-form-item class="is-full-width" label="备注">
        <el-input
          v-model="form.remark"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
      <el-form-item class="is-full-width" label="配件清单" required>
        <div class="combo-items">
          <div
            v-for="(item, index) in form.items"
            :key="item.key"
            class="combo-item"
          >
            <el-select
              v-model="item.product_id"
              filterable
              placeholder="配件商品"
              @change="handleItemProductChange(index)"
            >
              <el-option
                v-for="product in productOptions"
                :key="product.id"
                :label="`${product.code} · ${product.name}`"
                :value="product.id"
              />
            </el-select>
            <el-select
              v-model="item.sku_id"
              clearable
              filterable
              :loading="item.skuLoading"
              placeholder="配件规格"
            >
              <el-option
                v-for="sku in item.skuOptions"
                :key="sku.id"
                :label="sku.spec_code || sku.spec_name || `SKU #${sku.id}`"
                :value="sku.id"
              />
            </el-select>
            <el-input
              v-model="item.qty"
              inputmode="decimal"
              placeholder="数量"
            />
            <el-input-number
              v-model="item.sort_order"
              :min="0"
              controls-position="right"
            />
            <el-button
              class="combo-item__remove"
              link
              type="danger"
              :disabled="form.items.length <= 1"
              @click="removeItem(index)"
            >
              删除
            </el-button>
          </div>
          <el-button class="product-add-item" @click="appendItem">
            添加配件
          </el-button>
        </div>
      </el-form-item>
    </div>
  </FormDialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'

import {
  productApi,
  type ComboPayload,
  type ComboRecord,
  type ProductRecord,
  type ProductSku,
} from '@/api/product'
import FormDialog from '@/components/FormDialog.vue'

interface ComboItemForm {
  key: number
  product_id: number | null
  sku_id: number | null
  qty: string
  sort_order: number
  skuLoading: boolean
  skuOptions: ProductSku[]
}

defineProps<{
  saving: boolean
}>()

const emit = defineEmits<{
  submit: [payload: ComboPayload]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const editing = ref<ComboRecord | null>(null)
const productOptions = ref<ProductRecord[]>([])
const mainSkuOptions = ref<ProductSku[]>([])
const mainSkuLoading = ref(false)
let itemKey = 0

function emptyItem(): ComboItemForm {
  itemKey += 1
  return {
    key: itemKey,
    product_id: null,
    sku_id: null,
    qty: '1',
    sort_order: itemKey - 1,
    skuLoading: false,
    skuOptions: [],
  }
}

const form = reactive({
  combo_no: '',
  name: '',
  main_product_id: null as number | null,
  main_sku_id: null as number | null,
  is_enabled: true,
  remark: '',
  items: [emptyItem()],
})

async function loadProductOptions(): Promise<void> {
  const result = await productApi.listProducts({ page_size: 200 })
  productOptions.value = result.items
}

async function ensureProducts(ids: number[]): Promise<void> {
  const current = new Set(productOptions.value.map((item) => item.id))
  const missing = Array.from(new Set(ids)).filter((id) => !current.has(id))
  if (!missing.length) {
    return
  }
  const products = await Promise.all(
    missing.map((id) => productApi.getProduct(id)),
  )
  productOptions.value = [...productOptions.value, ...products]
}

async function fetchSkuOptions(productId: number | null): Promise<ProductSku[]> {
  if (!productId) {
    return []
  }
  try {
    return await productApi.listSkus(productId)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格加载失败')
    return []
  }
}

async function handleMainProductChange(): Promise<void> {
  form.main_sku_id = null
  mainSkuLoading.value = true
  mainSkuOptions.value = await fetchSkuOptions(form.main_product_id)
  mainSkuLoading.value = false
}

async function handleItemProductChange(index: number): Promise<void> {
  const item = form.items[index]
  item.sku_id = null
  item.skuLoading = true
  item.skuOptions = await fetchSkuOptions(item.product_id)
  item.skuLoading = false
}

function appendItem(): void {
  form.items.push(emptyItem())
}

function removeItem(index: number): void {
  if (form.items.length > 1) {
    form.items.splice(index, 1)
  }
}

function resetForm(): void {
  form.combo_no = ''
  form.name = ''
  form.main_product_id = null
  form.main_sku_id = null
  form.is_enabled = true
  form.remark = ''
  form.items = [emptyItem()]
  mainSkuOptions.value = []
}

async function open(combo?: ComboRecord): Promise<void> {
  editing.value = combo || null
  visible.value = true
  await loadProductOptions()
  if (!combo) {
    resetForm()
    return
  }

  const detail = await productApi.getCombo(combo.id)
  await ensureProducts([
    detail.main_product_id,
    ...(detail.items || []).map((item) => item.product_id),
  ])
  form.combo_no = detail.combo_no
  form.name = detail.name
  form.main_product_id = detail.main_product_id
  form.main_sku_id = detail.main_sku_id || null
  form.is_enabled = detail.is_enabled
  form.remark = detail.remark || ''
  form.items = (detail.items || []).map((item, index) => {
    itemKey += 1
    return {
      key: itemKey,
      product_id: item.product_id,
      sku_id: item.sku_id || null,
      qty: String(item.qty),
      sort_order: item.sort_order ?? index,
      skuLoading: false,
      skuOptions: [],
    }
  })
  if (!form.items.length) {
    form.items = [emptyItem()]
  }
  await handleMainProductChange()
  await Promise.all(
    form.items.map((item, index) => handleItemProductChange(index)),
  )
  if (detail.main_sku_id) {
    form.main_sku_id = detail.main_sku_id
  }
  detail.items?.forEach((item, index) => {
    if (item.sku_id) {
      form.items[index].sku_id = item.sku_id
    }
  })
}

function submit(): void {
  if (
    !form.combo_no.trim() ||
    !form.name.trim() ||
    !form.main_product_id
  ) {
    ElMessage.warning('请填写组合编码、组合名称并选择主商品')
    return
  }
  const items = form.items.map((item, index) => ({
    product_id: Number(item.product_id),
    sku_id: item.sku_id,
    qty: item.qty,
    sort_order: index,
  }))
  if (
    items.some(
      (item) => !item.product_id || !item.qty || Number(item.qty) <= 0,
    )
  ) {
    ElMessage.warning('请完整填写配件商品和数量')
    return
  }
  emit('submit', {
    combo_no: form.combo_no.trim(),
    name: form.name.trim(),
    main_product_id: form.main_product_id,
    main_sku_id: form.main_sku_id,
    is_enabled: form.is_enabled,
    remark: form.remark.trim() || null,
    items,
  })
}

defineExpose({ open })
</script>
