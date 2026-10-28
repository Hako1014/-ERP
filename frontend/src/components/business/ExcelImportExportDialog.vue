<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="min(820px, 94vw)"
    destroy-on-close
    @closed="resetDialogState"
  >
    <el-tabs v-model="activeTab">
      <el-tab-pane label="导入" name="import">
        <div class="excel-dialog__toolbar">
          <el-button
            v-if="templateUrl"
            :loading="templateLoading"
            :icon="Download"
            @click="handleTemplateDownload"
          >
            下载导入模板
          </el-button>
        </div>

        <el-upload
          ref="uploadRef"
          drag
          action="#"
          accept=".xlsx,.xls"
          :auto-upload="false"
          :limit="1"
          :disabled="!importUrl || importing"
          :on-change="handleFileChange"
          :on-exceed="handleExceed"
        >
          <el-icon class="excel-dialog__upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">
            将 Excel 文件拖到此处，或<em>点击选择</em>
          </div>
        </el-upload>

        <div v-if="selectedFile" class="excel-dialog__selected">
          <span>{{ selectedFile.name }}</span>
          <el-button text type="danger" @click="clearSelectedFile">
            移除
          </el-button>
        </div>

        <div class="excel-dialog__actions">
          <el-button
            type="primary"
            :disabled="!importUrl || !selectedFile"
            :loading="importing"
            :icon="Upload"
            @click="handleImport"
          >
            开始导入
          </el-button>
        </div>

        <template v-if="importResult">
          <el-alert
            class="excel-dialog__result"
            :type="errorRows.length ? 'warning' : 'success'"
            :closable="false"
            show-icon
            :title="resultSummary"
          />

          <el-table
            v-if="errorRows.length"
            class="excel-dialog__errors"
            :data="errorRows"
            border
            max-height="280"
          >
            <el-table-column label="行号" width="90">
              <template #default="{ row }">
                {{ row.row > 0 ? row.row : '文件' }}
              </template>
            </el-table-column>
            <el-table-column
              prop="field"
              label="字段"
              width="150"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                {{ row.field || '整行' }}
              </template>
            </el-table-column>
            <el-table-column
              prop="message"
              label="错误原因"
              min-width="260"
              show-overflow-tooltip
            />
          </el-table>
        </template>
      </el-tab-pane>

      <el-tab-pane label="导出" name="export">
        <div class="excel-dialog__export">
          <p>导出内容将按当前查询条件生成。</p>
          <div class="excel-dialog__actions">
            <el-button
              v-if="templateUrl"
              :loading="templateLoading"
              :icon="Download"
              @click="handleTemplateDownload"
            >
              下载导入模板
            </el-button>
            <el-button
              type="primary"
              :disabled="!exportUrl"
              :loading="exporting"
              :icon="Download"
              @click="handleExport"
            >
              导出 Excel
            </el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ElMessage,
  type UploadFile,
  type UploadInstance,
} from 'element-plus'
import { Download, Upload, UploadFilled } from '@element-plus/icons-vue'

import { api } from '@/api/client'
import { saveBlob } from '@/components/business/download'
import type {
  ExcelImportError,
  ExcelImportResult,
} from '@/components/business/types'

const visible = defineModel<boolean>('visible', { default: false })

const props = withDefaults(
  defineProps<{
    title?: string
    templateUrl?: string
    importUrl?: string
    exportUrl?: string
    exportMethod?: 'GET' | 'POST'
    exportParams?: Record<string, unknown>
    filename?: string
  }>(),
  {
    title: 'Excel 导入导出',
    exportMethod: 'GET',
    exportParams: () => ({}),
  },
)

const emit = defineEmits<{
  imported: [result: ExcelImportResult]
  exported: []
}>()

const activeTab = ref('import')
const uploadRef = ref<UploadInstance>()
const selectedFile = ref<File | null>(null)
const importResult = ref<ExcelImportResult | null>(null)
const importing = ref(false)
const exporting = ref(false)
const templateLoading = ref(false)
const errorRows = computed<ExcelImportError[]>(
  () => importResult.value?.errors || [],
)
const resultSummary = computed(() => {
  const result = importResult.value
  if (!result) {
    return ''
  }
  const created = result.created_count ?? result.created ?? 0
  const updated = result.updated_count ?? result.updated ?? 0
  const errors = result.error_count ?? errorRows.value.length
  const total = result.total ?? created + updated + errors
  return `处理 ${total} 条，新增 ${created} 条，更新 ${updated} 条，错误 ${errors} 条`
})
const exportFilename = computed(() => props.filename || `${props.title}.xlsx`)
const templateFilename = computed(
  () => `${props.title.replace(/\s*导入导出\s*/, '')}-导入模板.xlsx`,
)

function selectFile(file: File): void {
  selectedFile.value = file
  importResult.value = null
}

function handleFileChange(uploadFile: UploadFile): void {
  if (uploadFile.raw) {
    selectFile(uploadFile.raw)
  }
}

function handleExceed(): void {
  ElMessage.warning('每次只能选择一个文件')
}

function clearSelectedFile(): void {
  selectedFile.value = null
  importResult.value = null
  uploadRef.value?.clearFiles()
}

function resetDialogState(): void {
  importResult.value = null
  selectedFile.value = null
  uploadRef.value?.clearFiles()
}

async function handleImport(): Promise<void> {
  if (!props.importUrl || !selectedFile.value || importing.value) {
    return
  }

  importing.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    const result = await api.post<ExcelImportResult>(props.importUrl, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    importResult.value = result
    emit('imported', result)
    if ((result.errors || []).length) {
      ElMessage.warning('导入完成，请检查错误行')
    } else {
      ElMessage.success('导入完成')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '导入失败')
  } finally {
    importing.value = false
  }
}

async function handleTemplateDownload(): Promise<void> {
  if (!props.templateUrl || templateLoading.value) {
    return
  }

  templateLoading.value = true
  try {
    const blob = await api.get<Blob>(props.templateUrl, {
      responseType: 'blob',
    })
    saveBlob(blob, templateFilename.value)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模板下载失败')
  } finally {
    templateLoading.value = false
  }
}

async function handleExport(): Promise<void> {
  if (!props.exportUrl || exporting.value) {
    return
  }

  exporting.value = true
  try {
    const config = { responseType: 'blob' as const }
    const blob =
      props.exportMethod === 'POST'
        ? await api.post<Blob>(
            props.exportUrl,
            props.exportParams,
            config,
          )
        : await api.get<Blob>(props.exportUrl, {
            ...config,
            params: props.exportParams,
          })
    saveBlob(blob, exportFilename.value)
    emit('exported')
    ElMessage.success('导出文件已生成')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

defineExpose({
  selectFile,
  clearSelectedFile,
  handleImport,
  handleTemplateDownload,
  handleExport,
})
</script>

<style scoped>
.excel-dialog__toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.excel-dialog__upload-icon {
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);
  font-size: 46px;
}

.excel-dialog__selected {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  word-break: break-all;
}

.excel-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.excel-dialog__result {
  margin-top: 18px;
}

.excel-dialog__errors {
  margin-top: 12px;
}

.excel-dialog__export {
  min-height: 250px;
}

.excel-dialog__export p {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
}
</style>
