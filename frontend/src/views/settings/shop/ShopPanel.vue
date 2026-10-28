<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <div class="settings-page__toolbar-actions">
        <el-input
          v-model="keyword"
          class="settings-page__search"
          clearable
          placeholder="店铺名称、账户或 tag"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select
          v-model="platformFilter"
          class="platform-filter"
          clearable
          placeholder="全部平台"
          @change="search"
        >
          <el-option
            v-for="item in platforms"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>
      <el-button v-if="canManage" type="primary" @click="openCreate">
        新增店铺
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
      empty-description="暂无店铺"
      empty-action-text="重置筛选"
      :show-empty-action="canManage"
      @query-change="load"
      @empty-action="resetAndLoad"
    >
      <template #platform="{ row }">
        {{ row.platform_name || '--' }}
      </template>
      <template #sync="{ row }">
        <div class="sync-tags">
          <el-tag :type="row.auto_sync_order ? 'success' : 'info'" size="small">
            订单{{ row.auto_sync_order ? '开' : '关' }}
          </el-tag>
          <el-tag :type="row.auto_sync_stock ? 'success' : 'info'" size="small">
            库存{{ row.auto_sync_stock ? '开' : '关' }}
          </el-tag>
        </div>
      </template>
      <template #locked="{ row }">
        <StatusTag
          :status="row.is_locked ? 'LOCKED' : 'ACTIVE'"
          :label="row.is_locked ? '已锁定' : '正常'"
          :tone="row.is_locked ? 'warning' : 'success'"
        />
      </template>
      <template #api="{ row }">
        <StatusTag
          :status="row.api_configured ? 'ACTIVE' : 'UNCONFIGURED'"
          :label="row.api_configured ? '已配置' : '未配置'"
          :tone="row.api_configured ? 'success' : 'info'"
        />
      </template>
      <template #actions="{ row }">
        <div v-if="canManage" class="settings-page__actions">
          <el-button link type="primary" @click="openEdit(row)">
            编辑
          </el-button>
          <el-button link type="primary" @click="openApi(row)">
            API
          </el-button>
          <el-dropdown trigger="click" @command="handleCommand($event, row)">
            <el-button link type="primary">
              更多<el-icon><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="sync-order">
                  {{ row.auto_sync_order ? '关闭订单同步' : '开启订单同步' }}
                </el-dropdown-item>
                <el-dropdown-item command="sync-stock">
                  {{ row.auto_sync_stock ? '关闭库存同步' : '开启库存同步' }}
                </el-dropdown-item>
                <el-dropdown-item command="lock">
                  {{ row.is_locked ? '解锁店铺' : '锁定店铺' }}
                </el-dropdown-item>
                <el-dropdown-item command="delete" divided>
                  删除店铺
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        <span v-else class="settings-page__muted">只读</span>
      </template>
    </DataTable>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑店铺' : '新增店铺'"
      :width="820"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="所属平台" required>
          <el-select v-model="form.platform_id" filterable>
            <el-option
              v-for="item in platforms"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="店铺名称" required>
          <el-input v-model="form.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="登录账号">
          <el-input v-model="form.login_account" maxlength="64" />
        </el-form-item>
        <el-form-item label="店铺网址">
          <el-input v-model="form.url" maxlength="255" />
        </el-form-item>
        <el-form-item label="店铺 Tag">
          <el-input v-model="form.tag" maxlength="64" />
        </el-form-item>
        <el-form-item label="指定供应商">
          <el-select v-model="form.supplier_id" clearable filterable>
            <el-option
              v-for="item in suppliers"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="下载订单方式">
          <el-select v-model="form.download_mode" clearable>
            <el-option label="获取全部订单" value="ALL" />
            <el-option label="只获取对应商品订单" value="MATCH_PRODUCT" />
          </el-select>
        </el-form-item>
        <el-form-item label="默认发货地址">
          <el-input v-model="form.default_address" maxlength="255" />
        </el-form-item>
        <el-form-item label="发货短信模板">
          <el-input v-model="form.sms_ship_template" maxlength="64" />
        </el-form-item>
        <el-form-item label="派件短信模板">
          <el-input v-model="form.sms_deliver_template" maxlength="64" />
        </el-form-item>
        <el-form-item label="自动同步订单">
          <el-switch v-model="form.auto_sync_order" />
        </el-form-item>
        <el-form-item label="自动同步库存">
          <el-switch v-model="form.auto_sync_stock" />
        </el-form-item>
        <el-form-item label="启用物流参数">
          <el-switch v-model="form.enable_logistics_params" />
        </el-form-item>
        <el-form-item label="锁定店铺">
          <el-switch v-model="form.is_locked" />
        </el-form-item>
        <el-form-item class="is-full-width" label="留言匹配">
          <el-input
            v-model="form.message_match"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
      </div>
    </FormDialog>

    <FormDialog
      v-model:visible="apiDialogVisible"
      :title="`${apiTarget?.name || '店铺'} · API 配置`"
      :width="660"
      :loading="apiSaving"
      @submit="submitApi"
    >
      <el-alert
        v-if="apiForm.has_app_secret"
        type="success"
        :closable="false"
        show-icon
        title="已保存 AppSecret；不做修改时保持为空，密钥不会被覆盖。"
      />
      <el-form-item class="is-full-width" label="API 关联码">
        <el-input v-model="apiForm.api_code" maxlength="128" />
      </el-form-item>
      <el-form-item class="is-full-width" label="验证码">
        <el-input v-model="apiForm.verify_code" maxlength="128" />
      </el-form-item>
      <el-form-item class="is-full-width" label="AppKey">
        <el-input v-model="apiForm.app_key" maxlength="128" />
      </el-form-item>
      <el-form-item class="is-full-width" label="AppSecret">
        <el-input
          v-model="apiForm.app_secret"
          type="password"
          show-password
          maxlength="128"
          placeholder="留空则不修改已保存密钥"
        />
      </el-form-item>
      <el-form-item
        v-if="apiForm.has_app_secret"
        class="is-full-width"
        label="清除密钥"
      >
        <el-checkbox v-model="apiForm.clear_app_secret">
          提交后清除当前 AppSecret
        </el-checkbox>
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ArrowDown } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { partyApi, type PartyRecord } from '@/api/party'
import {
  shopApi,
  type Platform,
  type Shop,
  type ShopApiPayload,
  type ShopPayload,
} from '@/api/shop'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'

defineProps<{
  canManage: boolean
}>()

const columns: DataTableColumn<Shop>[] = [
  { prop: 'name', label: '店铺名称', minWidth: 160 },
  { label: '平台', slot: 'platform', minWidth: 120 },
  { prop: 'login_account', label: '登录账号', minWidth: 130 },
  { prop: 'tag', label: 'Tag', minWidth: 100 },
  { prop: 'supplier_name', label: '供应商', minWidth: 130 },
  { label: '同步开关', slot: 'sync', minWidth: 150 },
  { label: 'API', slot: 'api', width: 90, align: 'center' },
  { label: '店铺状态', slot: 'locked', width: 100, align: 'center' },
  { label: '操作', slot: 'actions', width: 230, fixed: 'right' },
]

const rows = ref<Shop[]>([])
const platforms = ref<Platform[]>([])
const suppliers = ref<PartyRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const platformFilter = ref<number | undefined>()
const dialogVisible = ref(false)
const editing = ref<Shop | null>(null)
const apiDialogVisible = ref(false)
const apiSaving = ref(false)
const apiTarget = ref<Shop | null>(null)
const form = reactive<ShopPayload>({
  platform_id: undefined,
  name: '',
  login_account: '',
  url: '',
  tag: '',
  supplier_id: null,
  default_address: '',
  is_locked: false,
  auto_sync_order: false,
  auto_sync_stock: false,
  download_mode: '',
  sms_ship_template: '',
  sms_deliver_template: '',
  enable_logistics_params: true,
  message_match: '',
})
const apiForm = reactive<ShopApiPayload & {
  has_app_secret: boolean
  clear_app_secret: boolean
}>({
  api_code: '',
  verify_code: '',
  app_key: '',
  app_secret: '',
  has_app_secret: false,
  clear_app_secret: false,
})

function resetForm(): void {
  Object.assign(form, {
    platform_id: undefined,
    name: '',
    login_account: '',
    url: '',
    tag: '',
    supplier_id: null,
    default_address: '',
    is_locked: false,
    auto_sync_order: false,
    auto_sync_stock: false,
    download_mode: '',
    sms_ship_template: '',
    sms_deliver_template: '',
    enable_logistics_params: true,
    message_match: '',
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await shopApi.listShops({
      keyword: keyword.value || undefined,
      platform_id: platformFilter.value,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions(): Promise<void> {
  try {
    const [platformRows, supplierData] = await Promise.all([
      shopApi.listPlatforms(),
      partyApi.listSuppliers({ is_active: true, page_size: 200 }),
    ])
    platforms.value = platformRows
    suppliers.value = supplierData.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '平台供应商加载失败')
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
  platformFilter.value = undefined
  page.value = 1
  void load()
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Shop): void {
  editing.value = row
  Object.assign(form, {
    platform_id: row.platform_id,
    name: row.name,
    login_account: row.login_account || '',
    url: row.url || '',
    tag: row.tag || '',
    supplier_id: row.supplier_id || null,
    default_address: row.default_address || '',
    is_locked: row.is_locked,
    auto_sync_order: row.auto_sync_order,
    auto_sync_stock: row.auto_sync_stock,
    download_mode: row.download_mode || '',
    sms_ship_template: row.sms_ship_template || '',
    sms_deliver_template: row.sms_deliver_template || '',
    enable_logistics_params: row.enable_logistics_params,
    message_match: row.message_match || '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.platform_id || !String(form.name || '').trim()) {
    ElMessage.warning('请选择所属平台并填写店铺名称')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await shopApi.updateShop(editing.value.id, { ...form })
    } else {
      await shopApi.createShop({ ...form })
    }
    ElMessage.success(editing.value ? '店铺已更新' : '店铺已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '店铺保存失败')
  } finally {
    saving.value = false
  }
}

async function openApi(row: Shop): Promise<void> {
  apiTarget.value = row
  Object.assign(apiForm, {
    api_code: '',
    verify_code: '',
    app_key: '',
    app_secret: '',
    has_app_secret: false,
    clear_app_secret: false,
  })
  try {
    const config = row.api_config || (await shopApi.getShopApi(row.id))
    Object.assign(apiForm, {
      api_code: config?.api_code || '',
      verify_code: config?.verify_code || '',
      app_key: config?.app_key || '',
      app_secret: '',
      has_app_secret: Boolean(config?.has_app_secret),
      clear_app_secret: false,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'API 配置加载失败')
  }
  apiDialogVisible.value = true
}

async function submitApi(): Promise<void> {
  if (!apiTarget.value) {
    return
  }
  const payload: ShopApiPayload = {
    api_code: apiForm.api_code || null,
    verify_code: apiForm.verify_code || null,
    app_key: apiForm.app_key || null,
  }
  if (apiForm.clear_app_secret) {
    payload.app_secret = null
  } else if (apiForm.app_secret) {
    payload.app_secret = apiForm.app_secret
  }
  apiSaving.value = true
  try {
    await shopApi.saveShopApi(apiTarget.value.id, payload)
    ElMessage.success('店铺 API 配置已保存')
    apiDialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'API 配置保存失败')
  } finally {
    apiSaving.value = false
  }
}

async function handleCommand(command: string, row: Shop): Promise<void> {
  try {
    if (command === 'sync-order') {
      await shopApi.setSyncOrder(row.id, !row.auto_sync_order)
    } else if (command === 'sync-stock') {
      await shopApi.setSyncStock(row.id, !row.auto_sync_stock)
    } else if (command === 'lock') {
      await (row.is_locked ? shopApi.unlockShop(row.id) : shopApi.lockShop(row.id))
    } else if (command === 'delete') {
      await ElMessageBox.confirm(`确定删除店铺“${row.name}”吗？`, '删除店铺', {
        type: 'warning',
      })
      await shopApi.deleteShop(row.id)
    }
    ElMessage.success('店铺状态已更新')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '店铺操作失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
})
</script>

<style scoped>
.platform-filter {
  width: 180px;
}

.sync-tags {
  display: flex;
  gap: 6px;
}
</style>
