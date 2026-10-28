<template>
  <div class="settings-page product-page">
    <SettingsPageHeader
      title="商品资料"
      description="维护商品基础资料、规格、上下架状态与导入导出。"
    />

    <section class="settings-page__panel">
      <div class="product-toolbar">
        <div class="product-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="商品编码、货号或名称"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="categoryId"
            class="product-filter"
            clearable
            filterable
            placeholder="全部分类"
            @change="search"
          >
            <el-option
              v-for="item in categoryOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="brandId"
            class="product-filter"
            clearable
            filterable
            placeholder="全部品牌"
            @change="search"
          >
            <el-option
              v-for="item in brandOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="listedFilter"
            class="product-filter"
            placeholder="全部上架状态"
            @change="search"
          >
            <el-option label="全部上架状态" value="" />
            <el-option label="已上架" value="true" />
            <el-option label="未上架" value="false" />
          </el-select>
          <el-select
            v-model="disabledFilter"
            class="product-filter"
            placeholder="全部商品状态"
            @change="search"
          >
            <el-option label="全部商品状态" value="" />
            <el-option label="正常" value="false" />
            <el-option label="已禁用" value="true" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <div class="product-toolbar__actions">
          <el-button @click="excelVisible = true">导入/导出</el-button>
          <el-button v-if="canManage" type="primary" @click="openCreate">
            新增商品
          </el-button>
        </div>
      </div>

      <div v-if="canManage && selectedRows.length" class="product-batch">
        <span>已选择 {{ selectedRows.length }} 个商品</span>
        <el-button link type="success" @click="batchListing(true)">
          批量上架
        </el-button>
        <el-button link type="warning" @click="batchListing(false)">
          批量下架
        </el-button>
        <el-button link type="danger" @click="batchRemove">批量删除</el-button>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        empty-description="暂无商品"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @selection-change="selectedRows = $event"
        @empty-action="resetAndLoad"
      >
        <template #image="{ row }">
          <el-image
            class="product-thumb"
            :src="row.image || ''"
            fit="cover"
            preview-teleported
            :preview-src-list="row.image ? [row.image] : []"
          >
            <template #error>
              <div class="product-thumb__placeholder">
                <el-icon><Picture /></el-icon>
              </div>
            </template>
          </el-image>
        </template>
        <template #price="{ row }">
          <MoneyText :value="row.online_price" />
        </template>
        <template #listed="{ row }">
          <StatusTag
            :status="row.listed ? 'ACTIVE' : 'DISABLED'"
            :label="row.listed ? '已上架' : '未上架'"
          />
        </template>
        <template #listedAt="{ row }">
          {{ formatDateTime(row.listed_at) }}
        </template>
        <template #disabled="{ row }">
          <StatusTag
            :status="row.is_disabled ? 'DISABLED' : 'ACTIVE'"
            :label="row.is_disabled ? '已禁用' : '正常'"
          />
        </template>
        <template #supplier="{ row }">
          {{ supplierName(row.default_supplier_id) }}
        </template>
        <template #actions="{ row }">
          <div class="settings-page__actions settings-page__actions--wrap">
            <el-button link type="primary" @click="openDetail(row)">
              详情
            </el-button>
            <template v-if="canManage">
              <el-button link type="primary" @click="openEdit(row)">
                编辑
              </el-button>
              <el-button
                link
                type="primary"
                @click="toggleListing(row)"
              >
                {{ row.listed ? '下架' : '上架' }}
              </el-button>
              <el-button
                link
                :type="row.is_disabled ? 'success' : 'warning'"
                @click="toggleDisabled(row)"
              >
                {{ row.is_disabled ? '启用' : '禁用' }}
              </el-button>
              <el-button link type="danger" @click="remove(row)">
                删除
              </el-button>
            </template>
            <span v-else class="settings-page__muted">只读</span>
          </div>
        </template>
      </DataTable>
    </section>

    <ProductFormDialog
      ref="productFormRef"
      v-model:visible="formVisible"
      :saving="saving"
      @submit="submitProduct"
    />

    <ProductDetailDrawer
      v-model:visible="detailVisible"
      :product="detailProduct"
      :loading="detailLoading"
      :can-manage="canManage"
      @create-sku="openCreateSku"
      @edit-sku="openEditSku"
      @delete-sku="removeSku"
    />

    <SkuFormDialog
      v-model:visible="skuFormVisible"
      :saving="skuSaving"
      @submit="submitSku"
    />

    <ExcelImportExportDialog
      v-model:visible="excelVisible"
      title="商品导入导出"
      :template-url="PRODUCT_TEMPLATE_URL"
      :import-url="canManage ? PRODUCT_IMPORT_URL : undefined"
      :export-url="PRODUCT_EXPORT_URL"
      :export-params="exportParams"
      filename="商品资料.xlsx"
      @imported="load"
    />
  </div>
</template>

<script setup lang="ts">
import { Picture } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { partyApi } from '@/api/party'
import {
  PRODUCT_EXPORT_URL,
  PRODUCT_IMPORT_URL,
  PRODUCT_TEMPLATE_URL,
  productApi,
  type ProductPayload,
  type ProductRecord,
  type ProductSku,
  type SkuPayload,
} from '@/api/product'
import {
  productSettingsApi,
  type ProductBrand,
  type ProductCategory,
} from '@/api/productSettings'
import DataTable from '@/components/DataTable.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'
import type { DataTableColumn } from '@/components/types'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import ProductDetailDrawer from '@/views/product/components/ProductDetailDrawer.vue'
import ProductFormDialog from '@/views/product/components/ProductFormDialog.vue'
import SkuFormDialog from '@/views/product/components/SkuFormDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import '@/views/settings/settings.css'
import '@/views/product/product.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('product:manage'))

const columns: DataTableColumn<ProductRecord>[] = [
  { type: 'selection', label: '选择', width: 46 },
  { label: '图片', slot: 'image', width: 78, align: 'center' },
  { prop: 'code', label: '商品编码', minWidth: 130 },
  { prop: 'art_no', label: '商品货号', minWidth: 120 },
  { prop: 'name', label: '商品名称', minWidth: 180 },
  { prop: 'category_name', label: '分类', minWidth: 110 },
  { prop: 'brand_name', label: '品牌', minWidth: 100 },
  { label: '网上售价', slot: 'price', width: 120, align: 'right' },
  { label: '上架状态', slot: 'listed', width: 100, align: 'center' },
  { label: '上架时间', slot: 'listedAt', minWidth: 160 },
  { label: '是否禁用', slot: 'disabled', width: 100, align: 'center' },
  { label: '供应商', slot: 'supplier', minWidth: 140 },
  { label: '操作', slot: 'actions', width: 300, fixed: 'right' },
]

const rows = ref<ProductRecord[]>([])
const selectedRows = ref<ProductRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const detailLoading = ref(false)
const skuSaving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const categoryId = ref<number | null>(null)
const brandId = ref<number | null>(null)
const listedFilter = ref('')
const disabledFilter = ref('')
const categoryOptions = ref<ProductCategory[]>([])
const brandOptions = ref<ProductBrand[]>([])
const supplierNames = ref<Record<number, string>>({})
const formVisible = ref(false)
const editingProductId = ref<number | null>(null)
const detailVisible = ref(false)
const skuFormVisible = ref(false)
const excelVisible = ref(false)
const detailProduct = ref<ProductRecord | null>(null)
const productFormRef = ref<InstanceType<typeof ProductFormDialog>>()
const skuFormRef = ref<InstanceType<typeof SkuFormDialog>>()

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    categoryId.value !== null ||
    brandId.value !== null ||
    Boolean(listedFilter.value) ||
    Boolean(disabledFilter.value),
)
const exportParams = computed(() => ({
  keyword: keyword.value || undefined,
  category_id: categoryId.value ?? undefined,
  brand_id: brandId.value ?? undefined,
  listed: listedFilter.value || undefined,
  is_disabled: disabledFilter.value || undefined,
}))

function supplierName(supplierId?: number | null): string {
  if (!supplierId) {
    return '--'
  }
  return supplierNames.value[supplierId] || `供应商 #${supplierId}`
}

function boolParam(value: string): boolean | undefined {
  if (value === 'true') {
    return true
  }
  if (value === 'false') {
    return false
  }
  return undefined
}

async function loadOptions(): Promise<void> {
  try {
    const [categories, brands, suppliers] = await Promise.all([
      productSettingsApi.listCategories(),
      productSettingsApi.listBrands(),
      partyApi.listSuppliers({ page_size: 200 }),
    ])
    categoryOptions.value = categories
    brandOptions.value = brands
    supplierNames.value = Object.fromEntries(
      suppliers.items.map((item) => [item.id, `${item.code} · ${item.name}`]),
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '筛选选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await productApi.listProducts({
      keyword: keyword.value || undefined,
      category_id: categoryId.value || undefined,
      brand_id: brandId.value || undefined,
      listed: boolParam(listedFilter.value),
      is_disabled: boolParam(disabledFilter.value),
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
    selectedRows.value = []
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品加载失败')
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void load()
}

function resetAndLoad(): void {
  keyword.value = ''
  categoryId.value = null
  brandId.value = null
  listedFilter.value = ''
  disabledFilter.value = ''
  search()
}

function openCreate(): void {
  editingProductId.value = null
  productFormRef.value?.open()
}

function openEdit(row: ProductRecord): void {
  editingProductId.value = row.id
  productFormRef.value?.open(row)
}

async function submitProduct(payload: ProductPayload): Promise<void> {
  saving.value = true
  try {
    if (editingProductId.value) {
      await productApi.updateProduct(editingProductId.value, payload)
      ElMessage.success('商品已更新')
    } else {
      await productApi.createProduct(payload)
      ElMessage.success('商品已创建')
    }
    formVisible.value = false
    editingProductId.value = null
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(row: ProductRecord): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detailProduct.value = await productApi.getProduct(row.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (detailProduct.value) {
    detailProduct.value = await productApi.getProduct(detailProduct.value.id)
  }
}

async function setListing(ids: number[], listed: boolean): Promise<void> {
  try {
    await productApi.setProductListing(ids, listed)
    ElMessage.success(listed ? '商品已上架' : '商品已下架')
    await load()
    if (detailProduct.value && ids.includes(detailProduct.value.id)) {
      await refreshDetail()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '上下架操作失败')
  }
}

async function toggleListing(row: ProductRecord): Promise<void> {
  await setListing([row.id], !row.listed)
}

async function batchListing(listed: boolean): Promise<void> {
  await setListing(
    selectedRows.value.map((item) => item.id),
    listed,
  )
}

async function toggleDisabled(row: ProductRecord): Promise<void> {
  const action = row.is_disabled ? '启用' : '禁用'
  try {
    await ElMessageBox.confirm(
      `确认${action}商品「${row.name}」？`,
      `${action}商品`,
      { type: row.is_disabled ? 'info' : 'warning' },
    )
    await productApi.setProductDisabled(row.id, !row.is_disabled)
    ElMessage.success(`商品已${action}`)
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '操作失败')
    }
  }
}

async function remove(row: ProductRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除商品「${row.name}」？`,
      '删除商品',
      { type: 'warning' },
    )
    await productApi.deleteProduct(row.id)
    ElMessage.success('商品已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

async function batchRemove(): Promise<void> {
  if (!selectedRows.value.length) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedRows.value.length} 个商品？`,
      '批量删除商品',
      { type: 'warning' },
    )
    await productApi.batchDeleteProducts(
      selectedRows.value.map((item) => item.id),
    )
    ElMessage.success('商品已批量删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '批量删除失败')
    }
  }
}

function openCreateSku(): void {
  skuFormRef.value?.open()
}

function openEditSku(sku: ProductSku): void {
  skuFormRef.value?.open(sku)
}

async function submitSku(payload: SkuPayload, skuId?: number): Promise<void> {
  if (!detailProduct.value) {
    return
  }
  skuSaving.value = true
  try {
    if (skuId) {
      await productApi.updateSku(skuId, payload)
      ElMessage.success('规格已更新')
    } else {
      await productApi.createSku(detailProduct.value.id, payload)
      ElMessage.success('规格已创建')
    }
    skuFormVisible.value = false
    await refreshDetail()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格保存失败')
  } finally {
    skuSaving.value = false
  }
}

async function removeSku(sku: ProductSku): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除规格「${sku.spec_code}」？`,
      '删除商品规格',
      { type: 'warning' },
    )
    await productApi.deleteSku(sku.id)
    ElMessage.success('规格已删除')
    await refreshDetail()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '规格删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>
