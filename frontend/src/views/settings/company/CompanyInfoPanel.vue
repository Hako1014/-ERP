<template>
  <section v-loading="loading" class="settings-page__panel">
    <div class="settings-page__toolbar">
      <h2>公司信息</h2>
      <el-button
        v-if="canManage"
        type="primary"
        :loading="saving"
        @click="save"
      >
        保存公司信息
      </el-button>
    </div>
    <el-form
      class="settings-page__form-grid"
      label-width="96px"
      :disabled="!canManage"
    >
      <el-form-item label="公司全称" required>
        <el-input v-model="form.full_name" maxlength="128" />
      </el-form-item>
      <el-form-item label="公司简称">
        <el-input v-model="form.short_name" maxlength="64" />
      </el-form-item>
      <el-form-item label="公司法人">
        <el-input v-model="form.legal_person" maxlength="64" />
      </el-form-item>
      <el-form-item label="手机号码">
        <el-input v-model="form.phone" maxlength="32" />
      </el-form-item>
      <el-form-item label="企业税号">
        <el-input v-model="form.tax_no" maxlength="64" />
      </el-form-item>
    </el-form>
  </section>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { companyApi, type CompanyPayload } from '@/api/company'

defineProps<{
  canManage: boolean
}>()

const loading = ref(false)
const saving = ref(false)
const form = reactive<CompanyPayload>({
  full_name: '',
  short_name: '',
  legal_person: '',
  phone: '',
  tax_no: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const company = await companyApi.getCompany()
    Object.assign(form, {
      full_name: company.full_name,
      short_name: company.short_name || '',
      legal_person: company.legal_person || '',
      phone: company.phone || '',
      tax_no: company.tax_no || '',
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '公司信息加载失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!String(form.full_name || '').trim()) {
    ElMessage.warning('请填写公司全称')
    return
  }
  saving.value = true
  try {
    await companyApi.updateCompany({ ...form })
    ElMessage.success('公司信息已更新')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '公司信息保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
