<template>
  <AuthShell title="忘记密码" description="验证手机号后重置登录密码">
    <el-form
      ref="formRef"
      class="auth-form"
      :model="form"
      :rules="rules"
      label-position="top"
      @submit.prevent
    >
      <el-form-item label="手机号码" prop="phone">
        <el-input
          v-model="form.phone"
          name="phone"
          autocomplete="tel"
          maxlength="11"
          clearable
          placeholder="请输入手机号码"
        />
      </el-form-item>

      <el-form-item label="短信验证码" prop="code">
        <div class="auth-form__code-row">
          <el-input
            v-model="form.code"
            name="code"
            inputmode="numeric"
            maxlength="6"
            clearable
            placeholder="请输入 6 位验证码"
          />
          <el-button
            :disabled="countdown > 0 || sendingCode"
            :loading="sendingCode"
            @click="sendCode"
          >
            {{ countdown > 0 ? `${countdown} 秒` : '获取验证码' }}
          </el-button>
        </div>
      </el-form-item>

      <el-form-item label="图形验证码" prop="captcha">
        <ImageCaptcha
          ref="captchaRef"
          v-model="form.captcha"
        />
      </el-form-item>

      <el-alert
        v-if="mockCode"
        class="auth-form__mock-code"
        type="warning"
        :closable="false"
        show-icon
        :title="`本地调试验证码：${mockCode}`"
      />

      <el-form-item label="新密码" prop="newPassword">
        <el-input
          v-model="form.newPassword"
          name="newPassword"
          type="password"
          autocomplete="new-password"
          show-password
          placeholder="请输入 8-64 位字母和数字组合"
        />
      </el-form-item>

      <el-form-item label="确认新密码" prop="confirmPassword">
        <el-input
          v-model="form.confirmPassword"
          name="confirmPassword"
          type="password"
          autocomplete="new-password"
          show-password
          placeholder="请再次输入新密码"
          @keyup.enter="handleReset"
        />
      </el-form-item>

      <el-button
        class="auth-form__submit"
        type="primary"
        :loading="resetting"
        @click="handleReset"
      >
        重置密码
      </el-button>

      <div class="auth-form__links">
        <router-link to="/login">返回登录</router-link>
      </div>
    </el-form>
  </AuthShell>
</template>

<script setup lang="ts">
import { onUnmounted, reactive, ref } from 'vue'
import {
  ElMessage,
  type FormInstance,
  type FormRules,
} from 'element-plus'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import AuthShell from '@/views/login/AuthShell.vue'
import ImageCaptcha from '@/views/login/ImageCaptcha.vue'

interface ResetPasswordForm {
  phone: string
  code: string
  captcha: string
  newPassword: string
  confirmPassword: string
}

interface CaptchaInstance {
  refresh: () => void
  validate: () => boolean
}

const router = useRouter()
const formRef = ref<FormInstance>()
const captchaRef = ref<CaptchaInstance>()
const mockCode = ref('')
const form = reactive<ResetPasswordForm>({
  phone: '',
  code: '',
  captcha: '',
  newPassword: '',
  confirmPassword: '',
})
const sendingCode = ref(false)
const resetting = ref(false)
const countdown = ref(0)
let countdownTimer: ReturnType<typeof setInterval> | null = null

function validateConfirmPassword(
  _rule: unknown,
  value: unknown,
  callback: (error?: string | Error) => void,
): void {
  const password = String(value || '')
  if (!password) {
    callback(new Error('请再次输入新密码'))
    return
  }
  if (password !== form.newPassword) {
    callback(new Error('两次输入的密码不一致'))
    return
  }
  callback()
}

const rules: FormRules<ResetPasswordForm> = {
  phone: [
    { required: true, message: '请输入手机号码', trigger: 'blur' },
    {
      pattern: /^1\d{10}$/,
      message: '请输入正确的手机号码',
      trigger: 'blur',
    },
  ],
  code: [
    { required: true, message: '请输入短信验证码', trigger: 'blur' },
    {
      pattern: /^\d{6}$/,
      message: '请输入 6 位数字验证码',
      trigger: 'blur',
    },
  ],
  captcha: [
    { required: true, message: '请输入图形验证码', trigger: 'blur' },
  ],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { pattern: /^(?=.*[A-Za-z])(?=.*\d).{8,64}$/, message: '密码需为 8-64 位，且同时包含字母和数字', trigger: 'blur' },
  ],
  confirmPassword: [
    { validator: validateConfirmPassword, trigger: 'blur' },
  ],
}

async function validateField(field: keyof ResetPasswordForm): Promise<boolean> {
  return formRef.value
    ?.validateField(field)
    .then(() => true)
    .catch(() => false) ?? false
}

function startCountdown(): void {
  countdown.value = 60
  if (countdownTimer) {
    clearInterval(countdownTimer)
  }
  countdownTimer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0 && countdownTimer) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

async function sendCode(): Promise<void> {
  if (sendingCode.value || countdown.value > 0) {
    return
  }
  if (!(await validateField('phone'))) {
    return
  }
  if (!captchaRef.value?.validate()) {
    ElMessage.error('图形验证码错误')
    captchaRef.value?.refresh()
    return
  }

  sendingCode.value = true
  try {
    const payload = await api.post<{ sent: boolean; mock_code?: string }>(
      '/auth/forgot-password/send-code',
      { phone: form.phone },
    )
    mockCode.value = payload.mock_code || ''
    startCountdown()
    ElMessage.success('验证码已发送')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '验证码发送失败')
  } finally {
    sendingCode.value = false
  }
}

async function handleReset(): Promise<void> {
  if (resetting.value) {
    return
  }
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    return
  }
  if (!captchaRef.value?.validate()) {
    ElMessage.error('图形验证码错误')
    captchaRef.value?.refresh()
    return
  }

  resetting.value = true
  try {
    await api.post('/auth/forgot-password/reset', {
      phone: form.phone,
      code: form.code,
      new_password: form.newPassword,
    })
    ElMessage.success('密码已重置，请使用新密码登录')
    await router.replace({ name: 'login' })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '密码重置失败')
  } finally {
    resetting.value = false
  }
}

onUnmounted(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
  }
})
</script>

<style scoped>
.auth-form__code-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  gap: 10px;
}

.auth-form__mock-code {
  margin: -2px 0 18px;
}

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
