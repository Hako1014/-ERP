import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import type {
  UploadRawFile,
  UploadRequestOptions,
} from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import AttachmentUpload from '@/components/business/AttachmentUpload.vue'

interface AttachmentUploadVm {
  beforeUpload: (file: UploadRawFile) => boolean
  uploadRequest: (
    options: UploadRequestOptions,
  ) => Promise<{ url: string; filename: string; size: number }>
}

function rawFile(name: string, content = 'image'): UploadRawFile {
  const file = new File([content], name, { type: 'image/png' })
  return Object.assign(file, { uid: Date.now() }) as UploadRawFile
}

describe('AttachmentUpload', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('validates image extensions before upload', () => {
    const wrapper = mount(AttachmentUpload, {
      props: { modelValue: [] },
      global: { plugins: [ElementPlus] },
    })
    const vm = wrapper.vm as unknown as AttachmentUploadVm

    expect(vm.beforeUpload(rawFile('发货单.png'))).toBe(true)
    expect(vm.beforeUpload(rawFile('发货单.txt'))).toBe(false)
  })

  it('uploads through the shared upload endpoint', async () => {
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue({
      url: '/uploads/2026/09/24/image.png',
      filename: '发货单.png',
      size: 1024,
    })
    const wrapper = mount(AttachmentUpload, {
      props: { modelValue: [] },
      global: { plugins: [ElementPlus] },
    })
    const vm = wrapper.vm as unknown as AttachmentUploadVm
    const onSuccess = vi.fn()
    const options = {
      file: rawFile('发货单.png'),
      onSuccess,
      onError: vi.fn(),
      onProgress: vi.fn(),
    } as unknown as UploadRequestOptions

    const result = await vm.uploadRequest(options)

    expect(postSpy).toHaveBeenCalledWith(
      '/upload',
      expect.any(FormData),
      expect.objectContaining({
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
    expect(result.url).toBe('/uploads/2026/09/24/image.png')
    expect(onSuccess).toHaveBeenCalledWith(result)
  })
})
