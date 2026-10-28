<template>
  <el-breadcrumb separator="/">
    <el-breadcrumb-item
      v-if="parentTitle"
      :to="parentPath ? { path: parentPath } : undefined"
    >
      {{ parentTitle }}
    </el-breadcrumb-item>
    <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
  </el-breadcrumb>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => String(route.meta.title || '首页'))
const parentTitle = computed(() =>
  route.meta.parentTitle ? String(route.meta.parentTitle) : '',
)
const parentPath = computed(() =>
  route.meta.parentPath ? String(route.meta.parentPath) : '',
)
</script>
