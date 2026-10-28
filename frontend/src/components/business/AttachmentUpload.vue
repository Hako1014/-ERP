<template>
  <div class="attachment-upload">
    <el-upload
      ref="uploadRef"
      v-model:file-list="fileList"
      action="/upload"
      :accept="accept"
      :disabled="disabled"
      :limit="maxCount"
      :list-type="listType"
      :multiple="maxCount > 1"
      :http-request="uploadRequest"
      :before-upload="beforeUpload"
      :on-success="handleSuccess"
      :on-error="handleError"
      :on-remove="handleRemove"
      :on-preview="handlePreview"
      :on-exceed="handleExceed"
    >
      <el-icon v-if="listType === 'picture-card'"><Plus /></el-icon>
      <el-button v-else :icon="Upload">选择文件</el-button>
    </el-upload>

    <p class="attachment-upload__tip">
      仅支持图片，单张不超过 {{ maxSizeMb }}MB，最多 {{ maxCount }} 张
    </p>

    <el-dialog
      v-model="previewVisible"
      title="附件预览"
      width="min(720px, 92vw)"
      append-to-body
    >
      <img
        v-if="previewUrl"
        class="attachment-upload__preview"
        :src="previewUrl"
        alt="附件预览"
      />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import {
  type UploadFile,
  type UploadInstance,
  type UploadRawFile,
  type UploadRequestOptions,
  type UploadUserFile,
} from 'element-plus'

import { api } from '@/api/client'
import type {
  AttachmentItem,
  AttachmentUploadResponse,
} from '@/components/business/types'

const model = defineModel<AttachmentItem[]>('modelValue', {
  default: () => [],
})

const props = withDefaults(
  defineProps<{
    maxCount?: number
    maxSizeMb?: number
    accept?: string
    disabled?: boolean
    listType?: 'text' | 'picture' | 'picture-card'
  }>(),
  {
    maxCount: 9,
    maxSizeMb: 5,
    accept: '.jpg,.jpeg,.png,.gif,.webp',
    disabled: false,
    listType: 'picture-card',
  },
)

const emit = defineEmits<{
  change: [items: AttachmentItem[]]
}>()

const uploadRef = ref<UploadInstance>()
const fileList = ref<UploadUserFile[]>([])
const previewVisible = ref(false)
const previewUrl = ref('')
const allowedExtensions = computed(() =>
  props.accept
    .split(',')
    .map((item) => item.trim().replace(/^\./, '').toLowerCase())
    .filter(Boolean),
)

function toAttachment(file: UploadUserFile): AttachmentItem | null {
  const response = file.response as AttachmentUploadResponse | undefined
  const url = file.url || response?.url
  if (!url) {
    return null
  }
  return {
    uid: file.uid,
    name: file.name,
    url,
    size: file.size,
  }
}

function fileListToAttachments(): AttachmentItem[] {
  return fileList.value.flatMap((file) => {
    const item = toAttachment(file)
    return item ? [item] : []
  })
}

function attachmentSignature(items: AttachmentItem[]): string {
  return JSON.stringify(
    items.map((item) => ({
      name: item.name,
      url: item.url,
      size: item.size ?? null,
    })),
  )
}

function fileSignature(files: UploadUserFile[]): string {
  return attachmentSignature(
    files.flatMap((file) => {
      const item = toAttachment(file)
      return item ? [item] : []
    }),
  )
}

function toUploadFile(item: AttachmentItem, index: number): UploadUserFile {
  return {
    uid: typeof item.uid === 'number' ? item.uid : index,
    name: item.name,
    url: item.url,
    size: item.size,
    status: 'success',
  }
}

async function syncModel(): Promise<void> {
  await nextTick()
  const items = fileListToAttachments()
  model.value = items
  emit('change', items)
}

function beforeUpload(rawFile: UploadRawFile): boolean {
  const extension = rawFile.name.split('.').pop()?.toLowerCase() || ''
  if (!allowedExtensions.value.includes(extension)) {
    ElMessage.error('不支持的文件类型，仅支持图片')
    return false
  }
  if (rawFile.size > props.maxSizeMb * 1024 * 1024) {
    ElMessage.error(`文件大小不能超过 ${props.maxSizeMb}MB`)
    return false
  }
  return true
}

async function uploadRequest(
  options: UploadRequestOptions,
): Promise<AttachmentUploadResponse> {
  const formData = new FormData()
  formData.append('file', options.file)
  const response = await api.post<AttachmentUploadResponse>(
    '/upload',
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        if (!event.total) {
          return
        }
        options.onProgress({
          percent: Math.round((event.loaded / event.total) * 100),
        } as never)
      },
    },
  )
  options.onSuccess(response)
  return response
}

function handleSuccess(
  response: unknown,
  uploadFile: UploadFile,
): void {
  const payload = response as AttachmentUploadResponse
  uploadFile.url = payload.url
  uploadFile.response = payload
  void syncModel()
}

function handleError(error: Error, uploadFile: UploadFile): void {
  ElMessage.error(error.message || `${uploadFile.name} 上传失败`)
  void syncModel()
}

function handleRemove(): void {
  void syncModel()
}

function handlePreview(file: UploadFile): void {
  previewUrl.value = file.url || ''
  previewVisible.value = Boolean(previewUrl.value)
}

function handleExceed(): void {
  ElMessage.warning(`最多上传 ${props.maxCount} 张图片`)
}

watch(
  model,
  (items) => {
    if (attachmentSignature(items) !== fileSignature(fileList.value)) {
      fileList.value = items.map(toUploadFile)
    }
  },
  { immediate: true, deep: true },
)

defineExpose({
  beforeUpload,
  uploadRequest,
  syncModel,
  clear: () => uploadRef.value?.clearFiles(),
})
</script>

<style scoped>
.attachment-upload__tip {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.attachment-upload__preview {
  display: block;
  max-width: 100%;
  max-height: 72vh;
  margin: 0 auto;
  object-fit: contain;
}
</style>
