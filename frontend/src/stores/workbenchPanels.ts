import { defineStore } from 'pinia'
import { ref } from 'vue'

export type WorkbenchPanel = 'announcements' | 'todos' | 'help'

export const useWorkbenchPanelStore = defineStore('workbench-panels', () => {
  const active = ref<WorkbenchPanel | null>(null)
  const announcementCount = ref(0)
  const todoCount = ref(0)
  const identity = ref('')

  function open(panel: WorkbenchPanel): void {
    active.value = panel
  }

  function close(): void {
    active.value = null
  }

  function resetForIdentity(nextIdentity: string): void {
    identity.value = nextIdentity
    active.value = null
    announcementCount.value = 0
    todoCount.value = 0
  }

  function updateCounts(
    expectedIdentity: string,
    counts: { announcements?: number; todos?: number },
  ): void {
    if (!expectedIdentity || identity.value !== expectedIdentity) return
    if (counts.announcements !== undefined) announcementCount.value = counts.announcements
    if (counts.todos !== undefined) todoCount.value = counts.todos
  }

  return {
    active,
    announcementCount,
    todoCount,
    identity,
    open,
    close,
    resetForIdentity,
    updateCounts,
  }
})
