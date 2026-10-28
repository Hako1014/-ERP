<template>
  <header class="settings-page-header">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="settings-page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    description?: string
  }>(),
  {
    description: '',
  },
)
</script>

<style scoped>
.settings-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}

.settings-page-header h1 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 22px;
  line-height: 1.35;
}

.settings-page-header p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.settings-page-header__actions {
  flex: 0 0 auto;
}

@media (max-width: 720px) {
  .settings-page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .settings-page-header__actions {
    display: flex;
  }
}
</style>
