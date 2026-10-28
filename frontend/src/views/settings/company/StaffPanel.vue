<template>
  <section class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>员工与登录账户</h2>
      <div class="settings-page__toolbar-actions">
        <el-input
          v-model="keyword"
          class="settings-page__search"
          clearable
          placeholder="姓名、工号、账户或手机号"
          @keyup.enter="search"
          @clear="search"
        />
        <el-button @click="search">查询</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">
          新增员工
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
      empty-description="暂无员工"
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
      <template #distribution="{ row }">
        {{ row.is_distribution ? '是' : '否' }}
      </template>
      <template #actions="{ row }">
        <div v-if="canManage" class="settings-page__actions">
          <el-button link type="primary" @click="openEdit(row)">
            编辑
          </el-button>
          <el-button link type="primary" @click="openPassword(row)">
            重置密码
          </el-button>
          <el-button link type="primary" @click="openScope(row)">
            数据范围
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

    <FormDialog
      v-model:visible="dialogVisible"
      :title="editing ? '编辑员工' : '新增员工'"
      :width="760"
      :loading="saving"
      @submit="submit"
    >
      <div class="settings-page__form-grid">
        <el-form-item label="姓名" required>
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="工号">
          <el-input v-model="form.staff_no" maxlength="32" />
        </el-form-item>
        <el-form-item label="登录账户">
          <el-input v-model="form.login_account" maxlength="64" />
        </el-form-item>
        <el-form-item label="手机号码">
          <el-input v-model="form.phone" maxlength="32" />
        </el-form-item>
        <el-form-item v-if="!editing" label="初始密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            maxlength="128"
          />
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="form.department_id" clearable filterable>
            <el-option
              v-for="item in departments"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="职务">
          <el-select v-model="form.position_id" clearable filterable>
            <el-option
              v-for="item in positions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="form.gender" clearable>
            <el-option label="男" value="男" />
            <el-option label="女" value="女" />
            <el-option label="保密" value="保密" />
          </el-select>
        </el-form-item>
        <el-form-item label="地区">
          <el-input v-model="form.region" maxlength="64" />
        </el-form-item>
        <el-form-item label="分销业务">
          <el-switch v-model="form.is_distribution" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
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

    <FormDialog
      v-model:visible="scopeVisible"
      title="设置数据范围"
      :width="480"
      :loading="saving"
      @submit="submitScope"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="未配置时默认仅本人；管理员始终可查看全公司。"
      />
      <el-form-item label="员工">
        <span>{{ scopeTarget?.name }}</span>
      </el-form-item>
      <el-form-item label="数据范围">
        <el-select v-model="scopeType">
          <el-option label="仅本人" value="SELF" />
          <el-option label="本部门" value="DEPARTMENT" />
          <el-option label="全公司" value="COMPANY" />
        </el-select>
      </el-form-item>
    </FormDialog>

    <FormDialog
      v-model:visible="passwordVisible"
      title="重置员工密码"
      :width="480"
      :loading="saving"
      @submit="submitPassword"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="重置后旧密码立即失效，请将新密码安全告知员工。"
      />
      <el-form-item label="员工">
        <span>{{ passwordTarget?.name }}</span>
      </el-form-item>
      <el-form-item label="新密码" required>
        <el-input
          v-model="newPassword"
          type="password"
          show-password
          maxlength="128"
        />
      </el-form-item>
    </FormDialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  companyApi,
  type Department,
  type DataScopeType,
  type Position,
  type Staff,
  type StaffPayload,
} from '@/api/company'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { DataTableColumn } from '@/components/types'

defineProps<{
  canManage: boolean
}>()

const columns: DataTableColumn<Staff>[] = [
  { prop: 'name', label: '姓名', minWidth: 110 },
  { prop: 'staff_no', label: '工号', minWidth: 110 },
  { prop: 'login_account', label: '登录账户', minWidth: 130 },
  { prop: 'phone', label: '手机', minWidth: 130 },
  { prop: 'department_name', label: '部门', minWidth: 120 },
  { prop: 'position_name', label: '职务', minWidth: 110 },
  {
    label: '分销业务',
    slot: 'distribution',
    width: 100,
    align: 'center',
  },
  { label: '状态', slot: 'status', width: 90, align: 'center' },
  { label: '操作', slot: 'actions', width: 360, fixed: 'right' },
]

const rows = ref<Staff[]>([])
const departments = ref<Department[]>([])
const positions = ref<Position[]>([])
const loading = ref(false)
const saving = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const dialogVisible = ref(false)
const passwordVisible = ref(false)
const scopeVisible = ref(false)
const editing = ref<Staff | null>(null)
const passwordTarget = ref<Staff | null>(null)
const scopeTarget = ref<Staff | null>(null)
const scopeType = ref<DataScopeType>('SELF')
const newPassword = ref('')
const form = reactive<StaffPayload>({
  name: '',
  staff_no: '',
  login_account: '',
  phone: '',
  password: '',
  department_id: null,
  position_id: null,
  gender: '',
  region: '',
  remark: '',
  is_distribution: false,
  is_active: true,
})

function resetForm(): void {
  Object.assign(form, {
    name: '',
    staff_no: '',
    login_account: '',
    phone: '',
    password: '',
    department_id: null,
    position_id: null,
    gender: '',
    region: '',
    remark: '',
    is_distribution: false,
    is_active: true,
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await companyApi.listStaff({
      keyword: keyword.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '员工加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions(): Promise<void> {
  try {
    const [departmentRows, positionRows] = await Promise.all([
      companyApi.listDepartments(),
      companyApi.listPositions(),
    ])
    departments.value = departmentRows
    positions.value = positionRows
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '部门职务加载失败')
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
  page.value = 1
  void load()
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Staff): void {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    staff_no: row.staff_no || '',
    login_account: row.login_account || '',
    phone: row.phone || '',
    password: '',
    department_id: row.department_id || null,
    position_id: row.position_id || null,
    gender: row.gender || '',
    region: row.region || '',
    remark: row.remark || '',
    is_distribution: row.is_distribution,
    is_active: row.is_active,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!String(form.name || '').trim()) {
    ElMessage.warning('请填写员工姓名')
    return
  }
  if (!editing.value && !/^(?=.*[A-Za-z])(?=.*\d).{8,64}$/.test(String(form.password || ''))) {
    ElMessage.warning('初始密码需为 8-64 位，且同时包含字母和数字')
    return
  }
  saving.value = true
  try {
    const payload = { ...form }
    if (editing.value) {
      delete payload.password
      await companyApi.updateStaff(editing.value.id, payload)
    } else {
      await companyApi.createStaff(payload)
    }
    ElMessage.success(editing.value ? '员工已更新' : '员工已创建')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '员工保存失败')
  } finally {
    saving.value = false
  }
}

function openPassword(row: Staff): void {
  passwordTarget.value = row
  newPassword.value = ''
  passwordVisible.value = true
}

async function openScope(row: Staff): Promise<void> {
  scopeTarget.value = row
  try {
    const data = await companyApi.getStaffDataScope(row.id)
    scopeType.value = data.scope_type
    scopeVisible.value = true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '数据范围加载失败')
  }
}

async function submitScope(): Promise<void> {
  if (!scopeTarget.value) return
  saving.value = true
  try {
    await companyApi.setStaffDataScope(scopeTarget.value.id, scopeType.value)
    ElMessage.success('数据范围已更新')
    scopeVisible.value = false
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '数据范围保存失败')
  } finally {
    saving.value = false
  }
}

async function submitPassword(): Promise<void> {
  if (!passwordTarget.value) {
    return
  }
  if (!/^(?=.*[A-Za-z])(?=.*\d).{8,64}$/.test(newPassword.value)) {
    ElMessage.warning('新密码需为 8-64 位，且同时包含字母和数字')
    return
  }
  saving.value = true
  try {
    await companyApi.resetPassword(passwordTarget.value.id, newPassword.value)
    ElMessage.success('密码已重置')
    passwordVisible.value = false
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '密码重置失败')
  } finally {
    saving.value = false
  }
}

async function toggleActive(row: Staff): Promise<void> {
  try {
    if (row.is_active) {
      await companyApi.disableStaff(row.id)
    } else {
      await companyApi.enableStaff(row.id)
    }
    ElMessage.success(row.is_active ? '员工已停用' : '员工已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '员工状态更新失败')
  }
}

async function remove(row: Staff): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除员工“${row.name}”吗？`, '删除员工', {
      type: 'warning',
    })
    await companyApi.deleteStaff(row.id)
    ElMessage.success('员工已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '员工删除失败')
    }
  }
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
})
</script>
