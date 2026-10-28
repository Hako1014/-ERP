<template>
  <PartyManagementPanel kind="supplier" :can-manage="canManage" />
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useAuthStore } from '@/stores/auth'
import PartyManagementPanel from '@/views/settings/party/PartyManagementPanel.vue'
import '@/views/settings/settings.css'

const authStore = useAuthStore()
const canManage = computed(() => authStore.hasPermission('settings:manage'))
</script>
