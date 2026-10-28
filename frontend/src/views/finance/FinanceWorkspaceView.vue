<template>
  <div class="settings-page finance-page">
    <SettingsPageHeader :title="config.title" :description="config.description" />
    <section class="settings-page__panel">
      <div class="settings-page__toolbar">
        <h2>{{ config.title }} <span class="finance-count">{{ total }} 条</span></h2>
        <div class="settings-page__toolbar-actions">
          <el-input v-if="config.search" v-model="keyword" clearable placeholder="输入关键字查询" @keyup.enter="search" @clear="search" />
          <el-button v-if="config.search" @click="search">查询</el-button>
          <el-button v-if="config.seed" :disabled="!canManage" @click="importSeed">导入金蝶科目</el-button>
          <el-button v-if="config.create && canManage" type="primary" @click="openCreate">{{ config.createLabel || '新增' }}</el-button>
        </div>
      </div>

      <DataTable v-model:page="page" v-model:page-size="pageSize" :rows="rows" :columns="columns" :loading="loading" :total="total" :sync-query="false" :empty-description="`暂无${config.title}`" @query-change="load">
        <template #money="{ row, column }"><MoneyText :value="row[column.property]" /></template>
        <template #state="{ row }"><StatusTag :status="row.status || row.audit_status || (row.is_enabled ? 'ACTIVE' : 'DISABLED')" :label="displayStatus(row.status || row.audit_status || (row.is_enabled ? 'ACTIVE' : 'DISABLED'))" /></template>
        <template #date="{ row }">{{ formatDateTime(row.created_at || row.saved_at || row.settlement_date || row.business_date) }}</template>
        <template #actions="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-dropdown v-if="actionItems(row).length" @command="(command: string) => runAction(row, command)">
              <el-button link type="primary">操作<i class="fa fa-angle-down finance-chevron" /></el-button>
              <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="item in actionItems(row)" :key="item.command" :command="item.command" :disabled="item.manage && !canManage">{{ item.label }}</el-dropdown-item></el-dropdown-menu></template>
            </el-dropdown>
          </div>
        </template>
      </DataTable>
    </section>

    <FormDialog v-model:visible="formVisible" :title="`新增${config.title}`" :loading="saving" @submit="submitCreate">
      <div class="finance-form-grid">
        <el-form-item v-for="field in config.fields || []" :key="field.key" :label="field.label" :required="field.required">
          <el-input v-if="field.kind === 'text' || !field.kind" v-model="form[field.key]" :placeholder="field.placeholder || `请输入${field.label}`" :maxlength="field.maxlength || 255" />
          <el-input v-else-if="field.kind === 'textarea'" v-model="form[field.key]" type="textarea" :rows="3" />
          <el-input v-else-if="field.kind === 'number'" v-model="form[field.key]" type="number" :min="field.min ?? 0" :step="field.step || '0.01'" />
          <el-select v-else-if="field.kind === 'select'" v-model="form[field.key]" clearable filterable>
            <el-option v-for="option in field.options || lookupOptions[field.key] || []" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-switch v-else-if="field.kind === 'switch'" v-model="form[field.key]" />
          <el-date-picker v-else-if="field.kind === 'date'" v-model="form[field.key]" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
      </div>
      <el-alert v-if="config.formNote" :title="config.formNote" type="info" :closable="false" />
    </FormDialog>

    <el-drawer v-model="detailVisible" :title="`${config.title}详情`" size="min(760px, 92vw)">
      <el-skeleton v-if="detailLoading" :rows="8" animated />
      <template v-else-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item v-for="field in detailFields" :key="field.label" :label="field.label">
            <MoneyText v-if="field.money" :value="detail[field.key]" />
            <span v-else>{{ detail[field.key] ?? '--' }}</span>
          </el-descriptions-item>
        </el-descriptions>
          <template v-if="detail.items?.length">
          <el-divider content-position="left">凭证明细（借贷分列）</el-divider>
          <el-table :data="detail.items" border row-key="id">
            <el-table-column prop="subject_code" label="科目编码" width="110" />
            <el-table-column prop="subject_name" label="会计科目" min-width="150" />
            <el-table-column prop="summary" label="摘要" min-width="160" />
            <el-table-column label="借方" width="140" align="right"><template #default="{ row }"><MoneyText :value="row.debit" /></template></el-table-column>
            <el-table-column label="贷方" width="140" align="right"><template #default="{ row }"><MoneyText :value="row.credit" /></template></el-table-column>
          </el-table>
          <div class="finance-totals"><span>借方合计 <MoneyText :value="detail.total_debit" /></span><span>贷方合计 <MoneyText :value="detail.total_credit" /></span></div>
        </template>
        <template v-if="detail.voucher">
          <el-divider content-position="left">关联凭证</el-divider>
          <div class="finance-voucher"><strong>{{ detail.voucher.voucher_word }}{{ detail.voucher.voucher_no }}</strong><span>{{ detail.voucher.status || '已生成' }}</span><el-button link type="primary" @click="voucherPreview = detail.voucher">查看分录</el-button></div>
        </template>
        <section v-if="voucherPreview?.items?.length" class="finance-voucher-preview">
          <el-divider content-position="left">{{ voucherPreview ? `关联凭证 ${voucherPreview.voucher_word || ''}${voucherPreview.voucher_no || ''}` : '凭证明细（借贷分列）' }}</el-divider>
          <el-table :data="voucherPreview.items" border row-key="id">
            <el-table-column prop="subject_code" label="科目编码" width="110" />
            <el-table-column prop="subject_name" label="会计科目" min-width="150" />
            <el-table-column prop="summary" label="摘要" min-width="160" />
            <el-table-column label="借方" width="140" align="right"><template #default="{ row }"><MoneyText :value="row.debit" /></template></el-table-column>
            <el-table-column label="贷方" width="140" align="right"><template #default="{ row }"><MoneyText :value="row.credit" /></template></el-table-column>
          </el-table>
          <div class="finance-totals"><span>借方合计 <MoneyText :value="voucherPreview.total_debit" /></span><span>贷方合计 <MoneyText :value="voucherPreview.total_credit" /></span></div>
        </section>
        <div v-if="detailActions.length" class="finance-detail-actions">
          <el-button v-for="item in detailActions" :key="item.command" :type="item.tone || 'primary'" plain @click="runAction(detail, item.command)">{{ item.label }}</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { financeApi, type FinanceRow } from '@/api/finance'
import { partyApi } from '@/api/party'
import { shopApi } from '@/api/shop'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import '@/views/settings/settings.css'

type Field = { key: string; label: string; required?: boolean; kind?: string; placeholder?: string; maxlength?: number; min?: number; step?: string; options?: { label: string; value: string | number | boolean }[] }
type ActionItem = { command: string; label: string; manage?: boolean; tone?: 'primary' | 'success' | 'warning' | 'danger' }
type PageConfig = { title: string; description: string; path: string; search?: boolean; create?: boolean; createLabel?: string; seed?: boolean; fields?: Field[]; columns: { key: string; label: string; money?: boolean; slot?: string; width?: number }[]; formNote?: string; actions?: (row: FinanceRow) => ActionItem[]; isArray?: boolean }
const simpleState = [{ label: '草稿', value: 'DRAFT' }, { label: '已审核', value: 'APPROVED' }, { label: '已作废', value: 'CANCELLED' }]
const configs: Record<string, PageConfig> = {
  'finance-subjects': { title: '会计科目', description: '维护会计科目及辅助核算属性', path: '/account-subjects', search: true, create: true, seed: true, columns: [{ key: 'code', label: '科目编码' }, { key: 'name', label: '科目名称' }, { key: 'category_name', label: '科目类别' }, { key: 'balance_direction_name', label: '余额方向' }, { key: 'project_category_name', label: '项目类别' }, { key: 'is_enabled', label: '状态', slot: 'state' }], fields: [{ key: 'code', label: '科目编码', required: true }, { key: 'name', label: '科目名称', required: true }, { key: 'category', label: '科目类别', kind: 'select', options: ['ASSET','LIABILITY','EQUITY','COST','PROFIT_LOSS'].map((x) => ({ label: x, value: x })) }, { key: 'balance_direction', label: '余额方向', kind: 'select', options: [{ label: '借方', value: 'DEBIT' }, { label: '贷方', value: 'CREDIT' }] }] },
  'finance-project-categories': { title: '项目核算类别', description: '维护凭证辅助核算使用的项目类别', path: '/project-categories', create: true, isArray: true, columns: [{ key: 'code', label: '类别编码' }, { key: 'name', label: '类别名称' }, { key: 'remark', label: '说明' }], fields: [{ key: 'code', label: '类别编码', required: true }, { key: 'name', label: '类别名称', required: true }, { key: 'remark', label: '说明', kind: 'textarea' }] },
  'finance-vouchers': { title: '凭证管理', description: '查看凭证、借贷分录、审核状态及金蝶同步状态', path: '/vouchers', search: true, columns: [{ key: 'voucher_no', label: '凭证号' }, { key: 'business_date', label: '业务日期' }, { key: 'reference', label: '凭证摘要' }, { key: 'total_debit', label: '借方合计', money: true, slot: 'money' }, { key: 'total_credit', label: '贷方合计', money: true, slot: 'money' }, { key: 'status', label: '状态', slot: 'state' }], actions: (r) => [...(r.status === 'DRAFT' ? [{ command: 'audit', label: '审核', manage: true, tone: 'success' as const }] : []), ...(r.status === 'APPROVED' ? [{ command: 'unaudit', label: '反审', manage: true, tone: 'warning' as const }, { command: 'kingdee-sync', label: '同步金蝶', manage: true }] : [])] },
  'finance-payments': { title: '付款单', description: '登记供应商付款，审核后自动生成记账凭证', path: '/payments', search: true, create: true, fields: [{ key: 'supplier_id', label: '供应商', required: true, kind: 'select' }, { key: 'amount', label: '付款金额', required: true, kind: 'number' }, { key: 'payment_type', label: '付款类型' }, { key: 'pay_method', label: '付款方式' }], columns: [{ key: 'payment_no', label: '付款单号' }, { key: 'supplier_name', label: '供应商' }, { key: 'payment_type', label: '付款类型' }, { key: 'amount', label: '付款金额', money: true, slot: 'money' }, { key: 'audit_status', label: '审核状态', slot: 'state' }, { key: 'created_at', label: '创建时间', slot: 'date' }], actions: (r) => financeDocActions(r) },
  'finance-receipts': { title: '收款单', description: '登记客户收款，审核后自动生成记账凭证', path: '/receipts', search: true, create: true, fields: [{ key: 'customer_id', label: '客户', required: true, kind: 'select' }, { key: 'amount', label: '收款金额', required: true, kind: 'number' }, { key: 'sale_type', label: '销售类型' }, { key: 'settle_type', label: '结算方式' }], columns: [{ key: 'receipt_no', label: '收款单号' }, { key: 'customer_name', label: '客户' }, { key: 'sale_type', label: '销售类型' }, { key: 'amount', label: '收款金额', money: true, slot: 'money' }, { key: 'audit_status', label: '审核状态', slot: 'state' }, { key: 'created_at', label: '创建时间', slot: 'date' }], actions: (r) => financeDocActions(r) },
  'finance-expenses': { title: '费用单', description: '登记费用并跟踪审核及凭证', path: '/expenses', search: true, create: true, fields: [{ key: 'customer_id', label: '客户', required: true, kind: 'select' }, { key: 'amount', label: '费用金额', required: true, kind: 'number' }, { key: 'expense_type', label: '费用类型' }, { key: 'description', label: '费用说明', kind: 'textarea' }], columns: [{ key: 'expense_no', label: '费用单号' }, { key: 'customer_name', label: '客户' }, { key: 'expense_type', label: '费用类型' }, { key: 'amount', label: '金额', money: true, slot: 'money' }, { key: 'audit_status', label: '审核状态', slot: 'state' }, { key: 'created_at', label: '创建时间', slot: 'date' }], actions: (r) => financeDocActions(r) },
  'finance-settlements': { title: '结算单', description: '管理平台结算单，核对后确认并生成应收款', path: '/settlements', search: true, create: true, fields: [{ key: 'shop_id', label: '店铺', required: true, kind: 'select' }, { key: 'amount', label: '结算金额', required: true, kind: 'number' }, { key: 'platform_no', label: '平台结算单号' }, { key: 'settlement_date', label: '结算日期', kind: 'date' }, { key: 'remark', label: '备注', kind: 'textarea' }], columns: [{ key: 'settlement_no', label: '结算单号' }, { key: 'platform_no', label: '平台单号' }, { key: 'shop_name', label: '店铺' }, { key: 'amount', label: '结算金额', money: true, slot: 'money' }, { key: 'status', label: '状态', slot: 'state' }, { key: 'settlement_date', label: '结算日期', slot: 'date' }], actions: (r) => [...(r.status === 'PENDING' ? [{ command: 'check', label: '核对', manage: true, tone: 'success' as const }] : []), ...(r.status === 'CHECKED' ? [{ command: 'confirm', label: '确认并生成应收', manage: true, tone: 'primary' as const }] : [])] },
  'finance-incomes': { title: '收入单', description: '登记其他收入并跟踪审核、核销和凭证', path: '/incomes', search: true, create: true, fields: [{ key: 'supplier_id', label: '往来单位', required: true, kind: 'select' }, { key: 'amount', label: '收入金额', required: true, kind: 'number' }, { key: 'income_type', label: '收入类型' }, { key: 'description', label: '收入说明', kind: 'textarea' }], columns: [{ key: 'income_no', label: '收入单号' }, { key: 'supplier_name', label: '往来单位' }, { key: 'income_type', label: '收入类型' }, { key: 'amount', label: '金额', money: true, slot: 'money' }, { key: 'audit_status', label: '审核状态', slot: 'state' }, { key: 'created_at', label: '创建时间', slot: 'date' }], actions: (r) => financeDocActions(r) },
  'finance-invoices': { title: '发票管理', description: '管理发票登记、开具和收票状态', path: '/invoices', search: true, create: true, fields: [{ key: 'invoice_title', label: '发票抬头', required: true }, { key: 'tax_no', label: '纳税人识别号' }, { key: 'invoice_type', label: '发票类型', required: true, kind: 'select', options: [{ label: '普通发票', value: 'NORMAL' }, { label: '专用发票', value: 'SPECIAL' }, { label: '电子发票', value: 'ELECTRONIC' }, { label: '红字专用发票', value: 'RED_SPECIAL' }, { label: '红字普通发票', value: 'RED_NORMAL' }] }, { key: 'amount', label: '发票金额', required: true, kind: 'number' }, { key: 'company', label: '开票单位' }, { key: 'applicant', label: '申请人' }], columns: [{ key: 'invoice_no', label: '发票号' }, { key: 'invoice_title', label: '发票抬头' }, { key: 'invoice_type_name', label: '发票类型' }, { key: 'amount', label: '发票金额', money: true, slot: 'money' }, { key: 'received_amount', label: '已收金额', money: true, slot: 'money' }, { key: 'status', label: '状态', slot: 'state' }], actions: (r) => [...(r.status === 'PENDING' ? [{ command: 'issue', label: '开具', manage: true }] : []), ...(!r.is_received ? [{ command: 'receive', label: '登记收票', manage: true, tone: 'success' as const }] : [])] },
  'finance-supplier-balances': { title: '供应商账户余额', description: '查看供应商余额及最近一次与金蝶同步的结果', path: '/supplier-account-balances', search: true, columns: [{ key: 'supplier_name', label: '供应商' }, { key: 'balance', label: '账户余额', money: true, slot: 'money' }, { key: 'sync_status', label: '同步状态', slot: 'state' }, { key: 'balance_synced_at', label: '同步时间', slot: 'date' }], actions: () => [{ command: 'sync', label: '同步金蝶', manage: true }] },
}
function financeDocActions(row: FinanceRow): ActionItem[] {
  const approvalStatus = row.approval?.state?.status || row.approval?.status
  if (approvalStatus === 'PENDING') return [{ command: 'approve', label: '审核通过', manage: true, tone: 'success' }, { command: 'reject', label: '审核不通过', manage: true, tone: 'danger' }]
  if (approvalStatus === 'NOT_SUBMITTED' || approvalStatus === 'REJECTED') return [{ command: 'submit', label: '提交审批', manage: true }]
  if (approvalStatus === 'APPROVED') return [{ command: 'unaudit', label: '弃审', manage: true, tone: 'warning' }]
  return []
}
const route = useRoute()
const auth = useAuthStore()
const config = computed(() => {
  if (route.name === 'purchase-payments') {
    return {
      ...configs['finance-payments'],
      title: '采购付款单',
      description: '查看和登记本公司供应商采购付款，审核后生成记账凭证',
    }
  }
  return configs[String(route.name)] || configs['finance-vouchers']
})
const canManage = computed(() => auth.hasPermission('finance:manage'))
const rows = ref<FinanceRow[]>([]); const total = ref(0); const page = ref(1); const pageSize = ref(20); const keyword = ref(''); const loading = ref(false); const saving = ref(false); const formVisible = ref(false); const detailVisible = ref(false); const detailLoading = ref(false); const detail = ref<FinanceRow | null>(null); const voucherPreview = ref<FinanceRow | null>(null)
const lookupOptions = reactive<Record<string, { label: string; value: number }[]>>({ supplier_id: [], customer_id: [], shop_id: [] })
const form = reactive<Record<string, any>>({})
const columns = computed<DataTableColumn<FinanceRow>[]>(() => config.value.columns.map((column) => ({ prop: column.key, label: column.label, minWidth: column.width ? undefined : 130, width: column.width, slot: column.slot || (column.money ? 'money' : undefined), formatter: column.key === 'is_enabled' ? (row) => row.is_enabled ? '启用' : '停用' : undefined })))
const detailFields = computed(() => {
  if (!detail.value) return []
  const primary = config.value.columns.filter((column) => column.key !== 'items').map((column) => ({ ...column, key: column.key }))
  return primary.filter((field) => detail.value?.[field.key] !== undefined && detail.value?.[field.key] !== null && typeof detail.value?.[field.key] !== 'object').map((field) => ({ ...field, label: field.label, money: field.money || ['amount','balance','total_debit','total_credit','received_amount','unreceived_amount'].includes(field.key) }))
})
const detailActions = computed(() => detail.value ? actionItems(detail.value) : [])
function actionItems(row: FinanceRow): ActionItem[] { return config.value.actions?.(row) || [] }
function displayStatus(value: string): string { return ({ DRAFT: '草稿', PENDING: '待处理', NOT_SUBMITTED: '未提交', APPROVED: '已审核', REJECTED: '已驳回', CANCELLED: '已作废', CHECKED: '已核对', ISSUED: '已开具', RECEIVED: '已收票', ACTIVE: '启用', DISABLED: '停用', SYNCED: '已同步', NOT_SYNCED: '未同步', FAILED: '同步失败' } as Record<string, string>)[value] || value || '--' }
async function load(): Promise<void> {
  loading.value = true
  try {
    const params = { keyword: keyword.value || undefined, page: page.value, page_size: pageSize.value }
    const result = await financeApi.list(config.value.path, params)
    if (Array.isArray(result)) { rows.value = result; total.value = result.length }
    else { rows.value = result.items || []; total.value = result.total || 0 }
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : `${config.value.title}加载失败`) } finally { loading.value = false }
}
async function loadLookups(): Promise<void> {
  try {
    const [suppliers, customers, shops] = await Promise.all([
      partyApi.listSuppliers({ is_active: true, page_size: 200 }),
      partyApi.listCustomers({ is_active: true, page_size: 200 }),
      shopApi.listShops({ page_size: 200 }),
    ])
    lookupOptions.supplier_id = suppliers.items.map((item) => ({ label: item.name, value: item.id }))
    lookupOptions.customer_id = customers.items.map((item) => ({ label: item.name, value: item.id }))
    lookupOptions.shop_id = shops.items.map((item) => ({ label: item.name, value: item.id }))
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '财务表单选项加载失败') }
}
function search(): void { page.value = 1; void load() }
function openCreate(): void { Object.keys(form).forEach((key) => delete form[key]); (config.value.fields || []).forEach((field) => { form[field.key] = field.kind === 'switch' ? false : '' }); formVisible.value = true }
async function submitCreate(): Promise<void> {
  const fields = config.value.fields || []
  const missing = fields.find((field) => field.required && (form[field.key] === '' || form[field.key] === null || form[field.key] === undefined))
  if (missing) { ElMessage.warning(`请填写${missing.label}`); return }
  saving.value = true
  try {
    const payload: Record<string, unknown> = {}
    for (const field of fields) {
      const value = form[field.key]
      if (value === '' || value === null || value === undefined) continue
      payload[field.key] = field.kind === 'number' ? String(value) : value
    }
    await financeApi.create(config.value.path, payload); ElMessage.success(`${config.value.title}已创建`); formVisible.value = false; await load()
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : `${config.value.title}创建失败`) } finally { saving.value = false }
}
async function openDetail(row: FinanceRow): Promise<void> {
  detailVisible.value = true; detailLoading.value = true
  try { detail.value = await financeApi.detail(config.value.path, row.id) } catch (error) { detail.value = row; ElMessage.error(error instanceof Error ? error.message : '详情加载失败') } finally { detailLoading.value = false }
}
async function importSeed(): Promise<void> {
  try { const { value } = await ElMessageBox.confirm('导入标准金蝶科目；已存在科目默认保留原配置。是否继续？', '导入科目', { type: 'info', distinguishCancelAndClose: true }); if (value !== 'confirm') return; await financeApi.create('/account-subjects/import-seed', { update_existing: false }); ElMessage.success('科目种子导入完成'); await load() } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '导入失败') }
}
async function runAction(row: FinanceRow, command: string): Promise<void> {
  if (!canManage.value) { ElMessage.warning('当前账号没有财务管理权限'); return }
  try {
    let payload: Record<string, unknown> = {}
    if (['reject','unaudit','receive','delete'].includes(command)) {
      const labels: Record<string, string> = { reject: '请填写不通过原因', unaudit: '请填写弃审原因', receive: '请输入本次收票金额', delete: '请填写作废原因' }
      const { value } = await ElMessageBox.prompt(labels[command], command === 'receive' ? '登记收票' : '财务单据操作', { inputType: command === 'receive' ? 'number' : 'textarea', inputValidator: (value) => Boolean(value?.trim()) || '此项必填' })
      payload = command === 'receive' ? { received_amount: String(value), is_received: true } : command === 'reject' ? { comment: value.trim() } : { reason: value.trim() }
    }
    if (command === 'approve') { const { value } = await ElMessageBox.prompt('可填写审核意见', '审核通过', { inputPlaceholder: '可留空' }); payload = { comment: value || undefined } }
    let result: FinanceRow | undefined
    if (command === 'sync') result = await financeApi.action(config.value.path, row.id, 'sync')
    else if (command === 'kingdee-sync') result = await financeApi.action(config.value.path, row.id, command)
    else if (command === 'receive') result = await financeApi.action(config.value.path, row.id, command, payload)
    else if (command === 'delete') await financeApi.remove(config.value.path, row.id, String(payload.reason))
    else result = await financeApi.action(config.value.path, row.id, command, payload)
    ElMessage.success('操作已完成')
    if (result && detail.value?.id === row.id) detail.value = result
    await load()
    if (detail.value && detail.value.id === row.id) await openDetail(row)
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '操作失败') }
}
watch(() => route.name, () => { page.value = 1; keyword.value = ''; detail.value = null; voucherPreview.value = null; void load() })
onMounted(() => { void Promise.all([load(), loadLookups()]) })
</script>

<style scoped>
.finance-count { color: var(--el-text-color-secondary); font-size: 13px; font-weight: 400; margin-left: 8px; }
.finance-chevron { margin-left: 5px; }
.finance-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.finance-detail-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 24px; }
.finance-totals { display: flex; justify-content: flex-end; gap: 36px; padding: 14px 8px; font-weight: 600; }
.finance-voucher { display: flex; align-items: center; gap: 16px; }
@media (max-width: 720px) { .finance-form-grid { grid-template-columns: 1fr; } }
</style>
