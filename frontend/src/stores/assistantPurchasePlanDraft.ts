import { defineStore } from 'pinia'
import { ref } from 'vue'

import type { AssistantPurchasePlanDraft } from '@/api/assistant'

interface PendingPurchasePlanDraft {
  identity: string
  draft: AssistantPurchasePlanDraft
}

export interface UnresolvedPurchasePlanCreation {
  identity: string
  draftId: string
  requestId: string
  fingerprint: string
}

/** 一次性内存槽位：仅登录身份匹配时交付，不落 URL、localStorage 或持久化存储。 */
export const useAssistantPurchasePlanDraftStore = defineStore(
  'assistant-purchase-plan-draft',
  () => {
    const pending = ref<PendingPurchasePlanDraft | null>(null)
    const unresolvedCreation = ref<UnresolvedPurchasePlanCreation | null>(null)

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

    function setUnresolvedCreation(value: UnresolvedPurchasePlanCreation): void {
      unresolvedCreation.value = value
    }

    function getUnresolvedCreation(identity: string): UnresolvedPurchasePlanCreation | null {
      const current = unresolvedCreation.value
      return current?.identity === identity ? current : null
    }

    function clearUnresolvedCreation(identity: string): void {
      if (unresolvedCreation.value?.identity === identity) {
        unresolvedCreation.value = null
      }
    }

    return {
      pending,
      unresolvedCreation,
      queue,
      consume,
      clear,
      setUnresolvedCreation,
      getUnresolvedCreation,
      clearUnresolvedCreation,
    }
  },
)
