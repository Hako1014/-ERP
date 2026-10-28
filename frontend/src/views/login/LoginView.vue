<template>
  <AuthShell title="登录" description="使用登录账号进入系统">
    <el-form
      ref="formRef"
      class="auth-form"
      :model="form"
      :rules="rules"
      label-position="top"
      @submit.prevent
    >
      <el-form-item label="登录账号" prop="account">
        <el-input
          v-model="form.account"
          name="account"
          autocomplete="username"
          maxlength="64"
          clearable
          placeholder="请输入登录账号"
        />
      </el-form-item>

      <el-form-item label="登录密码" prop="password">
        <el-input
          v-model="form.password"
          name="password"
          type="password"
          autocomplete="current-password"
          show-password
          placeholder="请输入登录密码"
          @keyup.enter="handleLogin"
        />
      </el-form-item>

      <el-button
        class="auth-form__submit"
        type="primary"
        :loading="loading"
        @click="handleLogin"
      >
        登录
      </el-button>

      <div class="auth-form__links">
        <router-link to="/forgot-password">忘记密码</router-link>
      </div>
    </el-form>
  </AuthShell>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import { safeRedirectPath } from '@/router/guards'
import { resolveLandingPath } from '@/router/landing'
import { useAuthStore, type LoginSession } from '@/stores/auth'
import AuthShell from '@/views/login/AuthShell.vue'

interface LoginForm {
  account: string
  password: string
}

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive<LoginForm>({
  account: '',
  password: '',
})
const rules: FormRules<LoginForm> = {
  account: [
    { required: true, message: '请输入登录账号', trigger: 'blur' },
    { min: 3, max: 64, message: '登录账号长度为 3 到 64 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入登录密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
}

async function handleLogin(): Promise<void> {
  if (loading.value) {
    return
  }

  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    return
  }

  loading.value = true
  try {
    const session = await api.post<LoginSession>('/auth/login', {
      account: form.account,
      password: form.password,
    })
    authStore.setLoginSession(session)
    await authStore.fetchPermissions()

    const redirect = safeRedirectPath(route.query.redirect)
    const target =
      redirect || resolveLandingPath(authStore.roles, authStore.hasPermission)
    await router.replace(target)
  } catch (error) {
    authStore.clearSession()
    ElMessage.error(error instanceof Error ? error.message : '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-form__submit {
  width: 100%;
  margin-top: 4px;
}

.auth-form__links {
  margin-top: 18px;
  text-align: center;
  font-size: 14px;
}

.auth-form__links a {
  color: var(--el-color-primary);
}
</style>
