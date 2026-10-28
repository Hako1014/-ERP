<template>
  <section class="placeholder-page">
    <el-empty
      :description="`${pageTitle}页面骨架已就绪`"
      :image-size="96"
    >
      <el-button type="primary" @click="router.push('/dashboard')">
        返回首页看板
      </el-button>
    </el-empty>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const pageTitle = computed(() => String(route.meta.title || '业务'))
</script>

<style scoped>
.placeholder-page {
  min-height: 420px;
  display: grid;
  place-items: center;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
</style>
