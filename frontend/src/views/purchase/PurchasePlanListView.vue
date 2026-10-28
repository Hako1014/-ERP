<template>
  <div class="settings-page purchase-page">
    <SettingsPageHeader
      title="采购计划"
      description="按前 20 日销量、现有库存和在途库存测算采购建议，并跟踪计划转单与审批。"
    />

    <section class="settings-page__panel">
      <div class="purchase-toolbar">
        <div class="purchase-toolbar__filters">
          <el-input
            v-model="keyword"
            class="settings-page__search"
            clearable
            placeholder="计划编号或供应商"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="supplierFilter"
            class="purchase-filter--wide"
            clearable
            filterable
            placeholder="全部供应商"
            @change="search"
          >
            <el-option
              v-for="item in supplierOptions"
              :key="item.id"
              :label="`${item.code} · ${item.name}`"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-model="statusFilter"
            class="purchase-filter"
            placeholder="全部状态"
            @change="search"
          >
            <el-option label="全部状态" value="" />
            <el-option label="进行中" value="IN_PROGRESS" />
            <el-option label="已完成" value="FINISHED" />
            <el-option label="已中止" value="ABORTED" />
          </el-select>
          <el-select
            v-model="auditFilter"
            class="purchase-filter"
            placeholder="全部审核状态"
            @change="search"
          >
            <el-option label="全部审核状态" value="" />
            <el-option label="待审核" value="PENDING" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已驳回" value="REJECTED" />
          </el-select>
          <el-button @click="search">查询</el-button>
        </div>
        <div class="purchase-toolbar__actions">
          <el-button v-if="canCreate" type="primary" @click="openCreate">
            新建采购计划
          </el-button>
        </div>
      </div>

      <DataTable
        v-model:page="page"
        v-model:page-size="pageSize"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        :total="total"
        :sync-query="false"
        empty-description="暂无采购计划"
        empty-action-text="重置筛选"
        :show-empty-action="hasFilters"
        @query-change="load"
        @empty-action="resetAndLoad"
      >
        <template #planDate="{ row }">
          {{ formatDateTime(row.plan_date) }}
        </template>
        <template #totalQty="{ row }">{{ row.total_qty }}</template>
        <template #totalAmount="{ row }">
          <MoneyText :value="row.total_amount" />
        </template>
        <template #status="{ row }">
          <StatusTag
            :status="row.status"
            :label="labelOf(PURCHASE_STATUS_LABELS, row.status)"
            :tone="statusTone(row.status)"
          />
        </template>
        <template #audit="{ row }">
          <StatusTag
            :status="row.audit_status"
            :label="labelOf(AUDIT_STATUS_LABELS, row.audit_status)"
            :tone="statusTone(row.audit_status)"
          />
        </template>
        <template #createdAt="{ row }">
          {{ formatDateTime(row.created_at) }}
        </template>
        <template #actions="{ row }">
          <div class="settings-page__actions">
            <el-button link type="primary" @click="openDetail(row)">
              详情
            </el-button>
            <el-button
              v-if="canCreate && isEditable(row)"
              link
              type="danger"
              @click="remove(row)"
            >
              删除
            </el-button>
          </div>
        </template>
      </DataTable>
    </section>

    <PurchasePlanFormDialog
      ref="planFormRef"
      v-model:visible="createVisible"
      :supplier-options="supplierOptions"
      :loading="saving"
      @submit="createPlan"
    />

    <el-dialog
      v-model="assistantConflictVisible"
      title="采购计划表单已有未保存内容"
      width="min(560px, 92vw)"
      :show-close="false"
      :close-on-click-modal="false"
    >
      <p>
        选择如何处理当前表单与 AI 草稿。合并时保留当前已有字段和商品值，仅补入缺失项；替换会丢弃当前未保存内容。
      </p>
      <template #footer>
        <el-button @click="keepCurrentAssistantForm">保留当前表单</el-button>
        <el-button @click="applyQueuedAssistantDraft('merge')">合并草稿</el-button>
        <el-button type="warning" @click="applyQueuedAssistantDraft('replace')">
          替换为草稿
        </el-button>
      </template>
    </el-dialog>

    <PurchaseDetailDrawer
      v-model:visible="detailVisible"
      title="采购计划详情"
      :document="detail"
      :document-no="detail?.plan_no"
      :loading="detailLoading"
    >
      <template #summary>
        <el-descriptions-item label="供应商">
          {{ detail?.supplier_name || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="计划采购时间">
          {{ formatDateTime(detail?.plan_date) }}
        </el-descriptions-item>
        <el-descriptions-item label="采购总数量">
          {{ detail?.total_qty || '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="采购金额">
          <MoneyText :value="detail?.total_amount" />
        </el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">
          {{ detail?.remark || '--' }}
        </el-descriptions-item>
      </template>
      <template #items>
        <section class="purchase-detail-section">
          <div class="purchase-detail-section__heading">
            <h3 class="purchase-detail-section__title">计划明细</h3>
            <el-button
              v-if="canCreate && detail && isEditable(detail)"
              link
              type="primary"
              @click="openBatchAdjust"
            >
              批量调整选中明细
            </el-button>
          </div>
          <el-table
            :data="detail?.items || []"
            row-key="id"
            border
            max-height="440"
            @selection-change="selectedPlanItems = $event"
          >
            <el-table-column
              v-if="canCreate && detail && isEditable(detail)"
              type="selection"
              width="46"
            />
            <el-table-column prop="product_code" label="商品编码" min-width="120" />
            <el-table-column prop="product_name" label="商品名称" min-width="180" />
            <el-table-column label="规格" min-width="130">
              <template #default="{ row }">
                {{ row.spec_code || row.spec_name || '商品级' }}
              </template>
            </el-table-column>
            <el-table-column prop="planned_qty" label="计划数量" width="100" align="right" />
            <el-table-column prop="purchased_qty" label="已采购" width="100" align="right" />
            <el-table-column prop="remaining_qty" label="剩余数量" width="100" align="right" />
            <el-table-column label="采购价" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.purchase_price" /></template>
            </el-table-column>
            <el-table-column label="金额" width="120" align="right">
              <template #default="{ row }"><MoneyText :value="row.amount" /></template>
            </el-table-column>
          </el-table>
        </section>
        <section v-if="detail?.attachments?.length" class="purchase-detail-section">
          <div class="purchase-detail-section__heading"><h3 class="purchase-detail-section__title">业务附件</h3></div>
          <div v-for="attachment in detail.attachments" :key="attachment.id" class="purchase-plan-attachment">
            <span>{{ attachment.display_name }} · {{ Math.ceil(attachment.size / 1024) }} KiB</span>
            <el-button link type="primary" @click="downloadPlanAttachment(attachment.id, attachment.display_name)">下载</el-button>
          </div>
        </section>
      </template>
      <template #footer>
        <div class="purchase-detail-actions">
          <el-button
            v-if="canCreate && detail && isEditable(detail)"
            @click="openHeaderEdit"
          >
            编辑计划信息
          </el-button>
          <el-button
            v-if="canCreate && detail && canSubmit(detail)"
            type="primary"
            :loading="actionLoading"
            @click="runAction(detail, 'submit')"
          >
            提交审批
          </el-button>
          <el-button
            v-if="canApprove && detail && canAudit(detail)"
            type="success"
            :loading="actionLoading"
            @click="runAction(detail, 'approve')"
          >
            审核通过
          </el-button>
          <el-button
            v-if="canApprove && detail && canAudit(detail)"
            type="danger"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'reject')"
          >
            审核不通过
          </el-button>
          <el-button
            v-if="canUnaudit && detail && detail.audit_status === 'APPROVED'"
            type="warning"
            plain
            :loading="actionLoading"
            @click="runAction(detail, 'unaudit')"
          >
            弃审
          </el-button>
          <el-button
            v-if="canCreate && detail && canTransfer(detail)"
            type="primary"
            plain
            @click="openOrderFromPlan"
          >
            转采购订单
          </el-button>
          <el-button
            v-if="canCreate && detail && detail.status === 'IN_PROGRESS'"
            type="warning"
            plain
            @click="runAction(detail, 'abort')"
          >
            中止计划
          </el-button>
          <el-button
            v-if="canCreate && detail && canFinish(detail)"
            type="success"
            plain
            @click="runAction(detail, 'finish')"
          >
            标记完成
          </el-button>
        </div>
      </template>
    </PurchaseDetailDrawer>

    <FormDialog
      v-model:visible="headerEditVisible"
      title="编辑采购计划"
      :loading="saving"
      @submit="submitHeaderEdit"
    >
      <el-form-item label="计划采购时间" required>
        <el-date-picker
          v-model="headerForm.plan_date"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ss"
        />
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="headerForm.remark"
          type="textarea"
          :rows="3"
          maxlength="255"
          show-word-limit
        />
      </el-form-item>
    </FormDialog>

    <FormDialog
      v-model:visible="batchVisible"
      title="批量调整计划明细"
      :loading="saving"
      @submit="submitBatchAdjust"
    >
      <p class="purchase-dialog-tip">
        将统一覆盖选中 {{ selectedPlanItems.length }} 条明细的数量和采购价。
        未填写的字段保持原值。
      </p>
      <el-form-item label="计划采购数量">
        <el-input v-model="batchForm.planned_qty" inputmode="decimal" />
      </el-form-item>
      <el-form-item label="采购价">
        <el-input v-model="batchForm.purchase_price" inputmode="decimal" />
      </el-form-item>
    </FormDialog>

    <PurchaseOrderFormDialog
      ref="orderFormRef"
      v-model:visible="orderVisible"
      :supplier-options="supplierOptions"
      :warehouse-options="warehouseOptions"
      :loading="saving"
      @submit="submitOrderFromPlan"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { partyApi, type PartyRecord } from '@/api/party'
import {
  purchaseOrderApi,
  purchasePlanApi,
  purchaseReferenceApi,
  type PurchaseOrderCreatePayload,
  type PurchaseOrderFromPlanPayload,
  type PurchasePlan,
  type PurchasePlanCreatePayload,
  type PurchasePlanItem,
  type WarehouseOption,
} from '@/api/purchase'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import MoneyText from '@/components/MoneyText.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'
import { useAuthStore } from '@/stores/auth'
import { useAssistantPurchasePlanDraftStore } from '@/stores/assistantPurchasePlanDraft'
import { useQuickEntryStore } from '@/stores/quickEntry'
import { assistantApi, type AssistantPurchasePlanDraft } from '@/api/assistant'
import { formatDateTime } from '@/utils/datetime'
import SettingsPageHeader from '@/views/settings/components/SettingsPageHeader.vue'
import PurchaseDetailDrawer from '@/views/purchase/components/PurchaseDetailDrawer.vue'
import PurchaseOrderFormDialog from '@/views/purchase/components/PurchaseOrderFormDialog.vue'
import PurchasePlanFormDialog from '@/views/purchase/components/PurchasePlanFormDialog.vue'
import {
  AUDIT_STATUS_LABELS,
  PURCHASE_STATUS_LABELS,
  labelOf,
  statusTone,
  toDateTimeInput,
} from '@/views/purchase/purchaseHelpers'
import '@/views/settings/settings.css'
import '@/views/purchase/purchase.css'

const authStore = useAuthStore()
const assistantDraftStore = useAssistantPurchasePlanDraftStore()
const quickEntryStore = useQuickEntryStore()
const canCreate = computed(() => authStore.hasPermission('purchase:create'))
const canApprove = computed(() => authStore.hasPermission('purchase:approve'))
const canUnaudit = computed(
  () => canCreate.value || canApprove.value,
)

const columns: DataTableColumn<PurchasePlan>[] = [
  { prop: 'plan_no', label: '计划编号', minWidth: 170 },
  { prop: 'supplier_name', label: '供应商', minWidth: 160 },
  { label: '计划采购时间', slot: 'planDate', minWidth: 160 },
  { label: '采购总数量', slot: 'totalQty', width: 110, align: 'right' },
  { label: '采购金额', slot: 'totalAmount', width: 130, align: 'right' },
  { label: '计划状态', slot: 'status', width: 100, align: 'center' },
  { label: '审核状态', slot: 'audit', width: 100, align: 'center' },
  { label: '制单时间', slot: 'createdAt', minWidth: 160 },
  { label: '操作', slot: 'actions', width: 150, fixed: 'right' },
]

const rows = ref<PurchasePlan[]>([])
const supplierOptions = ref<PartyRecord[]>([])
const warehouseOptions = ref<WarehouseOption[]>([])
const loading = ref(false)
const saving = ref(false)
const actionLoading = ref(false)
const detailLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const supplierFilter = ref<number | null>(null)
const statusFilter = ref('')
const auditFilter = ref('')
const createVisible = ref(false)
const assistantConflictVisible = ref(false)
const queuedAssistantDraft = ref<AssistantPurchasePlanDraft | null>(null)
const assistantSourceDraftId = ref<string | null>(null)
const assistantSourceDraftVersion = ref(0)
const assistantAttachSourceOnConfirm = ref(false)
const assistantRequestId = ref('')
const assistantRequestFingerprint = ref('')
const unknownAssistantRequest = ref<{
  requestId: string
  fingerprint: string
} | null>(assistantDraftStore.getUnresolvedCreation(currentIdentity()))
const detailVisible = ref(false)
const headerEditVisible = ref(false)
const batchVisible = ref(false)
const orderVisible = ref(false)
const detail = ref<PurchasePlan | null>(null)
const selectedPlanItems = ref<PurchasePlanItem[]>([])
const planFormRef = ref<InstanceType<typeof PurchasePlanFormDialog>>()
const orderFormRef = ref<InstanceType<typeof PurchaseOrderFormDialog>>()
const headerForm = reactive({ plan_date: '', remark: '' })
const batchForm = reactive({ planned_qty: '', purchase_price: '' })

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    supplierFilter.value !== null ||
    Boolean(statusFilter.value) ||
    Boolean(auditFilter.value),
)

function isEditable(plan: PurchasePlan): boolean {
  return (
    plan.status === 'IN_PROGRESS' &&
    ['PENDING', 'REJECTED'].includes(plan.audit_status)
  )
}

function canSubmit(plan: PurchasePlan): boolean {
  return plan.status === 'IN_PROGRESS' && plan.audit_status !== 'APPROVED'
}

function canAudit(plan: PurchasePlan): boolean {
  return plan.status === 'IN_PROGRESS' && plan.audit_status === 'PENDING'
}

function canTransfer(plan: PurchasePlan): boolean {
  return plan.status === 'IN_PROGRESS' && plan.audit_status === 'APPROVED'
}

function canFinish(plan: PurchasePlan): boolean {
  return (
    canTransfer(plan) &&
    Boolean(plan.items?.length) &&
    plan.items?.every((item) => Number(item.remaining_qty) <= 0) === true
  )
}

async function loadOptions(): Promise<void> {
  try {
    const suppliers = await partyApi.listSuppliers({
      is_active: true,
      page_size: 200,
    })
    supplierOptions.value = suppliers.items
    if (authStore.hasPermission('module:stock')) {
      warehouseOptions.value = await purchaseReferenceApi.listWarehouses()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购基础选项加载失败')
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await purchasePlanApi.list({
      keyword: keyword.value || undefined,
      supplier_id: supplierFilter.value || undefined,
      status: statusFilter.value || undefined,
      audit_status: auditFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划加载失败')
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
  supplierFilter.value = null
  statusFilter.value = ''
  auditFilter.value = ''
  search()
}

function openCreate(): void {
  planFormRef.value?.open(supplierFilter.value || undefined)
}

function currentIdentity(): string {
  return `${authStore.profile?.staff_id ?? ''}\u0000${authStore.token ?? ''}`
}

async function receiveAssistantDraft(
  draft: AssistantPurchasePlanDraft,
  source: 'assistant' | 'quick-entry' = 'assistant',
): Promise<void> {
  if (!canCreate.value || !draft.ready || !draft.payload) {
    ElMessage.warning('当前账号无权创建采购计划，或草稿尚未补齐')
    return
  }
  const unresolved = assistantDraftStore.getUnresolvedCreation(currentIdentity())
  if (unresolved && unresolved.draftId !== draft.draft_id) {
    ElMessage.warning('先确认上一张 AI 采购计划的保存结果，再处理新的草稿')
    return
  }
  await nextTick()
  const planForm = planFormRef.value
  if (!planForm) {
    ElMessage.error('采购计划表单尚未就绪，草稿仍保存在本人会话中')
    return
  }
  if (source === 'quick-entry' && createVisible.value && planForm.hasUnsavedContent()) {
    ElMessage.warning('采购计划表单有未保存内容，请先处理后再导入快捷草稿')
    return
  }
  if (createVisible.value && planForm.hasUnsavedContent()) {
    queuedAssistantDraft.value = draft
    assistantConflictVisible.value = true
    return
  }
  if (!createVisible.value) planForm.open(supplierFilter.value || undefined)
  try {
    await planForm.applyAssistantDraft(draft, 'replace', source)
    assistantSourceDraftId.value = source === 'assistant' ? draft.draft_id : null
    assistantSourceDraftVersion.value = draft.version
    assistantAttachSourceOnConfirm.value = Boolean(draft.attach_source_on_confirm)
    assistantRequestId.value = unresolved?.requestId || ''
    assistantRequestFingerprint.value = unresolved?.fingerprint || ''
    unknownAssistantRequest.value = unresolved
      ? { requestId: unresolved.requestId, fingerprint: unresolved.fingerprint }
      : null
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划草稿预填失败')
  }
}

async function applyQueuedAssistantDraft(mode: 'merge' | 'replace'): Promise<void> {
  const draft = queuedAssistantDraft.value
  if (!draft) return
  try {
    await planFormRef.value?.applyAssistantDraft(draft, mode)
    assistantSourceDraftId.value = draft.draft_id
    assistantSourceDraftVersion.value = draft.version
    assistantAttachSourceOnConfirm.value = Boolean(draft.attach_source_on_confirm)
    const unresolved = assistantDraftStore.getUnresolvedCreation(currentIdentity())
    assistantRequestId.value = unresolved?.requestId || ''
    assistantRequestFingerprint.value = unresolved?.fingerprint || ''
    unknownAssistantRequest.value = unresolved
      ? { requestId: unresolved.requestId, fingerprint: unresolved.fingerprint }
      : null
    assistantConflictVisible.value = false
    queuedAssistantDraft.value = null
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划草稿预填失败')
  }
}

function keepCurrentAssistantForm(): void {
  assistantConflictVisible.value = false
  queuedAssistantDraft.value = null
  ElMessage.info('已保留当前表单，AI 草稿未填入')
}

watch(
  () => assistantDraftStore.pending,
  () => {
    const draft = assistantDraftStore.consume(currentIdentity())
    if (draft) void receiveAssistantDraft(draft)
  },
  { immediate: true, flush: 'post' },
)

watch(
  () => quickEntryStore.pending,
  () => {
    const draft = quickEntryStore.consume(currentIdentity())
    if (draft) void receiveAssistantDraft(draft, 'quick-entry')
  },
  { immediate: true, flush: 'post' },
)

watch(createVisible, (visible) => {
  if (visible) return
  assistantSourceDraftId.value = null
  assistantSourceDraftVersion.value = 0
  assistantAttachSourceOnConfirm.value = false
  assistantRequestId.value = ''
  assistantRequestFingerprint.value = ''
  const unresolved = assistantDraftStore.getUnresolvedCreation(currentIdentity())
  unknownAssistantRequest.value = unresolved
    ? { requestId: unresolved.requestId, fingerprint: unresolved.fingerprint }
    : null
})

async function createPlan(payload: PurchasePlanCreatePayload): Promise<void> {
  const requestPayload = JSON.stringify(payload)
  let requestId: string | undefined
  const unresolved = assistantDraftStore.getUnresolvedCreation(currentIdentity())
  if (
    unresolved &&
    (unresolved.draftId !== assistantSourceDraftId.value ||
      unresolved.fingerprint !== requestPayload)
  ) {
    ElMessage.warning('上一保存结果尚未确认。请先恢复原草稿和表单内容，再重试原请求。')
    return
  }
  if (assistantSourceDraftId.value) {
    const unknown = unresolved
      ? { requestId: unresolved.requestId, fingerprint: unresolved.fingerprint }
      : unknownAssistantRequest.value
    if (unknown && unknown.fingerprint !== requestPayload) {
      ElMessage.warning('上一保存结果尚未确认。请恢复原表单内容并重试同一请求，或先确认其创建状态。')
      return
    }
    if (unknown) {
      requestId = unknown.requestId
    } else {
      if (
        !assistantRequestId.value ||
        assistantRequestFingerprint.value !== requestPayload
      ) {
        assistantRequestId.value = crypto.randomUUID()
        assistantRequestFingerprint.value = requestPayload
      }
      requestId = assistantRequestId.value
    }
    assistantDraftStore.setUnresolvedCreation({
      identity: currentIdentity(),
      draftId: assistantSourceDraftId.value,
      requestId,
      fingerprint: requestPayload,
    })
  }
  saving.value = true
  try {
    const plan = assistantSourceDraftId.value
      ? await assistantApi.confirmDocumentDraft(assistantSourceDraftId.value, {
          draft_version: assistantSourceDraftVersion.value,
          request_id: requestId!,
          form: payload,
          attach_source: assistantAttachSourceOnConfirm.value,
        })
      : await purchasePlanApi.create(payload, { idempotencyKey: requestId })
    await finishPlanCreation(plan)
  } catch (error) {
    const status = typeof error === 'object' && error && 'status' in error
      ? (error as { status?: number }).status
      : undefined
    if (requestId) {
      try {
        const result = await purchasePlanApi.getCreationRequest(requestId)
        if (result.status === 'SUCCEEDED' && result.result) {
          await finishPlanCreation(result.result)
          return
        }
        if (result.status === 'FAILED') {
          assistantDraftStore.clearUnresolvedCreation(currentIdentity())
          unknownAssistantRequest.value = null
          ElMessage.error(result.message || '采购计划创建失败，表单内容已保留')
          return
        }
        if (status !== undefined && status < 500 && status !== 409) {
          assistantDraftStore.clearUnresolvedCreation(currentIdentity())
          unknownAssistantRequest.value = null
          ElMessage.error(error instanceof Error ? error.message : '采购计划创建失败，表单内容已保留')
          return
        }
        unknownAssistantRequest.value = { requestId, fingerprint: requestPayload }
        ElMessage.warning(
          result.status === 'PENDING'
            ? '保存请求仍在处理中。表单已保留，请稍后重试同一内容。'
            : '暂未查到已提交结果。表单已保留，请使用同一内容重试以避免重复创建。',
        )
        return
      } catch {
        if (status !== undefined && status < 500 && status !== 409) {
          assistantDraftStore.clearUnresolvedCreation(currentIdentity())
          unknownAssistantRequest.value = null
          ElMessage.error(error instanceof Error ? error.message : '采购计划创建失败，表单内容已保留')
          return
        }
        unknownAssistantRequest.value = { requestId, fingerprint: requestPayload }
        ElMessage.error('保存结果暂未确认，表单已保留。请恢复原内容并使用同一请求重试。')
        return
      }
    }
    if (requestId) assistantDraftStore.clearUnresolvedCreation(currentIdentity())
    ElMessage.error(error instanceof Error ? error.message : '采购计划创建失败，表单内容已保留')
  } finally {
    saving.value = false
  }
}

async function finishPlanCreation(plan: PurchasePlan): Promise<void> {
  ElMessage.success(`采购计划 ${plan.plan_no} 已生成，状态：${labelOf(PURCHASE_STATUS_LABELS, plan.status)}`)
  assistantSourceDraftId.value = null
  assistantSourceDraftVersion.value = 0
  assistantAttachSourceOnConfirm.value = false
  assistantRequestId.value = ''
  assistantRequestFingerprint.value = ''
  unknownAssistantRequest.value = null
  assistantDraftStore.clearUnresolvedCreation(currentIdentity())
  createVisible.value = false
  await load()
  await openDetail(plan)
}

async function openDetail(plan: PurchasePlan): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await purchasePlanApi.get(plan.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function downloadPlanAttachment(attachmentId: string, filename: string): Promise<void> {
  const planId = detail.value?.id
  if (!planId) return
  try {
    const blob = await purchasePlanApi.downloadAttachment(planId, attachmentId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '附件下载失败')
  }
}

async function refreshDetail(): Promise<void> {
  if (detail.value) {
    detail.value = await purchasePlanApi.get(detail.value.id)
  }
}

function openHeaderEdit(): void {
  if (!detail.value) {
    return
  }
  headerForm.plan_date = toDateTimeInput(detail.value.plan_date)
  headerForm.remark = detail.value.remark || ''
  headerEditVisible.value = true
}

async function submitHeaderEdit(): Promise<void> {
  if (!detail.value) {
    return
  }
  saving.value = true
  try {
    await purchasePlanApi.update(detail.value.id, {
      plan_date: headerForm.plan_date || null,
      remark: headerForm.remark.trim() || null,
    })
    ElMessage.success('采购计划已更新')
    headerEditVisible.value = false
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划更新失败')
  } finally {
    saving.value = false
  }
}

function openBatchAdjust(): void {
  if (!selectedPlanItems.value.length) {
    ElMessage.warning('请先选择需要调整的计划明细')
    return
  }
  batchForm.planned_qty = ''
  batchForm.purchase_price = ''
  batchVisible.value = true
}

async function submitBatchAdjust(): Promise<void> {
  if (!detail.value) {
    return
  }
  if (!batchForm.planned_qty && !batchForm.purchase_price) {
    ElMessage.warning('至少填写计划采购数量或采购价')
    return
  }
  saving.value = true
  try {
    await purchasePlanApi.batchUpdateItems(detail.value.id, {
      item_ids: selectedPlanItems.value.map((item) => item.id),
      planned_qty: batchForm.planned_qty || undefined,
      purchase_price: batchForm.purchase_price || null,
    })
    ElMessage.success('计划明细已批量更新')
    batchVisible.value = false
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '计划明细调整失败')
  } finally {
    saving.value = false
  }
}

async function runAction(
  plan: PurchasePlan,
  action: 'submit' | 'approve' | 'reject' | 'unaudit' | 'abort' | 'finish',
): Promise<void> {
  actionLoading.value = true
  try {
    if (action === 'submit') {
      await purchasePlanApi.submit(plan.id)
    } else if (action === 'approve') {
      const { value } = await ElMessageBox.prompt(
        '可填写审批意见',
        '审核通过',
        { inputPlaceholder: '可留空' },
      )
      await purchasePlanApi.approve(plan.id, value || undefined)
    } else if (action === 'reject') {
      const { value } = await ElMessageBox.prompt(
        '请填写不通过原因',
        '审核不通过',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写审批意见',
        },
      )
      await purchasePlanApi.reject(plan.id, value)
    } else if (action === 'unaudit') {
      const { value } = await ElMessageBox.prompt(
        '请填写弃审原因',
        '弃审采购计划',
        {
          inputValidator: (value) => Boolean(value?.trim()) || '请填写弃审原因',
        },
      )
      await purchasePlanApi.unaudit(plan.id, value)
    } else if (action === 'abort') {
      await ElMessageBox.confirm('确定中止当前采购计划吗？', '中止采购计划', {
        type: 'warning',
      })
      await purchasePlanApi.abort(plan.id)
    } else {
      await purchasePlanApi.finish(plan.id)
    }
    ElMessage.success('采购计划状态已更新')
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购计划操作失败')
    }
  } finally {
    actionLoading.value = false
  }
}

function openOrderFromPlan(): void {
  if (!detail.value) {
    return
  }
  orderFormRef.value?.openFromPlan(detail.value)
}

async function submitOrderFromPlan(
  payload: PurchaseOrderCreatePayload | PurchaseOrderFromPlanPayload,
): Promise<void> {
  if (!('plan_id' in payload)) {
    return
  }
  saving.value = true
  try {
    const order = await purchaseOrderApi.fromPlan(payload)
    ElMessage.success(`采购订单 ${order.order_no} 已生成`)
    orderVisible.value = false
    await Promise.all([load(), refreshDetail()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采购计划转单失败')
  } finally {
    saving.value = false
  }
}

async function remove(plan: PurchasePlan): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除采购计划 ${plan.plan_no} 吗？`, '删除采购计划', {
      type: 'warning',
    })
    await purchasePlanApi.remove(plan.id)
    ElMessage.success('采购计划已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '采购计划删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>

<style scoped>
.purchase-detail-section__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
