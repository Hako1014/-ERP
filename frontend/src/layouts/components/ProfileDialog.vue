<template>
  <el-dialog v-model="visible" title="个人资料" width="min(720px, 94vw)" append-to-body>
    <el-skeleton v-if="loading && !profile" :rows="5" animated />
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <template v-if="profile">
      <section class="profile-dialog__avatar-row" aria-label="头像设置">
        <el-avatar :size="76" :src="avatarUrl || undefined">{{ avatarText }}</el-avatar>
        <div>
          <strong>个人头像</strong>
          <p>仅支持 JPEG、PNG 或 WebP，最大 5 MiB；图片会移除 EXIF 等元数据。</p>
          <input
            ref="avatarInput"
            class="profile-dialog__file-input"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="选择头像文件"
            @change="uploadAvatar"
          />
          <el-button :loading="uploadingAvatar" :disabled="loading" @click="avatarInput?.click()">
            更换头像
          </el-button>
          <el-button v-if="profile.avatar_version" text type="danger" :disabled="uploadingAvatar" @click="removeAvatar">
            移除头像
          </el-button>
        </div>
      </section>

      <el-divider content-position="left">只读账号与组织信息</el-divider>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="正式姓名">{{ profile.name }}</el-descriptions-item>
        <el-descriptions-item label="员工编号">{{ profile.staff_no || '—' }}</el-descriptions-item>
        <el-descriptions-item label="登录账号">{{ profile.login_account || '—' }}</el-descriptions-item>
        <el-descriptions-item label="登录/找回手机">{{ profile.phone_masked || '未设置' }}</el-descriptions-item>
        <el-descriptions-item label="公司">{{ profile.company_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="部门">{{ profile.department_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="职务">{{ profile.position_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ profile.roles.join('、') || '—' }}</el-descriptions-item>
        <el-descriptions-item label="数据范围">{{ dataScopeLabel }}</el-descriptions-item>
        <el-descriptions-item label="账号状态">{{ profile.is_active ? '启用' : '停用' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">可编辑资料</el-divider>
      <el-form label-position="top" class="profile-dialog__form">
        <el-form-item label="显示名称" help="只影响顶栏称呼，不会修改正式姓名或单据责任人">
          <el-input v-model="form.display_name" maxlength="64" show-word-limit :disabled="saving" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="form.gender" clearable placeholder="未填写" :disabled="saving">
            <el-option v-for="item in genderOptions" :key="item" :label="item" :value="item" />
            <el-option v-if="form.gender && !genderOptions.includes(form.gender)" :label="form.gender" :value="form.gender" />
          </el-select>
        </el-form-item>
        <el-form-item label="地区">
          <el-input v-model="form.region" maxlength="64" show-word-limit :disabled="saving" placeholder="可选" />
        </el-form-item>
      </el-form>
    </template>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button v-if="profile" type="primary" :loading="saving" :disabled="loading || uploadingAvatar" @click="saveProfile">
        保存资料
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { profileApi, type MyProfile } from '@/api/profile'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const authStore = useAuthStore()
const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const identity = computed(() => authStore.token && authStore.profile?.staff_id
  ? `${authStore.profile.staff_id}\u0000${authStore.token}`
  : '')
const profile = ref<MyProfile | null>(null)
const avatarUrl = ref('')
const avatarInput = ref<HTMLInputElement>()
const loading = ref(false)
const saving = ref(false)
const uploadingAvatar = ref(false)
const errorMessage = ref('')
const form = reactive({ display_name: '', gender: '', region: '' })
const genderOptions = ['女', '男', '其他']
const avatarText = computed(() => (form.display_name || profile.value?.name || '用').slice(0, 1))
const dataScopeLabel = computed(() => ({ SELF: '仅本人', DEPARTMENT: '本部门', COMPANY: '全公司' }[profile.value?.data_scope || ''] || '—'))

function revokeAvatarUrl(): void {
  if (avatarUrl.value) URL.revokeObjectURL(avatarUrl.value)
  avatarUrl.value = ''
}

async function refreshAvatar(expectedIdentity: string, expectedVersion: string | null): Promise<void> {
  if (!expectedVersion) {
    revokeAvatarUrl()
    return
  }
  try {
    const data = await profileApi.getAvatar()
    if (identity.value !== expectedIdentity || profile.value?.avatar_version !== expectedVersion) return
    revokeAvatarUrl()
    avatarUrl.value = URL.createObjectURL(data)
  } catch {
    if (identity.value === expectedIdentity) revokeAvatarUrl()
  }
}

async function loadProfile(): Promise<void> {
  const expectedIdentity = identity.value
  const expectedToken = authStore.token
  if (!expectedIdentity || !expectedToken) return
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await profileApi.getMine()
    if (identity.value !== expectedIdentity || !authStore.applySelfProfile(result, expectedToken)) return
    profile.value = result
    form.display_name = result.display_name
    form.gender = result.gender || ''
    form.region = result.region || ''
    void refreshAvatar(expectedIdentity, result.avatar_version)
  } catch (error) {
    if (identity.value === expectedIdentity) errorMessage.value = error instanceof Error ? error.message : '个人资料加载失败，请重试。'
  } finally {
    if (identity.value === expectedIdentity) loading.value = false
  }
}

watch([visible, identity], ([isVisible]) => {
  if (!isVisible) return
  profile.value = null
  form.display_name = ''
  form.gender = ''
  form.region = ''
  revokeAvatarUrl()
  void loadProfile()
}, { immediate: true })

watch(() => profile.value?.avatar_version, (version) => {
  if (visible.value && profile.value) void refreshAvatar(identity.value, version || null)
})

async function saveProfile(): Promise<void> {
  const expectedIdentity = identity.value
  const expectedToken = authStore.token
  if (!profile.value || !expectedIdentity || !expectedToken) return
  saving.value = true
  errorMessage.value = ''
  try {
    const result = await profileApi.updateMine({
      display_name: form.display_name.trim() || null,
      gender: form.gender || null,
      region: form.region.trim() || null,
    })
    if (identity.value !== expectedIdentity || !authStore.applySelfProfile(result, expectedToken)) return
    profile.value = result
    form.display_name = result.display_name
    form.gender = result.gender || ''
    form.region = result.region || ''
    ElMessage.success('个人资料已保存')
  } catch (error) {
    if (identity.value === expectedIdentity) errorMessage.value = error instanceof Error ? error.message : '保存失败，已保留本次输入。'
  } finally {
    if (identity.value === expectedIdentity) saving.value = false
  }
}

async function uploadAvatar(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const expectedIdentity = identity.value
  const expectedToken = authStore.token
  errorMessage.value = ''
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    errorMessage.value = '请选择 JPEG、PNG 或 WebP 图片，且文件不超过 5 MiB。'
    return
  }
  uploadingAvatar.value = true
  try {
    const result = await profileApi.uploadAvatar(file)
    if (identity.value !== expectedIdentity || !expectedToken || !authStore.applySelfProfile(result, expectedToken)) return
    profile.value = result
    ElMessage.success('头像已更新')
  } catch (error) {
    if (identity.value === expectedIdentity) errorMessage.value = error instanceof Error ? error.message : '头像上传失败，请重试。'
  } finally {
    if (identity.value === expectedIdentity) uploadingAvatar.value = false
    input.value = ''
  }
}

async function removeAvatar(): Promise<void> {
  const expectedIdentity = identity.value
  const expectedToken = authStore.token
  if (!profile.value || !expectedToken) return
  uploadingAvatar.value = true
  errorMessage.value = ''
  try {
    const result = await profileApi.deleteAvatar()
    if (identity.value !== expectedIdentity || !authStore.applySelfProfile(result, expectedToken)) return
    profile.value = result
    revokeAvatarUrl()
    ElMessage.success('头像已移除')
  } catch (error) {
    if (identity.value === expectedIdentity) errorMessage.value = error instanceof Error ? error.message : '头像移除失败，请重试。'
  } finally {
    if (identity.value === expectedIdentity) uploadingAvatar.value = false
  }
}

onBeforeUnmount(revokeAvatarUrl)
</script>

<style scoped>
.profile-dialog__avatar-row { display: flex; align-items: center; gap: 18px; margin-bottom: 18px; }
.profile-dialog__avatar-row p { margin: 6px 0 10px; color: var(--el-text-color-secondary); font-size: 13px; }
.profile-dialog__file-input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.profile-dialog__form { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.profile-dialog__form :deep(.el-form-item:first-child) { grid-column: 1 / -1; }
@media (max-width: 560px) { .profile-dialog__form { display: block; } }
</style>
