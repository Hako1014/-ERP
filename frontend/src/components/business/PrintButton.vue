<template>
  <el-button
    :type="type"
    :disabled="disabled"
    :loading="loading"
    :icon="Printer"
    @click="handlePrint"
  >
    {{ label }}
  </el-button>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Printer } from '@element-plus/icons-vue'

import { api } from '@/api/client'
import { openBlobInNewTab } from '@/components/business/download'
import type { PrintPayload } from '@/components/business/types'

const props = withDefaults(
  defineProps<{
    source: string | (() => Promise<Blob | PrintPayload>)
    params?: Record<string, unknown>
    label?: string
    filename?: string
    type?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'default'
    disabled?: boolean
  }>(),
  {
    params: () => ({}),
    label: '打印',
    filename: 'document.pdf',
    type: 'default',
    disabled: false,
  },
)

const emit = defineEmits<{
  generated: [payload: PrintPayload]
  error: [error: unknown]
}>()

const loading = ref(false)

async function handlePrint(): Promise<void> {
  if (loading.value || props.disabled) {
    return
  }

  loading.value = true
  try {
    const result =
      typeof props.source === 'string'
        ? await api.get<Blob>(props.source, {
            params: props.params,
            responseType: 'blob',
          })
        : await props.source()
    const payload: PrintPayload =
      result instanceof Blob
        ? { blob: result, filename: props.filename }
        : {
            blob: result.blob,
            filename: result.filename || props.filename,
          }
    openBlobInNewTab(payload.blob)
    emit('generated', payload)
  } catch (error) {
    emit('error', error)
    ElMessage.error(error instanceof Error ? error.message : '打印文件生成失败')
  } finally {
    loading.value = false
  }
}
</script>
