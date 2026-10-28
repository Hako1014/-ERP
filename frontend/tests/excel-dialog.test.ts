import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import ExcelImportExportDialog from '@/components/business/ExcelImportExportDialog.vue'

interface ExcelDialogVm {
  selectFile: (file: File) => void
  handleImport: () => Promise<void>
  handleExport: () => Promise<void>
}

function mountDialog() {
  return mount(ExcelImportExportDialog, {
    props: {
      visible: true,
      title: '商品导入导出',
      importUrl: '/products/import',
      exportUrl: '/products/export',
      templateUrl: '/products/template',
      filename: 'products.xlsx',
    },
    attachTo: document.body,
    global: {
      plugins: [ElementPlus],
      stubs: {
        teleport: true,
      },
    },
  })
}

describe('ExcelImportExportDialog', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('renders import errors by row and field', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({
      total: 2,
      created_count: 1,
      updated_count: 0,
      error_count: 1,
      errors: [
        {
          row: 3,
          field: 'code',
          message: '商品编码已存在：P001',
        },
      ],
    })
    const wrapper = mountDialog()
    await flushPromises()
    const vm = wrapper.vm as unknown as ExcelDialogVm

    vm.selectFile(new File(['excel'], 'products.xlsx'))
    await vm.handleImport()
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith(
      '/products/import',
      expect.any(FormData),
      expect.objectContaining({
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
    expect(wrapper.text()).toContain('商品编码已存在：P001')
    expect(wrapper.text()).toContain('code')
    expect(wrapper.text()).toContain('3')
  })

  it('downloads the exported Excel blob', async () => {
    const blob = new Blob(['excel-content'])
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(blob)
    const createObjectUrl = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:test')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
      () => undefined,
    )
    const wrapper = mountDialog()
    await flushPromises()
    const vm = wrapper.vm as unknown as ExcelDialogVm

    await vm.handleExport()
    await flushPromises()

    expect(getSpy).toHaveBeenCalledWith('/products/export', {
      responseType: 'blob',
      params: {},
    })
    expect(createObjectUrl).toHaveBeenCalledWith(blob)
  })
})
