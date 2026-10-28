<template>
  <section class="forbidden-page">
    <el-result
      icon="error"
      title="无权访问"
      sub-title="当前账号没有访问此页面的权限"
    >
      <template #extra>
        <el-button type="primary" @click="goHome">返回首页</el-button>
        <el-button @click="goBack">返回上一页</el-button>
      </template>
    </el-result>
  </section>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

import { resolveLandingPath } from '@/router/landing'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

function goHome(): void {
  void router.replace(
    resolveLandingPath(authStore.roles, authStore.hasPermission),
  )
}

function goBack(): void {
  if (window.history.length > 1) {
    router.back()
    return
  }
  goHome()
}
</script>

<style scoped>
.forbidden-page {
  min-height: calc(100vh - 120px);
  display: grid;
  place-items: center;
}
</style>
