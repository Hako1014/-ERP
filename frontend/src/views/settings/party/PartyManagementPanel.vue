<template>
  <div class="settings-page">
    <SettingsPageHeader :title="title" :description="description" />

    <section class="settings-page__panel">
      <div class="settings-page__toolbar">
        <div class="settings-page__toolbar-actions">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            :placeholder="`${entityLabel}编码或名称`"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select v-model="activeFilter" class="status-filter" @change="search">
            <el-option label="全部状态" value="" />
            <el-option label="启用" value="true" />
            <el-option label="停用" value="false" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          {{ `新增${entityLabel}` }}
        </el-button>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        :empty-description="`暂无${entityLabel}`"
        empty-action-text="重置筛选"
        :show-empty-action="canManage"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #status="{ row }">
          <StatusTag
            :status="row.is_active ? 'ACTIVE' : 'DISABLED'"
            :label="row.is_active ? '启用' : '停用'"
          />
        </template>
        <template #actions="{ row }">
          <div v-if="canManage" class="settings-page__actions settings-page__actions--wrap">
            <el-button link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button link type="primary" @click="openAccounts(row)">
              账户
            </el-button>
            <el-button
              v-if="kind === 'supplier'"
              link
              type="primary"
              @click="openPrices(row)"
            >
              价格
            </el-button>
            <el-button
              link
              :type="row.is_active ? 'warning' : 'success'"
              @click="toggleActive(row)"
            >
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" @click="remove(row)">
              删除
            </el-button>
          </div>
          <span v-else class="settings-page__muted">只读</span>
        </template>
      </DataTable>
    </section>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? `编辑${entityLabel}` : `新增${entityLabel}`"
      :width="760"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item :label="`${entityLabel}编码`" required>
          <el-input v-model="form.code" maxlength="64" />
        </el-form-item>
        <el-form-item :label="`${entityLabel}名称`" required>
          <el-input v-model="form.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" maxlength="64" />
        </el-form-item>
        <el-form-item label="手机号码">
          <el-input v-model="form.phone" maxlength="32" />
        </el-form-item>
        <el-form-item label="传真">
          <el-input v-model="form.fax" maxlength="32" />
        </el-form-item>
        <el-form-item label="联系邮箱">
          <el-input v-model="form.email" maxlength="64" />
        </el-form-item>
        <el-form-item label="结算方式">
          <el-input v-model="form.settlement_type" maxlength="32" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item class="is-full-width" label="联系地址">
          <el-input v-model="form.address" maxlength="255" />
        </el-form-item>
        <el-form-item class="is-full-width" label="备注">
          <el-input
            v-model="form.remark"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
      </div>
    </FormDialog>

    <el-drawer
      v-model="accountDrawerVisible"
      :title="`${accountTarget?.name || entityLabel} · 账户信息`"
      size="min(760px, 94vw)"
      destroy-on-close
    >
      <div class="settings-page__toolbar">
        <h2>银行账户</h2>
        <el-button v-if="canManage" type="primary" @click="openCreateAccount">
          新增账户
        </el-button>
      </div>
      <el-table v-loading="accountLoading" :data="accounts" row-key="id">
        <el-table-column prop="bank" label="开户行" min-width="140" />
        <el-table-column prop="branch" label="支行" min-width="140" />
        <el-table-column prop="account_no" label="账号" min-width="150" />
        <el-table-column label="余额" width="130" align="right">
          <template #default="{ row }">
            <MoneyText :value="row.balance" />
          </template>
        </el-table-column>
        <el-table-column label="默认" width="80" align="center">
          <template #default="{ row }">
            <StatusTag
              v-if="row.is_default"
              status="ACTIVE"
              label="默认"
              tone="success"
            />
            <span v-else>--</span>
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManage"
          label="操作"
          width="210"
          fixed="right"
        >
          <template #default="{ row }">
            <div class="settings-page__actions">
              <el-button link type="primary" @click="openEditAccount(row)">
                编辑
              </el-button>
              <el-button
                v-if="!row.is_default"
                link
                type="primary"
                @click="setDefaultAccount(row)"
              >
                设默认
              </el-button>
              <el-button link type="danger" @click="removeAccount(row)">
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            description="暂无账户信息"
            action-text="新增账户"
            :show-action="canManage"
            @action="openCreateAccount"
          />
        </template>
      </el-table>
    </el-drawer>

    <FormDialog
      v-model:visible="accountDialogVisible"
      :title="editingAccount ? '编辑账户' : '新增账户'"
      :width="640"
      :loading="accountSaving"
      @submit="submitAccount"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="开户行">
          <el-input v-model="accountForm.bank" maxlength="64" />
        </el-form-item>
        <el-form-item label="支行">
          <el-input v-model="accountForm.branch" maxlength="64" />
        </el-form-item>
        <el-form-item label="银行号">
          <el-input v-model="accountForm.bank_no" maxlength="64" />
        </el-form-item>
        <el-form-item label="银行账号">
          <el-input v-model="accountForm.account_no" maxlength="64" />
        </el-form-item>
        <el-form-item class="is-full-width" label="开户行所在地">
          <el-input v-model="accountForm.address" maxlength="255" />
        </el-form-item>
        <el-form-item label="默认账户">
          <el-switch v-model="accountForm.is_default" />
        </el-form-item>
      </div>
    </FormDialog>

    <el-drawer
      v-if="kind === 'supplier'"
      v-model="priceDrawerVisible"
      :title="`${priceTarget?.name || '供应商'} · 商品价格`"
      size="min(900px, 96vw)"
      destroy-on-close
    >
      <div class="settings-page__toolbar">
        <h2>供应商商品价格</h2>
        <el-button v-if="canManage" type="primary" @click="openCreatePrice">
          新增价格
        </el-button>
      </div>
      <el-table v-loading="priceLoading" :data="prices" row-key="id">
        <el-table-column prop="product_code" label="商品编码" min-width="130" />
        <el-table-column prop="product_name" label="商品名称" min-width="180" />
        <el-table-column prop="sku_spec_code" label="规格" min-width="140" />
        <el-table-column label="定价" width="130" align="right">
          <template #default="{ row }">
            <MoneyText :value="row.price" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <StatusTag
              :status="row.status"
              :label="row.status === 'ACTIVE' ? '启用' : '停用'"
            />
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManage"
          label="操作"
          width="210"
          fixed="right"
        >
          <template #default="{ row }">
            <div class="settings-page__actions">
              <el-button link type="primary" @click="openEditPrice(row)">
                编辑
              </el-button>
              <el-button
                link
                :type="row.status === 'ACTIVE' ? 'warning' : 'success'"
                @click="togglePriceStatus(row)"
              >
                {{ row.status === 'ACTIVE' ? '停用' : '启用' }}
              </el-button>
              <el-button link type="danger" @click="removePrice(row)">
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            description="暂无供应商价格"
            action-text="新增价格"
            :show-action="canManage"
            @action="openCreatePrice"
          />
        </template>
      </el-table>
    </el-drawer>

    <FormDialog
      v-if="kind === 'supplier'"
      v-model:visible="priceDialogVisible"
      :title="editingPrice ? '编辑供应商价格' : '新增供应商价格'"
      :width="640"
      :loading="priceSaving"
      @submit="submitPrice"
    >
      <el-form-item label="商品" required>
        <el-select
          v-model="priceForm.product_id"
          filterable
          remote
          :remote-method="searchProducts"
          :loading="productLoading"
          placeholder="输入商品编码或名称搜索"
          @change="handleProductChange"
        >
          <el-option
            v-for="item in productOptions"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="规格">
        <el-select
          v-model="priceForm.sku_id"
          clearable
          filterable
          :loading="skuLoading"
          placeholder="不选表示商品级价格"
        >
          <el-option
            v-for="item in skuOptions"
            :key="item.id"
            :label="item.spec_code || item.spec_name || `SKU #${item.id}`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="定价" required>
        <el-input
          v-model="priceForm.price"
          inputmode="decimal"
          placeholder="例如 12.50"
        />
      </el-form-item>
      <el-form-item label="状态">
        <el-radio-group v-model="priceForm.status">
          <el-radio value="ACTIVE">启用</el-radio>
          <el-radio value="DISABLED">停用</el-radio>
        </el-radio-group>
      </el-form-item>
    </FormDialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  partyApi,
  type PartyAccount,
  type PartyAccountPayload,
  type PartyPayload,
  type PartyRecord,
  type SupplierPrice,
} from '@/api/party'
import {
  productOptionsApi,
  type ProductOption,
  type ProductSkuOption,
} from '@/api/productOptions'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'

const props = withDefaults(
  defineProps<{
    kind: 'supplier' | 'customer'
    canManage: boolean
  }>(),
  {},
)

const entityLabel = computed(() =>
  props.kind === 'supplier' ? '供应商' : '客户',
)
const title = computed(() =>
  props.kind === 'supplier' ? '供应商管理' : '客户管理',
)
const description = computed(() =>
  props.kind === 'supplier'
    ? '维护供应商主体、结算账户和商品采购定价。'
    : '维护客户主体与收付款银行账户。',
)
const columns: DataTableColumn<PartyRecord>[] = [
  { prop: 'code', label: '编码', minWidth: 120 },
  { prop: 'name', label: '名称', minWidth: 150 },
  { prop: 'contact', label: '联系人', minWidth: 100 },
  { prop: 'phone', label: '手机', minWidth: 130 },
  { prop: 'settlement_type', label: '结算方式', minWidth: 110 },
  { prop: 'email', label: '邮箱', minWidth: 150 },
  { label: '状态', slot: 'status', width: 90, align: 'center' },
  { label: '操作', slot: 'actions', width: 320, fixed: 'right' },
]

const rows = ref<PartyRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const activeFilter = ref('')
const dialogVisible = ref(false)
const editing = ref<PartyRecord | null>(null)
const form = reactive<PartyPayload>({
  code: '',
  name: '',
  contact: '',
  phone: '',
  fax: '',
  address: '',
  settlement_type: '',
  email: '',
  remark: '',
  is_active: true,
})

const accountDrawerVisible = ref(false)
const accountDialogVisible = ref(false)
const accountLoading = ref(false)
const accountSaving = ref(false)
const accountTarget = ref<PartyRecord | null>(null)
const editingAccount = ref<PartyAccount | null>(null)
const accounts = ref<PartyAccount[]>([])
const accountForm = reactive<PartyAccountPayload>({
  bank: '',
  branch: '',
  bank_no: '',
  account_no: '',
  address: '',
  is_default: false,
})

const priceDrawerVisible = ref(false)
const priceDialogVisible = ref(false)
const priceLoading = ref(false)
const priceSaving = ref(false)
const productLoading = ref(false)
const skuLoading = ref(false)
const priceTarget = ref<PartyRecord | null>(null)
const editingPrice = ref<SupplierPrice | null>(null)
const prices = ref<SupplierPrice[]>([])
const productOptions = ref<ProductOption[]>([])
const skuOptions = ref<ProductSkuOption[]>([])
const priceForm = reactive<{
  product_id?: number
  sku_id?: number | null
  price: string
  status: string
}>({
  product_id: undefined,
  sku_id: null,
  price: '',
  status: 'ACTIVE',
})

function resetForm(): void {
  Object.assign(form, {
    code: '',
    name: '',
    contact: '',
    phone: '',
    fax: '',
    address: '',
    settlement_type: '',
    email: '',
    remark: '',
    is_active: true,
  })
}

function resetAccountForm(): void {
  Object.assign(accountForm, {
    bank: '',
    branch: '',
    bank_no: '',
    account_no: '',
    address: '',
    is_default: false,
  })
}

function resetPriceForm(): void {
  Object.assign(priceForm, {
    product_id: undefined,
    sku_id: null,
    price: '',
    status: 'ACTIVE',
  })
  skuOptions.value = []
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const query = {
      keyword: keyword.value || undefined,
      is_active:
        activeFilter.value === '' ? undefined : activeFilter.value === 'true',
      page: page.value,
      page_size: pageSize.value,
    }
    const data =
      props.kind === 'supplier'
        ? await partyApi.listSuppliers(query)
        : await partyApi.listCustomers(query)
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : `${entityLabel.value}加载失败`)
  } finally {
    loading.value = false
  }
}

function search(): void {
  if (page.value === 1) {
    void load()
  } else {
    page.value = 1
  }
}

function resetAndLoad(): void {
  keyword.value = ''
  activeFilter.value = ''
  page.value = 1
  void load()
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: PartyRecord): void {
  editing.value = row
  Object.assign(form, {
    code: row.code,
    name: row.name,
    contact: row.contact || '',
    phone: row.phone || '',
    fax: row.fax || '',
    address: row.address || '',
    settlement_type: row.settlement_type || '',
    email: row.email || '',
    remark: row.remark || '',
    is_active: row.is_active,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.code || '').trim() || !String(form.name || '').trim()) {
    ElMessage.warning(`请填写${entityLabel.value}编码和名称`)
    return
  }
  saving.value = true
  try {
    if (props.kind === 'supplier') {
      if (editing.value) {
        await partyApi.updateSupplier(editing.value.id, { ...form })
      } else {
        await partyApi.createSupplier({ ...form })
      }
    } else if (editing.value) {
      await partyApi.updateCustomer(editing.value.id, { ...form })
    } else {
      await partyApi.createCustomer({ ...form })
    }
    ElMessage.success(editing.value ? '信息已更新' : '信息已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleActive(row: PartyRecord): Promise<void> {
  try {
    if (props.kind === 'supplier') {
      await (row.is_active
        ? partyApi.disableSupplier(row.id)
        : partyApi.enableSupplier(row.id))
    } else {
      await (row.is_active
        ? partyApi.disableCustomer(row.id)
        : partyApi.enableCustomer(row.id))
    }
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '状态更新失败')
  }
}

async function remove(row: PartyRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除“${row.name}”吗？`, '删除确认', {
      type: 'warning',
    })
    if (props.kind === 'supplier') {
      await partyApi.deleteSupplier(row.id)
    } else {
      await partyApi.deleteCustomer(row.id)
    }
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '删除失败')
    }
  }
}

async function openAccounts(row: PartyRecord): Promise<void> {
  accountTarget.value = row
  accountDrawerVisible.value = true
  await loadAccounts()
}

async function loadAccounts(): Promise<void> {
  if (!accountTarget.value) {
    return
  }
  accountLoading.value = true
  try {
    accounts.value =
      props.kind === 'supplier'
        ? await partyApi.listSupplierAccounts(accountTarget.value.id)
        : await partyApi.listCustomerAccounts(accountTarget.value.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '账户加载失败')
  } finally {
    accountLoading.value = false
  }
}

function openCreateAccount(): void {
  editingAccount.value = null
  resetAccountForm()
  accountDialogVisible.value = true
}

function openEditAccount(row: PartyAccount): void {
  editingAccount.value = row
  Object.assign(accountForm, {
    bank: row.bank || '',
    branch: row.branch || '',
    bank_no: row.bank_no || '',
    account_no: row.account_no || '',
    address: row.address || '',
    is_default: row.is_default,
  })
  accountDialogVisible.value = true
}

async function submitAccount(): Promise<void> {
  if (!accountTarget.value) {
    return
  }
  accountSaving.value = true
  try {
    if (editingAccount.value) {
      await partyApi.updateAccount(editingAccount.value.id, { ...accountForm })
    } else if (props.kind === 'supplier') {
      await partyApi.createSupplierAccount(accountTarget.value.id, {
        ...accountForm,
      })
    } else {
      await partyApi.createCustomerAccount(accountTarget.value.id, {
        ...accountForm,
      })
    }
    ElMessage.success(editingAccount.value ? '账户已更新' : '账户已创建')
    accountDialogVisible.value = false
    await loadAccounts()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '账户保存失败')
  } finally {
    accountSaving.value = false
  }
}

async function setDefaultAccount(row: PartyAccount): Promise<void> {
  try {
    await partyApi.setDefaultAccount(row.id)
    ElMessage.success('已设为默认账户')
    await loadAccounts()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '设置失败')
  }
}

async function removeAccount(row: PartyAccount): Promise<void> {
  try {
    await ElMessageBox.confirm('确定删除该账户吗？', '删除账户', {
      type: 'warning',
    })
    await partyApi.deleteAccount(row.id)
    ElMessage.success('账户已删除')
    await loadAccounts()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '账户删除失败')
    }
  }
}

async function openPrices(row: PartyRecord): Promise<void> {
  priceTarget.value = row
  priceDrawerVisible.value = true
  await loadPrices()
}

async function loadPrices(): Promise<void> {
  if (!priceTarget.value) {
    return
  }
  priceLoading.value = true
  try {
    const data = await partyApi.listSupplierPrices(priceTarget.value.id, {
      page_size: 200,
    })
    prices.value = data.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '供应商价格加载失败')
  } finally {
    priceLoading.value = false
  }
}

function openCreatePrice(): void {
  editingPrice.value = null
  resetPriceForm()
  priceDialogVisible.value = true
  void searchProducts('')
}

async function openEditPrice(row: SupplierPrice): Promise<void> {
  editingPrice.value = row
  Object.assign(priceForm, {
    product_id: row.product_id,
    sku_id: row.sku_id || null,
    price: String(row.price),
    status: row.status,
  })
  priceDialogVisible.value = true
  await Promise.all([
    searchProducts(row.product_name || row.product_code || ''),
    row.product_id ? loadSkus(row.product_id) : Promise.resolve(),
  ])
}

async function searchProducts(keywordValue: string): Promise<void> {
  productLoading.value = true
  try {
    const data = await productOptionsApi.listProducts(keywordValue)
    productOptions.value = data.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '商品选项加载失败')
  } finally {
    productLoading.value = false
  }
}

async function handleProductChange(productId?: number): Promise<void> {
  priceForm.sku_id = null
  skuOptions.value = []
  if (productId) {
    await loadSkus(productId)
  }
}

async function loadSkus(productId: number): Promise<void> {
  skuLoading.value = true
  try {
    skuOptions.value = await productOptionsApi.listSkus(productId)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '规格加载失败')
  } finally {
    skuLoading.value = false
  }
}

async function submitPrice(): Promise<void> {
  if (!priceTarget.value || !priceForm.product_id || !priceForm.price) {
    ElMessage.warning('请选择商品并填写定价')
    return
  }
  priceSaving.value = true
  try {
    const payload = {
      product_id: priceForm.product_id,
      sku_id: priceForm.sku_id || null,
      price: priceForm.price,
      status: priceForm.status,
    }
    if (editingPrice.value) {
      await partyApi.updateSupplierPrice(editingPrice.value.id, payload)
    } else {
      await partyApi.createSupplierPrice(priceTarget.value.id, payload)
    }
    ElMessage.success(editingPrice.value ? '价格已更新' : '价格已创建')
    priceDialogVisible.value = false
    await loadPrices()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '价格保存失败')
  } finally {
    priceSaving.value = false
  }
}

async function togglePriceStatus(row: SupplierPrice): Promise<void> {
  try {
    const status = row.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE'
    await partyApi.setSupplierPriceStatus(row.id, status)
    ElMessage.success(status === 'ACTIVE' ? '价格已启用' : '价格已停用')
    await loadPrices()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '价格状态更新失败')
  }
}

async function removePrice(row: SupplierPrice): Promise<void> {
  try {
    await ElMessageBox.confirm('确定删除该供应商价格吗？', '删除价格', {
      type: 'warning',
    })
    await partyApi.deleteSupplierPrice(row.id)
    ElMessage.success('价格已删除')
    await loadPrices()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '价格删除失败')
    }
  }
}

onMounted(load)
</script>

<style scoped>
.status-filter {
  width: 130px;
}

.settings-page__actions--wrap {
  flex-wrap: wrap;
}
</style>
