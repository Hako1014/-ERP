<template>
  <FormDialog
    v-model:visible="visible"
    :title="editing ? '编辑商品' : '新增商品'"
    :width="960"
    :loading="saving"
    @submit="submit"
  >
    <el-tabs v-model="activeTab">
      <el-tab-pane label="基础信息" name="basic">
        <div class="settings-page__form-grid">
          <el-form-item label="商品名称" required>
            <el-input v-model="form.name" maxlength="128" />
          </el-form-item>
          <el-form-item label="商品编码" required>
            <el-input v-model="form.code" maxlength="64" />
          </el-form-item>
          <el-form-item label="商品货号">
            <el-input v-model="form.art_no" maxlength="64" />
          </el-form-item>
          <el-form-item label="商品条码">
            <el-input v-model="form.barcode" maxlength="64" />
          </el-form-item>
          <el-form-item label="分类">
            <el-select v-model="form.category_id" clearable filterable>
              <el-option
                v-for="item in categoryOptions"
                :key="item.id"
                :label="categoryLabel(item)"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="品牌">
            <el-select v-model="form.brand_id" clearable filterable>
              <el-option
                v-for="item in brandOptions"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="默认供应商">
            <el-select v-model="form.default_supplier_id" clearable filterable>
              <el-option
                v-for="item in supplierOptions"
                :key="item.id"
                :label="`${item.code} · ${item.name}`"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="计量方式">
            <el-radio-group v-model="form.measure_type">
              <el-radio value="SINGLE">单计量</el-radio>
              <el-radio value="MULTI">多计量</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="form.measure_type === 'MULTI'" label="计量单位组">
            <el-select
              v-model="form.unit_group_id"
              clearable
              filterable
              @change="handleUnitGroupChange"
            >
              <el-option
                v-for="item in unitGroupOptions"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="主计量单位">
            <el-select v-model="form.main_unit_id" clearable filterable>
              <el-option
                v-for="item in mainUnitOptions"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item class="is-full-width" label="商品图片">
            <el-input
              v-model="form.image"
              maxlength="255"
              placeholder="填写图片 URL"
            />
          </el-form-item>
        </div>
      </el-tab-pane>

      <el-tab-pane label="价格与库存" name="price">
        <div class="settings-page__form-grid">
          <el-form-item label="网上售价">
            <el-input v-model="form.online_price" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="吊牌价">
            <el-input v-model="form.tag_price" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="最低价">
            <el-input v-model="form.min_price" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="成本价">
            <el-input v-model="form.cost_price" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="最小库存">
            <el-input-number v-model="form.min_stock" :min="0" />
          </el-form-item>
          <el-form-item label="最大库存">
            <el-input-number v-model="form.max_stock" :min="0" />
          </el-form-item>
          <el-form-item label="警戒库存">
            <el-input-number v-model="form.warn_stock" :min="0" />
          </el-form-item>
          <el-form-item label="重量(kg)">
            <el-input v-model="form.weight" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="单件体积">
            <el-input v-model="form.volume" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="税率(%)">
            <el-input v-model="form.tax_rate" inputmode="decimal" />
          </el-form-item>
          <el-form-item label="税收编码">
            <el-input v-model="form.tax_code" maxlength="64" />
          </el-form-item>
          <el-form-item label="商品选项">
            <div class="product-options">
              <el-checkbox v-model="form.is_gift">赠品</el-checkbox>
              <el-checkbox v-model="form.use_float_cost">浮动成本</el-checkbox>
              <el-checkbox v-model="form.is_disabled">禁用商品</el-checkbox>
            </div>
          </el-form-item>
          <el-form-item class="is-full-width" label="商品描述">
            <el-input
              v-model="form.description"
              type="textarea"
              :rows="4"
              maxlength="1000"
              show-word-limit
            />
          </el-form-item>
        </div>
      </el-tab-pane>
    </el-tabs>
  </FormDialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, reactive, ref } from 'vue'

import { partyApi, type PartyRecord } from '@/api/party'
import type { ProductPayload, ProductRecord } from '@/api/product'
import {
  productSettingsApi,
  type ProductBrand,
  type ProductCategory,
  type ProductUnit,
  type ProductUnitGroup,
} from '@/api/productSettings'
import FormDialog from '@/components/FormDialog.vue'

const props = defineProps<{
  saving: boolean
}>()

const emit = defineEmits<{
  submit: [payload: ProductPayload]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const activeTab = ref('basic')
const editing = ref<ProductRecord | null>(null)
const categoryOptions = ref<ProductCategory[]>([])
const brandOptions = ref<ProductBrand[]>([])
const unitOptions = ref<ProductUnit[]>([])
const unitGroupOptions = ref<ProductUnitGroup[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const optionsLoading = ref(false)

const form = reactive({
  name: '',
  code: '',
  art_no: '',
  barcode: '',
  category_id: null as number | null,
  brand_id: null as number | null,
  default_supplier_id: null as number | null,
  measure_type: 'SINGLE',
  unit_group_id: null as number | null,
  main_unit_id: null as number | null,
  online_price: '',
  tag_price: '',
  min_price: '',
  cost_price: '',
  weight: '',
  volume: '',
  min_stock: 0,
  max_stock: 0,
  warn_stock: 0,
  tax_rate: '',
  tax_code: '',
  is_gift: false,
  use_float_cost: false,
  is_disabled: false,
  description: '',
  image: '',
})

const categoryNameMap = computed(
  () => new Map(categoryOptions.value.map((item) => [item.id, item.name])),
)
const mainUnitOptions = computed(() => {
  if (form.measure_type !== 'MULTI' || !form.unit_group_id) {
    return unitOptions.value
  }
  const group = unitGroupOptions.value.find(
    (item) => item.id === form.unit_group_id,
  )
  if (!group) {
    return unitOptions.value
  }
  const allowed = new Set([
    group.main_unit_id,
    ...group.items.map((item) => item.unit_id),
  ])
  return unitOptions.value.filter((item) => allowed.has(item.id))
})

function categoryLabel(category: ProductCategory): string {
  if (!category.parent_id) {
    return category.name
  }
  const parent = categoryNameMap.value.get(category.parent_id)
  return parent ? `${parent} / ${category.name}` : category.name
}

function decimalText(value: unknown): string {
  return value === null || value === undefined ? '' : String(value)
}

async function loadOptions(): Promise<void> {
  if (optionsLoading.value) {
    return
  }
  optionsLoading.value = true
  try {
    const [categories, brands, units, groups, suppliers] = await Promise.all([
      productSettingsApi.listCategories(),
      productSettingsApi.listBrands(),
      productSettingsApi.listUnits(),
      productSettingsApi.listUnitGroups(),
      partyApi.listSuppliers({ is_active: true, page_size: 200 }),
    ])
    categoryOptions.value = categories
    brandOptions.value = brands
    unitOptions.value = units
    unitGroupOptions.value = groups
    supplierOptions.value = suppliers.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品选项加载失败')
  } finally {
    optionsLoading.value = false
  }
}

function resetForm(): void {
  form.name = ''
  form.code = ''
  form.art_no = ''
  form.barcode = ''
  form.category_id = null
  form.brand_id = null
  form.default_supplier_id = null
  form.measure_type = 'SINGLE'
  form.unit_group_id = null
  form.main_unit_id = null
  form.online_price = ''
  form.tag_price = ''
  form.min_price = ''
  form.cost_price = ''
  form.weight = ''
  form.volume = ''
  form.min_stock = 0
  form.max_stock = 0
  form.warn_stock = 0
  form.tax_rate = ''
  form.tax_code = ''
  form.is_gift = false
  form.use_float_cost = false
  form.is_disabled = false
  form.description = ''
  form.image = ''
}

function fillForm(product: ProductRecord): void {
  form.name = product.name
  form.code = product.code
  form.art_no = product.art_no || ''
  form.barcode = product.barcode || ''
  form.category_id = product.category_id || null
  form.brand_id = product.brand_id || null
  form.default_supplier_id = product.default_supplier_id || null
  form.measure_type = product.measure_type || 'SINGLE'
  form.unit_group_id = product.unit_group_id || null
  form.main_unit_id = product.main_unit_id || null
  form.online_price = decimalText(product.online_price)
  form.tag_price = decimalText(product.tag_price)
  form.min_price = decimalText(product.min_price)
  form.cost_price = decimalText(product.cost_price)
  form.weight = decimalText(product.weight)
  form.volume = decimalText(product.volume)
  form.min_stock = product.min_stock || 0
  form.max_stock = product.max_stock || 0
  form.warn_stock = product.warn_stock || 0
  form.tax_rate = decimalText(product.tax_rate)
  form.tax_code = product.tax_code || ''
  form.is_gift = product.is_gift
  form.use_float_cost = product.use_float_cost
  form.is_disabled = product.is_disabled
  form.description = product.description || ''
  form.image = product.image || ''
}

async function open(product?: ProductRecord): Promise<void> {
  editing.value = product || null
  activeTab.value = 'basic'
  if (product) {
    fillForm(product)
  } else {
    resetForm()
  }
  visible.value = true
  await loadOptions()
}

function handleUnitGroupChange(value: number | null): void {
  if (!value) {
    return
  }
  const group = unitGroupOptions.value.find((item) => item.id === value)
  if (group) {
    form.main_unit_id = group.main_unit_id
  }
}

function nullable(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function nullableId(value: unknown): number | null {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function submit(): void {
  if (!form.name.trim() || !form.code.trim()) {
    ElMessage.warning('请填写商品名称和商品编码')
    return
  }
  if (form.measure_type === 'MULTI' && !form.unit_group_id) {
    ElMessage.warning('多计量商品必须选择计量单位组')
    return
  }
  emit('submit', {
    name: form.name.trim(),
    code: form.code.trim(),
    art_no: nullable(form.art_no),
    barcode: nullable(form.barcode),
    category_id: nullableId(form.category_id),
    brand_id: nullableId(form.brand_id),
    default_supplier_id: nullableId(form.default_supplier_id),
    measure_type: form.measure_type,
    unit_group_id: nullableId(form.unit_group_id),
    main_unit_id: nullableId(form.main_unit_id),
    online_price: nullable(form.online_price),
    tag_price: nullable(form.tag_price),
    min_price: nullable(form.min_price),
    cost_price: nullable(form.cost_price),
    weight: nullable(form.weight),
    volume: nullable(form.volume),
    min_stock: form.min_stock,
    max_stock: form.max_stock,
    warn_stock: form.warn_stock,
    tax_rate: nullable(form.tax_rate),
    tax_code: nullable(form.tax_code),
    is_gift: form.is_gift,
    use_float_cost: form.use_float_cost,
    is_disabled: form.is_disabled,
    description: nullable(form.description),
    image: nullable(form.image),
  })
}

defineExpose({ open })
</script>

<style scoped>
.product-options {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}
</style>
