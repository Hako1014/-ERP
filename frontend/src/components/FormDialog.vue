<template>
  <el-dialog
    v-model="visible"
    class="form-dialog"
    :title="title"
    :width="width"
    :close-on-click-modal="!loading"
    :close-on-press-escape="!loading"
    :show-close="!loading"
    destroy-on-close
    @closed="emit('closed')"
  >
    <el-form
      :label-position="labelPosition"
      :label-width="labelWidth"
      :disabled="loading"
    >
      <slot />
    </el-form>

    <template #footer>
      <slot name="footer">
        <el-button :disabled="loading" @click="handleCancel">
          {{ cancelText }}
        </el-button>
        <el-button type="primary" :loading="loading" @click="emit('submit')">
          {{ submitText }}
        </el-button>
      </slot>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
const visible = defineModel<boolean>('visible', { default: false })

withDefaults(
  defineProps<{
    title: string
    width?: string | number
    labelWidth?: string | number
    labelPosition?: 'left' | 'right' | 'top'
    submitText?: string
    cancelText?: string
    loading?: boolean
  }>(),
  {
    width: '640px',
    labelWidth: '96px',
    labelPosition: 'right',
    submitText: '保存',
    cancelText: '取消',
    loading: false,
  },
)

const emit = defineEmits<{
  submit: []
  cancel: []
  closed: []
}>()

function handleCancel(): void {
  visible.value = false
  emit('cancel')
}
</script>

<style scoped>
.form-dialog :deep(.el-dialog__body) {
  padding-top: 18px;
}
</style>
