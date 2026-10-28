<template>
  <el-dialog
    v-model="visible"
    title="组织人员"
    width="min(920px, 96vw)"
    append-to-body
    class="organization-directory-dialog"
  >
    <div class="organization-directory__filters">
      <el-input
        v-model="keyword"
        clearable
        maxlength="64"
        placeholder="按姓名或部门搜索"
        aria-label="按姓名或部门搜索"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <el-tree-select
        v-model="departmentId"
        :data="departments"
        :props="treeProps"
        node-key="id"
        clearable
        check-strictly
        default-expand-all
        placeholder="全部部门"
        aria-label="按部门筛选"
        @change="applyFilters"
      />
      <el-button type="primary" :loading="loading" @click="applyFilters">查询</el-button>
    </div>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon>
      <template #default>
        <el-button link type="primary" @click="retry">重试</el-button>
      </template>
    </el-alert>

    <el-skeleton v-if="loading && people.length === 0" :rows="5" animated />
    <el-empty v-else-if="!loading && !errorMessage && people.length === 0" description="暂无符合条件的在职人员" />
    <div v-else class="organization-directory__content">
      <section class="organization-directory__list" aria-label="同公司在职人员">
        <button
          v-for="person in people"
          :key="person.staff_id"
          type="button"
          class="organization-directory__person"
          :class="{ 'is-active': selectedId === person.staff_id }"
          @click="selectPerson(person)"
        >
          <el-avatar :size="36">{{ person.name.slice(0, 1) }}</el-avatar>
          <span class="organization-directory__person-copy">
            <strong>{{ person.name }}</strong>
            <small>{{ person.department_name || '未分配部门' }} · {{ person.position_name || '未设置职务' }}</small>
          </span>
        </button>
        <el-pagination
          v-if="total > pageSize"
          v-model:current-page="page"
          layout="prev, pager, next"
          :page-size="pageSize"
          :total="total"
          small
          @current-change="loadPeople"
        />
      </section>

      <section class="organization-directory__detail" aria-label="人员详情">
        <el-skeleton v-if="detailLoading" :rows="4" animated />
        <el-empty v-else-if="detailError" :description="detailError" />
        <el-empty v-else-if="!selectedPerson" description="选择一位人员查看可见资料" />
        <template v-else>
          <el-avatar :size="76" :src="avatarUrl || undefined">{{ selectedPerson.name.slice(0, 1) }}</el-avatar>
          <h3>{{ selectedPerson.name }}</h3>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="部门">{{ selectedPerson.department_name || '未分配' }}</el-descriptions-item>
            <el-descriptions-item label="职务">{{ selectedPerson.position_name || '未设置' }}</el-descriptions-item>
          </el-descriptions>
          <p class="organization-directory__privacy">仅展示同公司目录信息，不包含登录账号、私人联系方式或权限配置。</p>
        </template>
      </section>
    </div>

    <template #footer>
      <el-button v-if="canManage" @click="openManagement">员工管理</el-button>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import {
  organizationApi,
  type OrganizationDepartment,
  type OrganizationPerson,
} from '@/api/organization'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const router = useRouter()
const authStore = useAuthStore()
const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const identity = computed(() => authStore.token && authStore.profile?.staff_id
  ? `${authStore.profile.staff_id}\u0000${authStore.token}`
  : '')
const canManage = computed(() => authStore.hasPermission('settings:manage'))
const keyword = ref('')
const departmentId = ref<number>()
const departments = ref<OrganizationDepartment[]>([])
const people = ref<OrganizationPerson[]>([])
const selectedPerson = ref<OrganizationPerson | null>(null)
const selectedId = ref<number | null>(null)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const detailLoading = ref(false)
const errorMessage = ref('')
const detailError = ref('')
const avatarUrl = ref('')
const treeProps = { label: 'name', children: 'children', value: 'id' }
let listRequest = 0
let detailRequest = 0

function buildDepartmentTree(rows: OrganizationDepartment[]): OrganizationDepartment[] {
  const nodes = new Map(rows.map((item) => [item.id, { ...item, children: [] as OrganizationDepartment[] }]))
  const roots: OrganizationDepartment[] = []
  for (const item of rows) {
    const node = nodes.get(item.id)!
    const parent = item.parent_id ? nodes.get(item.parent_id) : undefined
    if (parent && parent.id !== node.id) parent.children?.push(node)
    else roots.push(node)
  }
  return roots.length ? roots : [...nodes.values()].map(({ children: _children, ...item }) => item)
}

function revokeAvatar(): void {
  if (avatarUrl.value) URL.revokeObjectURL(avatarUrl.value)
  avatarUrl.value = ''
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : '目录暂时无法加载，请重试。'
}

async function loadPeople(): Promise<void> {
  const expectedIdentity = identity.value
  const requestId = ++listRequest
  if (!expectedIdentity) return
  loading.value = true
  errorMessage.value = ''
  selectedPerson.value = null
  selectedId.value = null
  detailError.value = ''
  revokeAvatar()
  try {
    const result = await organizationApi.listPeople({
      q: keyword.value.trim() || undefined,
      department_id: departmentId.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    if (identity.value !== expectedIdentity || requestId !== listRequest) return
    people.value = result.items
    total.value = result.total
    if (result.items[0]) await selectPerson(result.items[0])
  } catch (error) {
    if (identity.value === expectedIdentity && requestId === listRequest) errorMessage.value = errorText(error)
  } finally {
    if (identity.value === expectedIdentity && requestId === listRequest) loading.value = false
  }
}

async function selectPerson(person: OrganizationPerson): Promise<void> {
  const expectedIdentity = identity.value
  const requestId = ++detailRequest
  if (!expectedIdentity) return
  selectedId.value = person.staff_id
  selectedPerson.value = null
  detailError.value = ''
  detailLoading.value = true
  revokeAvatar()
  try {
    const result = await organizationApi.getPerson(person.staff_id)
    if (identity.value !== expectedIdentity || requestId !== detailRequest || selectedId.value !== person.staff_id) return
    selectedPerson.value = result
    if (!result.avatar_available) return
    const image = await organizationApi.getAvatar(person.staff_id)
    if (identity.value !== expectedIdentity || requestId !== detailRequest || selectedId.value !== person.staff_id) return
    avatarUrl.value = URL.createObjectURL(image)
  } catch (error) {
    if (identity.value === expectedIdentity && requestId === detailRequest) {
      if (selectedPerson.value) return // 头像失效不应隐藏仍有效的目录详情。
      detailError.value = errorText(error)
    }
  } finally {
    if (identity.value === expectedIdentity && requestId === detailRequest) detailLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  const expectedIdentity = identity.value
  if (!expectedIdentity) return
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await organizationApi.listDepartments()
    if (identity.value !== expectedIdentity) return
    departments.value = buildDepartmentTree(result.items)
    await loadPeople()
  } catch (error) {
    if (identity.value === expectedIdentity) errorMessage.value = errorText(error)
  } finally {
    if (identity.value === expectedIdentity) loading.value = false
  }
}

function applyFilters(): void {
  page.value = 1
  void loadPeople()
}

function retry(): void {
  void loadAll()
}

function openManagement(): void {
  if (!canManage.value) return
  visible.value = false
  void router.push({ name: 'settings-company' })
}

watch([visible, identity], ([isVisible]) => {
  if (!isVisible) return
  keyword.value = ''
  departmentId.value = undefined
  page.value = 1
  departments.value = []
  people.value = []
  total.value = 0
  selectedPerson.value = null
  selectedId.value = null
  revokeAvatar()
  void loadAll()
}, { immediate: true })

onBeforeUnmount(revokeAvatar)
</script>

<style scoped>
.organization-directory__filters { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(160px, 220px) auto; gap: 10px; margin-bottom: 16px; }
.organization-directory__content { display: grid; grid-template-columns: minmax(280px, 1.1fr) minmax(250px, .9fr); min-height: 360px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; overflow: hidden; }
.organization-directory__list { display: grid; align-content: start; gap: 4px; padding: 8px; border-right: 1px solid var(--el-border-color-lighter); }
.organization-directory__person { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px; border: 0; border-radius: 6px; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.organization-directory__person:hover, .organization-directory__person.is-active { background: var(--el-fill-color-light); }
.organization-directory__person-copy { display: grid; gap: 4px; min-width: 0; }
.organization-directory__person-copy strong, .organization-directory__person-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.organization-directory__person-copy small, .organization-directory__privacy { color: var(--el-text-color-secondary); font-size: 12px; }
.organization-directory__detail { display: grid; align-content: start; justify-items: center; gap: 14px; padding: 24px; }
.organization-directory__detail h3 { margin: 0; }
.organization-directory__detail :deep(.el-descriptions) { width: 100%; }
.organization-directory__privacy { margin: 0; text-align: center; }
.organization-directory__list :deep(.el-pagination) { justify-content: center; margin-top: 8px; }
@media (max-width: 680px) { .organization-directory__filters { grid-template-columns: 1fr; } .organization-directory__content { grid-template-columns: 1fr; } .organization-directory__list { border-right: 0; border-bottom: 1px solid var(--el-border-color-lighter); } }
</style>
