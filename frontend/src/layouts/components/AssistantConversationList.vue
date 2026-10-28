<template>
  <aside class="conversation-list" aria-label="本人的历史会话" :aria-busy="store.listLoading">
    <div class="conversation-list__heading"><strong>我的会话</strong><small>仅本人可见</small></div>
    <input v-model="store.query" class="conversation-list__search" aria-label="搜索会话" maxlength="120" placeholder="搜索标题或聊天关键词" />
    <select v-model="store.status" aria-label="会话状态" class="conversation-list__filter">
      <option value="ACTIVE">最近会话</option><option value="ARCHIVED">已归档</option><option value="ALL">全部会话</option>
    </select>
    <p v-if="store.listError" class="conversation-list__error" role="alert">{{ store.listError }} <button type="button" @click="store.loadList()">重试加载</button></p>
    <p v-if="store.listLoading && !store.conversations.length" role="status">正在加载历史会话…</p>
    <div class="conversation-list__rows">
      <section v-for="group in groups" :key="group.title">
        <h3>{{ group.title }}</h3>
        <div v-for="record in group.items" :key="record.id" class="conversation-list__row" :class="{ 'is-active': store.active?.id === record.id }">
          <button type="button" class="conversation-list__select" :aria-current="store.active?.id === record.id ? 'true' : undefined" @click="emit('select', record)">
            <span class="conversation-list__title">{{ record.title }}</span>
            <small>{{ record.summary || '尚未开始对话' }}</small>
            <time :datetime="record.updated_at">{{ assistantTime(record.updated_at) }}</time>
          </button>
          <el-dropdown trigger="click" @command="(command: string) => emit('action', record, command)">
            <el-button text circle :aria-label="`会话操作：${record.title}`" :disabled="record.id === store.active?.id && store.generating"><el-icon><MoreFilled /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">重命名</el-dropdown-item>
                <el-dropdown-item command="pin">{{ record.is_pinned ? '取消置顶' : '置顶' }}</el-dropdown-item>
                <el-dropdown-item command="archive">{{ record.status === 'ARCHIVED' ? '恢复会话' : '归档' }}</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除会话</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </section>
      <div v-if="!store.listLoading && !store.conversations.length" class="conversation-list__empty">
        <p>{{ store.query ? '没有找到匹配的会话。可清空关键词，或新建会话。' : store.status === 'ARCHIVED' ? '还没有归档会话。归档后可在这里恢复。' : '还没有会话。新建后开始提问，聊天会自动保存。' }}</p>
        <el-button v-if="store.query" link @click="store.query = ''">清空搜索</el-button>
        <el-button link type="primary" @click="store.create()">新建会话</el-button>
      </div>
      <el-button v-if="store.conversations.length < store.total" class="conversation-list__more" :loading="store.listLoading" @click="store.loadList(store.page + 1)">加载更多会话</el-button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { MoreFilled } from '@element-plus/icons-vue'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import type { Conversation } from '@/api/assistantConversations'
import { assistantTime, conversationGroup } from './assistantConversationHelpers'

const store = useAssistantConversationsStore()
const emit = defineEmits<{ select: [record: Conversation]; action: [record: Conversation, command: string] }>()
const groups = computed(() => {
  const result: Array<{ title: string; items: Conversation[] }> = []
  for (const record of store.conversations) {
    const title = conversationGroup(record.updated_at, record.is_pinned)
    let group = result.find((item) => item.title === title)
    if (!group) { group = { title, items: [] }; result.push(group) }
    group.items.push(record)
  }
  return result
})
let timer: ReturnType<typeof setTimeout> | undefined
watch(() => [store.query, store.status] as const, () => {
  clearTimeout(timer)
  timer = setTimeout(() => { void store.loadList() }, 250)
})
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.conversation-list { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 12px; padding: 16px 12px; background: var(--el-fill-color-lighter); }
.conversation-list__heading { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.conversation-list__heading small { color: var(--el-text-color-secondary); }
.conversation-list__search, .conversation-list__filter { width: 100%; padding: 9px; border: 1px solid var(--el-border-color); border-radius: 7px; background: var(--el-bg-color); color: var(--el-text-color-primary); font: inherit; font-size: 13px; box-sizing: border-box; }
.conversation-list__rows { flex: 1; min-height: 0; overflow-y: auto; }
.conversation-list h3 { margin: 16px 8px 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.conversation-list__row { display: flex; align-items: center; border-radius: 7px; margin-bottom: 4px; }
.conversation-list__row.is-active { background: var(--el-color-primary-light-9); }
.conversation-list__row:hover { background: var(--el-fill-color); }
.conversation-list__select { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; padding: 10px 8px; border: 0; background: transparent; text-align: left; cursor: pointer; color: var(--el-text-color-primary); }
.conversation-list__title, .conversation-list__select small { width: 100%; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.conversation-list__select small, .conversation-list__select time { font-size: 11px; color: var(--el-text-color-secondary); }
.conversation-list__title { font-size: 13px; font-weight: 500; }
.conversation-list__empty, .conversation-list__error { font-size: 13px; line-height: 1.7; padding: 8px; }
.conversation-list__error { color: var(--el-color-danger); margin: 0; }
.conversation-list__more { width: 100%; margin-top: 12px; }
.conversation-list button:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
</style>
