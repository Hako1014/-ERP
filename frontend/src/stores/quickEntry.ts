import { defineStore } from 'pinia'
import { ref } from 'vue'

import type { AssistantPurchasePlanDraft } from '@/api/assistant'

interface PendingImport {
  identity: string
  draft: AssistantPurchasePlanDraft
}

/** 路由切换用的一次性内存交接，不将单据内容放入 URL 或浏览器持久存储。 */
export const useQuickEntryStore = defineStore('quick-entry-handoff', () => {
  const pending = ref<PendingImport | null>(null)

  function queue(identity: string, draft: AssistantPurchasePlanDraft): void {
    pending.value = {
      identity,
      draft: JSON.parse(JSON.stringify(draft)) as AssistantPurchasePlanDraft,
    }
  }

  function consume(identity: string): AssistantPurchasePlanDraft | null {
    const current = pending.value
    pending.value = null
    if (!current || current.identity !== identity) return null
    return JSON.parse(JSON.stringify(current.draft)) as AssistantPurchasePlanDraft
  }

  function clear(): void {
    pending.value = null
  }

  return { pending, queue, consume, clear }
})
