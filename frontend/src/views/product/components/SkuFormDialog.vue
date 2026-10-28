<template>
  <FormDialog
    v-model:visible="visible"
    :title="editing ? '编辑商品规格' : '新增商品规格'"
    :width="720"
    :loading="saving"
    @submit="submit"
  >
    <div class="settings-page__form-grid">
      <el-form-item label="规格名称">
        <el-input v-model="form.spec_name" maxlength="128" />
      </el-form-item>
      <el-form-item label="规格编码" required>
        <el-input v-model="form.spec_code" maxlength="64" />
      </el-form-item>
      <el-form-item label="条形码">
        <el-input v-model="form.barcode" maxlength="64" />
      </el-form-item>
      <el-form-item label="状态">
        <el-switch
          v-model="form.is_active"
          active-text="启用"
          inactive-text="停用"
        />
      </el-form-item>
      <el-form-item label="吊牌价">
        <el-input v-model="form.tag_price" inputmode="decimal" />
      </el-form-item>
      <el-form-item label="销售价">
        <el-input v-model="form.sale_price" inputmode="decimal" />
      </el-form-item>
      <el-form-item label="成本价">
        <el-input v-model="form.cost_price" inputmode="decimal" />
      </el-form-item>
      <el-form-item label="最低售价">
        <el-input v-model="form.min_price" inputmode="decimal" />
      </el-form-item>
      <el-form-item class="is-full-width" label="规格图片">
        <el-input
          v-model="form.image"
          maxlength="255"
          placeholder="填写图片 URL"
        />
      </el-form-item>
    </div>
  </FormDialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'

import type { ProductSku, SkuPayload } from '@/api/product'
import FormDialog from '@/components/FormDialog.vue'

const props = defineProps<{
  saving: boolean
}>()

const emit = defineEmits<{
  submit: [payload: SkuPayload, skuId?: number]
}>()

const visible = defineModel<boolean>('visible', { default: false })

const editing = ref<ProductSku | null>(null)
const form = reactive({
  spec_name: '',
  spec_code: '',
  barcode: '',
  tag_price: '',
  sale_price: '',
  cost_price: '',
  min_price: '',
  image: '',
  is_active: true,
})

function decimalValue(value: ProductSku['tag_price']): string {
  return value === null || value === undefined ? '' : String(value)
}

function open(sku?: ProductSku): void {
  editing.value = sku || null
  form.spec_name = sku?.spec_name || ''
  form.spec_code = sku?.spec_code || ''
  form.barcode = sku?.barcode || ''
  form.tag_price = decimalValue(sku?.tag_price)
  form.sale_price = decimalValue(sku?.sale_price)
  form.cost_price = decimalValue(sku?.cost_price)
  form.min_price = decimalValue(sku?.min_price)
  form.image = sku?.image || ''
  form.is_active = sku?.is_active ?? true
  visible.value = true
}

function nullable(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function submit(): void {
  if (!form.spec_code.trim()) {
    ElMessage.warning('请填写规格编码')
    return
  }
  emit(
    'submit',
    {
      spec_name: nullable(form.spec_name),
      spec_code: form.spec_code.trim(),
      barcode: nullable(form.barcode),
      tag_price: nullable(form.tag_price),
      sale_price: nullable(form.sale_price),
      cost_price: nullable(form.cost_price),
      min_price: nullable(form.min_price),
      image: nullable(form.image),
      is_active: form.is_active,
    },
    editing.value?.id,
  )
}

defineExpose({ open })
</script>
