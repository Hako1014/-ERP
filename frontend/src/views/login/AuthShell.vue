<template>
  <main class="auth-shell">
    <section class="auth-shell__brand">
      <div class="auth-shell__logo">
        <el-icon><Shop /></el-icon>
        <span>电商ERP</span>
      </div>
      <span class="auth-shell__edition">企业管理端</span>
    </section>

    <section class="auth-shell__content">
      <div class="auth-shell__card">
        <header class="auth-shell__header">
          <h1>{{ title }}</h1>
          <p v-if="description">{{ description }}</p>
        </header>
        <slot />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { Shop } from '@element-plus/icons-vue'

defineProps<{
  title: string
  description?: string
}>()
</script>

<style scoped>
.auth-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(280px, 38%) 1fr;
  background: #f4f6f8;
}

.auth-shell__brand {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 48px 54px;
  background: #17202b;
  color: #eef4fb;
}

.auth-shell__logo {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 24px;
  font-weight: 700;
}

.auth-shell__logo .el-icon {
  font-size: 30px;
}

.auth-shell__edition {
  color: #9fb0c4;
  font-size: 14px;
}

.auth-shell__content {
  min-width: 0;
  display: grid;
  place-items: center;
  padding: 40px 24px;
}

.auth-shell__card {
  width: min(100%, 420px);
  padding: 34px 36px 30px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  box-shadow: 0 16px 44px rgb(23 32 43 / 8%);
}

.auth-shell__header {
  margin-bottom: 28px;
}

.auth-shell__header h1 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 26px;
  line-height: 1.35;
}

.auth-shell__header p {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

@media (max-width: 760px) {
  .auth-shell {
    grid-template-columns: 1fr;
  }

  .auth-shell__brand {
    min-height: auto;
    flex-direction: row;
    align-items: center;
    padding: 20px 22px;
  }

  .auth-shell__logo {
    font-size: 20px;
  }

  .auth-shell__edition {
    font-size: 13px;
  }

  .auth-shell__content {
    place-items: start center;
    padding: 28px 16px 40px;
  }

  .auth-shell__card {
    padding: 28px 22px 24px;
  }
}
</style>
