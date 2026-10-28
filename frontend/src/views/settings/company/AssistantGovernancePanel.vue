<template>
  <section class="assistant-governance" v-loading="loading">
    <el-alert
      title="密钥只会加密保存在服务端；读取配置时仅显示是否已设置。对话正文不会进入用量台账。"
      type="info"
      :closable="false"
      show-icon
      class="assistant-governance__notice"
    />

    <div class="assistant-governance__section-head">
      <div>
        <h2>服务配置与额度</h2>
        <p>固定连接 DeepSeek 官方服务地址；费用按管理员维护的单价估算，不内置价格承诺。</p>
      </div>
      <el-tag :type="config?.api_key_configured ? 'success' : 'warning'" effect="plain">
        {{ config?.api_key_configured ? 'API Key 已设置' : 'API Key 未设置' }}
      </el-tag>
    </div>

    <el-alert
      v-if="config && !config.secret_store_ready"
      title="服务端尚未配置有效的助手密钥加密密钥；保存 API Key 前需由部署人员设置 ASSISTANT_CONFIG_ENCRYPTION_KEY。"
      type="warning"
      :closable="false"
      show-icon
      class="assistant-governance__notice"
    />

    <el-form label-position="top" class="assistant-governance__form">
      <div class="assistant-governance__grid">
        <el-form-item label="助手服务">
          <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="附件外发识别">
          <el-switch v-model="form.allowAttachmentExternalSend" active-text="允许" inactive-text="禁止" :disabled="!form.enabled" />
          <small>启用后，员工仍须逐次确认；附件图片或文本才会发送到 DeepSeek。关闭时不外发。</small>
        </el-form-item>
        <el-form-item label="Provider">
          <el-input model-value="DeepSeek" disabled />
        </el-form-item>
        <el-form-item label="模型标识">
          <el-input v-model="form.model" maxlength="128" placeholder="例如 deepseek-v4-flash" />
        </el-form-item>
        <el-form-item label="服务地址（固定）">
          <el-input :model-value="config?.base_url || 'https://api.deepseek.com'" disabled />
        </el-form-item>
        <el-form-item label="替换 API Key">
          <el-input
            v-model="form.apiKey"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="留空表示保持当前密钥不变"
            maxlength="512"
            :disabled="form.clearApiKey"
          />
        </el-form-item>
        <el-form-item label="密钥操作">
          <el-checkbox v-model="form.clearApiKey">清除当前已保存的 API Key</el-checkbox>
        </el-form-item>
        <el-form-item label="每用户每分钟请求上限">
          <el-input-number v-model="form.requestsPerMinute" :min="1" :max="600" />
        </el-form-item>
        <el-form-item label="每用户并发上限">
          <el-input-number v-model="form.concurrentRequestsPerUser" :min="1" :max="10" />
        </el-form-item>
        <el-form-item label="每用户每日请求上限（留空不限）">
          <el-input-number
            v-model="form.dailyRequestsPerUser"
            :min="1"
            :max="50000"
            :value-on-clear="null"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="公司月度预算（留空不限）">
          <el-input v-model="form.monthlyBudget" inputmode="decimal" placeholder="留空不限；需同时填写输入和输出单价" />
        </el-form-item>
        <el-form-item label="输入单价 / 百万 token">
          <el-input v-model="form.inputPrice" inputmode="decimal" placeholder="同一计价单位；留空表示不估算费用" />
        </el-form-item>
        <el-form-item label="输出单价 / 百万 token">
          <el-input v-model="form.outputPrice" inputmode="decimal" placeholder="同一计价单位；留空表示不估算费用" />
        </el-form-item>
      </div>

      <div class="assistant-governance__actions">
        <el-link
          href="https://api-docs.deepseek.com/quick_start/pricing/"
          target="_blank"
          rel="noopener noreferrer"
          type="primary"
        >查看 DeepSeek 官方价格</el-link>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
      </div>
      <p class="assistant-governance__help">
        费用按上游 token 用量与录入单价估算；预算预占采用保守上界。建议使用官方最高档/缓存未命中价格，费率可能随时段或缓存命中变化。未收到用量回执时显示“未知”，并在当月预算占用中保留预占额。
      </p>
    </el-form>

    <el-divider />

    <div class="assistant-governance__section-head">
      <div>
        <h2>公司用量与审计</h2>
        <p>最近 30 天，最多显示 500 条；查询工具仅记录工具名与结果状态，不留存参数和单据内容。</p>
      </div>
      <el-button :loading="usageLoading" @click="loadUsage">刷新用量</el-button>
    </div>

    <div v-if="report" class="assistant-governance__summary">
      <el-tag effect="plain">请求 {{ report.summary.request_count }}</el-tag>
      <el-tag effect="plain">已知 token {{ report.summary.known_prompt_tokens + report.summary.known_completion_tokens }}</el-tag>
      <el-tag effect="plain">已知估算费用 {{ report.summary.known_cost }}</el-tag>
      <el-tag :type="report.summary.unknown_cost_count ? 'warning' : 'info'" effect="plain">
        未知费用 {{ report.summary.unknown_cost_count }} 条
      </el-tag>
      <el-tag v-if="report.summary.monthly_budget !== null" effect="plain">
        本月预算占用 {{ report.summary.monthly_budget_committed }} / {{ report.summary.monthly_budget }}
      </el-tag>
    </div>

    <el-table :data="report?.items || []" stripe empty-text="暂无用量记录" class="assistant-governance__table">
      <el-table-column prop="started_at" label="时间" min-width="160">
        <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
      </el-table-column>
      <el-table-column prop="staff_name" label="员工" min-width="110" />
      <el-table-column prop="model" label="模型" min-width="140" />
      <el-table-column prop="status" label="状态" width="95">
        <template #default="{ row }">{{ statusLabel(row.status) }}</template>
      </el-table-column>
      <el-table-column label="Token" min-width="135">
        <template #default="{ row }">
          {{ row.total_tokens === null ? '未知' : row.total_tokens }}
        </template>
      </el-table-column>
      <el-table-column label="延迟" width="95">
        <template #default="{ row }">{{ row.latency_ms === null ? '—' : `${row.latency_ms} ms` }}</template>
      </el-table-column>
      <el-table-column label="费用估算" min-width="130">
        <template #default="{ row }">{{ row.cost_status === 'unknown' ? '未知' : row.estimated_cost ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="工具" min-width="160">
        <template #default="{ row }">{{ row.tool_names?.join('、') || '—' }}</template>
      </el-table-column>
      <el-table-column prop="failure_category" label="失败分类" min-width="150" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { assistantApi, type AssistantAdminConfig, type AssistantUsageReport } from '@/api/assistant'

const loading = ref(false)
const saving = ref(false)
const usageLoading = ref(false)
const config = ref<AssistantAdminConfig | null>(null)
const report = ref<AssistantUsageReport | null>(null)
const form = reactive({
  enabled: true,
  allowAttachmentExternalSend: false,
  model: '',
  apiKey: '',
  clearApiKey: false,
  requestsPerMinute: 10,
  concurrentRequestsPerUser: 1,
  dailyRequestsPerUser: null as number | null,
  monthlyBudget: '',
  inputPrice: '',
  outputPrice: '',
})

function applyConfig(value: AssistantAdminConfig) {
  config.value = value
  form.enabled = value.enabled
  form.allowAttachmentExternalSend = value.allow_attachment_external_send
  form.model = value.model
  form.requestsPerMinute = value.requests_per_minute
  form.concurrentRequestsPerUser = value.concurrent_requests_per_user
  form.dailyRequestsPerUser = value.daily_requests_per_user
  form.monthlyBudget = value.monthly_budget ?? ''
  form.inputPrice = value.input_price_per_million ?? ''
  form.outputPrice = value.output_price_per_million ?? ''
  form.apiKey = ''
  form.clearApiKey = false
}

async function loadConfig() {
  loading.value = true
  try {
    applyConfig(await assistantApi.getAdminConfig())
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'AI 助手配置加载失败')
  } finally {
    loading.value = false
  }
}

async function loadUsage() {
  usageLoading.value = true
  try {
    report.value = await assistantApi.getAdminUsage(30)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'AI 助手用量加载失败')
  } finally {
    usageLoading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const value = await assistantApi.updateAdminConfig({
      enabled: form.enabled,
      allow_attachment_external_send: form.allowAttachmentExternalSend,
      provider: 'deepseek',
      model: form.model.trim(),
      api_key: form.clearApiKey ? null : form.apiKey.trim() || null,
      clear_api_key: form.clearApiKey,
      requests_per_minute: form.requestsPerMinute,
      concurrent_requests_per_user: form.concurrentRequestsPerUser,
      daily_requests_per_user: form.dailyRequestsPerUser,
      monthly_budget: form.monthlyBudget.trim() || null,
      input_price_per_million: form.inputPrice.trim() || null,
      output_price_per_million: form.outputPrice.trim() || null,
    })
    applyConfig(value)
    ElMessage.success('AI 助手配置已保存')
    await loadUsage()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'AI 助手配置保存失败')
  } finally {
    saving.value = false
  }
}

function formatTime(value: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function statusLabel(value: string) {
  return ({
    started: '进行中',
    success: '成功',
    failed: '失败',
    cancelled: '已取消',
    rejected: '已拦截',
  } as Record<string, string>)[value] || value
}

onMounted(() => {
  void loadConfig()
  void loadUsage()
})
</script>

<style scoped>
.assistant-governance {
  padding: 20px;
}

.assistant-governance__notice {
  margin-bottom: 18px;
}

.assistant-governance__section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin: 22px 0 16px;
}

.assistant-governance__section-head h2 {
  margin: 0;
  font-size: 17px;
  color: var(--el-text-color-primary);
}

.assistant-governance__section-head p,
.assistant-governance__help {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.assistant-governance__form {
  max-width: 1100px;
}

.assistant-governance__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(250px, 1fr));
  gap: 0 24px;
}

.assistant-governance__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 18px;
}

.assistant-governance__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.assistant-governance__table {
  width: 100%;
}

@media (max-width: 720px) {
  .assistant-governance {
    padding: 12px;
  }

  .assistant-governance__grid {
    grid-template-columns: 1fr;
  }

  .assistant-governance__section-head {
    flex-direction: column;
  }
}
</style>
