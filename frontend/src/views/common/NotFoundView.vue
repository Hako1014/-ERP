<template>
  <section class="not-found">
    <el-result
      icon="warning"
      title="页面不存在"
      sub-title="当前地址无法匹配系统页面"
    >
      <template #extra>
        <el-button type="primary" @click="router.push('/dashboard')">
          返回首页看板
        </el-button>
      </template>
    </el-result>
  </section>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.not-found {
  min-height: calc(100vh - 120px);
  display: grid;
  place-items: center;
}
</style>
