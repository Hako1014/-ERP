<template>
  <div class="data-table">
    <div v-if="$slots.filters" class="data-table__filters">
      <slot
        name="filters"
        :filters="filters"
        :set-filters="setFilters"
        :reset-filters="resetFilters"
      />
    </div>

    <el-table
      v-loading="loading"
      class="data-table__table"
      :data="tableRows"
      :row-key="rowKey"
      :height="height"
      :max-height="maxHeight"
      :border="border"
      :stripe="stripe"
      @row-click="handleRowClick"
      @selection-change="handleSelectionChange"
    >
      <el-table-column
        v-for="column in columns"
        :key="column.prop || column.label"
        :type="column.type"
        :prop="column.prop"
        :label="column.label"
        :width="column.width"
        :min-width="column.minWidth"
        :align="column.align"
        :header-align="column.headerAlign"
        :fixed="column.fixed"
        :sortable="column.sortable"
        :show-overflow-tooltip="column.showOverflowTooltip ?? true"
      >
        <template #default="scope">
          <slot
            v-if="column.slot"
            :name="column.slot"
            :row="scope.row"
            :column="scope.column"
            :index="scope.$index"
            :value="getCellValue(scope.row, column)"
          />
          <span v-else>{{ renderCell(column, scope.row, scope.$index) }}</span>
        </template>
      </el-table-column>

      <template #empty>
        <EmptyState
          :description="emptyDescription"
          :action-text="emptyActionText"
          :show-action="showEmptyAction"
          @action="handleEmptyAction"
        />
      </template>
    </el-table>

    <div class="data-table__pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        background
        :total="effectiveTotal"
        :page-sizes="[...pageSizeOptions]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="handleCurrentChange"
        @size-change="handleSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts" generic="T extends Record<string, any> = Record<string, any>">
import { computed, nextTick, ref, watch } from 'vue'
import {
  useRoute,
  useRouter,
  type LocationQuery,
  type LocationQueryRaw,
  type LocationQueryValue,
} from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import type {
  DataTableColumn,
  DataTableFilterValue,
  DataTableQuery,
} from '@/components/types'

const props = withDefaults(
  defineProps<{
    rows?: readonly T[]
    columns: readonly DataTableColumn<T>[]
    loading?: boolean
    total?: number
    rowKey?: string | ((row: T) => string | number)
    syncQuery?: boolean
    filterKeys?: readonly string[]
    defaultPageSize?: number
    pageSizeOptions?: readonly number[]
    emptyDescription?: string
    emptyActionText?: string
    showEmptyAction?: boolean
    height?: string | number
    maxHeight?: string | number
    border?: boolean
    stripe?: boolean
  }>(),
  {
    rows: () => [],
    loading: false,
    rowKey: 'id',
    syncQuery: true,
    defaultPageSize: 20,
    pageSizeOptions: () => [10, 20, 50, 100],
    emptyDescription: '暂无数据',
    emptyActionText: '重置筛选',
    showEmptyAction: true,
    border: false,
    stripe: true,
  },
)

const emit = defineEmits<{
  'update:page': [page: number]
  'update:pageSize': [pageSize: number]
  'update:filters': [filters: Record<string, DataTableFilterValue>]
  'query-change': [query: DataTableQuery]
  'row-click': [row: T, column: unknown, event: Event]
  'selection-change': [rows: T[]]
  'empty-action': []
}>()

const page = defineModel<number>('page', { default: 1 })
const pageSize = defineModel<number>('pageSize', { default: 20 })
const filters = defineModel<Record<string, DataTableFilterValue>>('filters', {
  default: () => ({}),
})

const route = useRoute()
const router = useRouter()
const applyingRouteQuery = ref(false)
const discoveredFilterKeys = ref<string[]>([
  ...(props.filterKeys || []),
  ...Object.keys(filters.value),
])
let querySyncScheduled = false

const managedFilterKeys = computed(() =>
  Array.from(
    new Set([...(props.filterKeys || []), ...discoveredFilterKeys.value]),
  ),
)
const managedQueryKeys = computed(() => [
  'page',
  'page_size',
  ...managedFilterKeys.value,
])
const managedQueryKeySet = computed(() => new Set(managedQueryKeys.value))
const effectiveTotal = computed(() => props.total ?? props.rows.length)
const tableRows = computed(() => props.rows as T[])

function positiveInteger(
  value: LocationQueryValue | LocationQueryValue[] | undefined,
  fallback: number,
): number {
  const raw = Array.isArray(value) ? value[0] : value
  const parsed = Number.parseInt(String(raw ?? ''), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function queryValue(
  value: LocationQueryValue | LocationQueryValue[] | undefined,
): string | undefined {
  const raw = Array.isArray(value) ? value[0] : value
  return raw === null || raw === undefined ? undefined : String(raw)
}

function filterValue(value: DataTableFilterValue): string | undefined {
  if (value === null || value === undefined) {
    return undefined
  }
  const normalized = String(value).trim()
  return normalized === '' ? undefined : normalized
}

function filtersEqual(
  left: Record<string, DataTableFilterValue>,
  right: Record<string, DataTableFilterValue>,
): boolean {
  const leftEntries = Object.entries(left)
    .filter(([, value]) => filterValue(value) !== undefined)
    .sort(([a], [b]) => a.localeCompare(b))
  const rightEntries = Object.entries(right)
    .filter(([, value]) => filterValue(value) !== undefined)
    .sort(([a], [b]) => a.localeCompare(b))

  if (leftEntries.length !== rightEntries.length) {
    return false
  }

  return leftEntries.every(
    ([key, value], index) =>
      key === rightEntries[index][0] &&
      String(value) === String(rightEntries[index][1]),
  )
}

function currentQuery(): DataTableQuery {
  return {
    page: page.value,
    pageSize: pageSize.value,
    filters: { ...filters.value },
  }
}

function emitQueryChange(): void {
  emit('query-change', currentQuery())
}

function discoverFilterKeys(value: Record<string, DataTableFilterValue>): void {
  const next = new Set(discoveredFilterKeys.value)
  Object.keys(value).forEach((key) => next.add(key))
  discoveredFilterKeys.value = Array.from(next)
}

async function applyRouteQuery(query: LocationQuery): Promise<void> {
  if (!props.syncQuery) {
    return
  }

  const hasManagedQuery = managedQueryKeys.value.some(
    (key) => query[key] !== undefined,
  )
  if (!hasManagedQuery) {
    return
  }

  applyingRouteQuery.value = true
  const nextPage = positiveInteger(query.page, 1)
  const nextPageSize = positiveInteger(query.page_size, props.defaultPageSize)
  const nextFilters: Record<string, DataTableFilterValue> = {}

  managedFilterKeys.value.forEach((key) => {
    const value = queryValue(query[key])
    if (value !== undefined && value !== '') {
      nextFilters[key] = value
    }
  })

  let changed = false
  if (page.value !== nextPage) {
    page.value = nextPage
    changed = true
  }
  if (pageSize.value !== nextPageSize) {
    pageSize.value = nextPageSize
    changed = true
  }
  if (!filtersEqual(filters.value, nextFilters)) {
    filters.value = nextFilters
    changed = true
  }

  await nextTick()
  applyingRouteQuery.value = false
  if (changed) {
    emitQueryChange()
  }
}

async function writeRouteQuery(): Promise<void> {
  const nextQuery = { ...route.query } as LocationQueryRaw
  managedQueryKeySet.value.forEach((key) => {
    delete nextQuery[key]
  })

  nextQuery.page = String(page.value)
  nextQuery.page_size = String(pageSize.value)
  managedFilterKeys.value.forEach((key) => {
    const value = filterValue(filters.value[key])
    if (value !== undefined) {
      nextQuery[key] = value
    }
  })

  await router.replace({ query: nextQuery })
}

function scheduleQuerySync(): void {
  if (querySyncScheduled) {
    return
  }

  querySyncScheduled = true
  void nextTick(async () => {
    querySyncScheduled = false
    emitQueryChange()
    if (!props.syncQuery || applyingRouteQuery.value) {
      return
    }
    await writeRouteQuery()
  })
}

function getCellValue(row: T, column: DataTableColumn<T>): unknown {
  if (!column.prop) {
    return undefined
  }

  return column.prop.split('.').reduce<unknown>((current, key) => {
    if (current === null || current === undefined) {
      return undefined
    }
    return (current as Record<string, unknown>)[key]
  }, row)
}

function renderCell(
  column: DataTableColumn<T>,
  row: T,
  index: number,
): string | number {
  const value = getCellValue(row, column)
  const formatted = column.formatter?.(row, value, index)
  if (formatted !== undefined && formatted !== null) {
    return formatted
  }
  return value === null || value === undefined || value === ''
    ? '--'
    : String(value)
}

function setFilters(
  nextFilters: Record<string, DataTableFilterValue>,
): void {
  discoverFilterKeys(nextFilters)
  filters.value = { ...nextFilters }
}

function resetFilters(): void {
  page.value = 1
  filters.value = {}
}

function handleCurrentChange(value: number): void {
  page.value = value
}

function handleSizeChange(value: number): void {
  pageSize.value = value
  page.value = 1
}

function handleRowClick(row: T, column: unknown, event: Event): void {
  emit('row-click', row, column, event)
}

function handleSelectionChange(rows: T[]): void {
  emit('selection-change', rows)
}

function handleEmptyAction(): void {
  resetFilters()
  emit('empty-action')
}

watch(
  () => route.query,
  (query) => {
    void applyRouteQuery(query)
  },
  { immediate: true, deep: true },
)

watch(
  filters,
  (value) => {
    discoverFilterKeys(value)
    scheduleQuerySync()
  },
  { deep: true },
)

watch([page, pageSize], scheduleQuerySync)

defineExpose({
  page,
  pageSize,
  filters,
  setFilters,
  resetFilters,
})
</script>

<style scoped>
.data-table {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.data-table__filters {
  padding: 14px 16px 0;
  border: 1px solid var(--el-border-color-lighter);
  border-bottom: 0;
  border-radius: 8px 8px 0 0;
  background: var(--el-bg-color);
}

.data-table__table {
  width: 100%;
}

.data-table__table :deep(.el-table__empty-block) {
  min-height: 260px;
}

.data-table__pagination {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .data-table__pagination {
    justify-content: flex-start;
    overflow-x: auto;
  }
}
</style>
