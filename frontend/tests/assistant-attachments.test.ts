import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import { useAssistantConversationsStore } from '@/stores/assistantConversations'
import { assistantAttachmentsApi as api, type AssistantAttachment, type AttachmentPolicy } from '@/api/assistantAttachments'
import AssistantAttachments from '@/layouts/components/AssistantAttachments.vue'
import AssistantAttachmentList from '@/layouts/components/AssistantAttachmentList.vue'

enableAutoUnmount(afterEach)
const id = '00000000-0000-4000-8000-000000000001'
const fileId = '00000000-0000-4000-8000-000000000002'
const rules: AttachmentPolicy = { extensions: ['.png', '.jpg', '.jpeg', '.webp', '.pdf', '.txt', '.csv', '.xlsx'],
  max_file_bytes: 10 * 1024 * 1024, max_files: 5, max_total_bytes: 25 * 1024 * 1024,
  max_image_pixels: 20000000, max_pdf_pages: 20, max_zip_entries: 1000, max_uncompressed_bytes: 50 * 1024 * 1024,
  owner_quota_bytes: 250 * 1024 * 1024, orphan_hours: 24, deleted_retention_days: 7 }
const attachment = (extra: Partial<AssistantAttachment> = {}): AssistantAttachment => ({ id: fileId, conversation_id: id,
  message_id: null, display_name: '单据.png', media_type: 'image/png', size: 100, status: 'READY',
  purpose: 'CHAT', model_sent: false, created_at: '', expires_at: '', ...extra })
const dialog = { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] }
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function setup(initial: AssistantAttachment[] = []) {
  const auth = useAuthStore()
  auth.setLoginSession({ staff_id: 1, name: '采购员', token: 'token1', roles: ['PURCHASER'] })
  auth.setPermissionSnapshot({ staff_id: 1, company_id: 1, company_name: '公司一', roles: ['PURCHASER'], permissions: ['module:purchase'], modules: ['purchase'] })
  const store = useAssistantConversationsStore()
  store.initialized = true
  store.active = { id, title: '会话', status: 'ACTIVE', version: 1, latest_message_seq: 0, message_count: 0,
    summary: '', is_pinned: false, created_at: '', updated_at: '', retention_days: null }
  const policy = vi.spyOn(api, 'policy').mockResolvedValue(rules)
  const pending = vi.spyOn(api, 'pending').mockResolvedValue(initial)
  const upload = vi.spyOn(api, 'upload').mockResolvedValue(attachment())
  const save = vi.spyOn(api, 'save').mockResolvedValue({ version: 2 })
  const removed = vi.spyOn(api, 'remove').mockResolvedValue({ id: fileId, status: 'DELETED' })
  const saved = vi.spyOn(store, 'attachmentsSaved').mockResolvedValue()
  const stream = vi.spyOn(store, 'send').mockResolvedValue()
  const wrapper = mount(AssistantAttachments, { props: { disabled: false }, global: { stubs: { ElDialog: dialog } } })
  await flushPromises()
  return { auth, store, wrapper, policy, pending, upload, save, removed, saved, stream }
}
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find((item) => item.text().includes(text))! }
async function choose(wrapper: ReturnType<typeof mount>, files: File[]) {
  Object.defineProperty(wrapper.get('input').element, 'files', { configurable: true, value: files })
  await wrapper.get('input').trigger('change'); await flushPromises()
}

describe('私有附件输入', () => {
  it('限制来自服务端；选择上传不调用模型，显式保存后才关联消息', async () => {
    const ctx = await setup()
    ctx.store.draft = '仅保存这一段'
    expect(ctx.wrapper.text()).toContain('PDF ≤20 页')
    await choose(ctx.wrapper, [new File(['image'], '单据.png', { type: 'image/png' })])
    expect(ctx.upload).toHaveBeenCalledOnce(); expect(ctx.save).not.toHaveBeenCalled(); expect(ctx.stream).not.toHaveBeenCalled()
    expect(ctx.wrapper.text()).toContain('尚未发给模型')
    await button(ctx.wrapper, '保存文字和附件').trigger('click'); await flushPromises()
    expect(ctx.save.mock.calls[0]![0]).toMatchObject({ conversation_id: id, attachment_ids: [fileId], content: '仅保存这一段' })
    expect(ctx.saved).toHaveBeenCalledWith(id, 2); expect(ctx.store.draft).toBe(''); expect(ctx.stream).not.toHaveBeenCalled()
  })
  it('支持拖放和粘贴图片，禁止格式、数量和大小在上传前报错', async () => {
    const ctx = await setup()
    await choose(ctx.wrapper, [new File(['x'], 'x.exe')]); expect(ctx.upload).not.toHaveBeenCalled()
    expect(ctx.wrapper.get('[role=alert]').text()).toContain('尚未上传')
    await choose(ctx.wrapper, Array.from({ length: 6 }, (_, index) => new File(['x'], `${index}.txt`))); expect(ctx.upload).not.toHaveBeenCalled()
    const huge = new File(['x'], 'large.txt'); Object.defineProperty(huge, 'size', { value: rules.max_file_bytes + 1 })
    await choose(ctx.wrapper, [huge]); expect(ctx.upload).not.toHaveBeenCalled()
    await ctx.wrapper.trigger('drop', { dataTransfer: { files: [new File(['x'], 'x.txt')] } }); await flushPromises()
    expect(ctx.upload).toHaveBeenCalledOnce()
    await ctx.wrapper.trigger('paste', { clipboardData: { files: [new File(['x'], 'x.png', { type: 'image/png' })] } }); await flushPromises()
    expect(ctx.upload).toHaveBeenCalledTimes(2)
  })
  it('恢复未保存的文件，移除后不会保存或调用模型', async () => {
    const ctx = await setup([attachment()])
    expect(ctx.pending).toHaveBeenCalledWith(id, expect.any(AbortSignal))
    await button(ctx.wrapper, '移除').trigger('click'); await flushPromises()
    expect(ctx.removed).toHaveBeenCalledWith(fileId, id, expect.any(AbortSignal))
    expect(ctx.wrapper.text()).not.toContain('单据.png'); expect(ctx.save).not.toHaveBeenCalled()
  })
  it('上传失败不展示成功附件；服务端校验完成前只显示进度', async () => {
    const ctx = await setup()
    ctx.upload.mockImplementationOnce(async (_id, _file, _controller, progress) => { progress(80); throw new Error('真实格式不符') })
    await choose(ctx.wrapper, [new File(['x'], 'bad.png')])
    expect(ctx.wrapper.get('[role=alert]').text()).toContain('真实格式不符')
    expect(ctx.wrapper.find('.assistant-attachment-list__item').exists()).toBe(false)
  })
  it('取消上传和切换身份会中止请求，迟到结果不进入新身份界面', async () => {
    const ctx = await setup()
    let finish!: (file: AssistantAttachment) => void
    ctx.upload.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    await choose(ctx.wrapper, [new File(['x'], 'a.png')])
    const controller = ctx.upload.mock.calls[0]![2]
    await button(ctx.wrapper, '取消上传').trigger('click')
    expect(controller.signal.aborted).toBe(true)
    finish(attachment()); await flushPromises()
    expect(ctx.wrapper.text()).not.toContain('单据.png')
    ctx.upload.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    await choose(ctx.wrapper, [new File(['x'], 'b.png')])
    ctx.auth.setLoginSession({ staff_id: 2, name: '其他人', token: 'token2', roles: ['ADMIN'] })
    expect(ctx.upload.mock.calls[1]![2].signal.aborted).toBe(true)
    finish(attachment()); await flushPromises()
    expect(ctx.wrapper.text()).not.toContain('单据.png')
  })
  it('保存网络结果未知时保留原幂等请求，不重发修改后的文字', async () => {
    const ctx = await setup([attachment()]); ctx.store.draft = '原文'
    ctx.save.mockRejectedValueOnce(new Error('断连'))
    await button(ctx.wrapper, '保存文字和附件').trigger('click'); await flushPromises()
    const original = JSON.parse(JSON.stringify(ctx.save.mock.calls[0]![0]))
    ctx.store.draft = '新文'
    await button(ctx.wrapper, '核对并重试原保存请求').trigger('click'); await flushPromises()
    expect(ctx.save.mock.calls[1]![0]).toEqual(original); expect(ctx.store.draft).toBe('新文')
  })
  it('首次上传可创建会话，不因切换到新会话的监听器取消本次上传', async () => {
    const ctx = await setup(); const record = ctx.store.active!
    ctx.store.active = null; await flushPromises()
    vi.spyOn(ctx.store, 'create').mockImplementationOnce(async () => { ctx.store.active = record; return true })
    await choose(ctx.wrapper, [new File(['x'], 'a.png')])
    expect(ctx.upload).toHaveBeenCalledOnce()
    expect(ctx.wrapper.text()).toContain('单据.png')
  })
  it('会话被清空/版本变化时中止正在上传的文件并恢复服务器待保存列表', async () => {
    const ctx = await setup()
    let finish!: (file: AssistantAttachment) => void
    ctx.upload.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    await choose(ctx.wrapper, [new File(['x'], 'a.png')])
    ctx.store.active!.version++
    await nextTick(); await flushPromises()
    expect(ctx.upload.mock.calls[0]![2].signal.aborted).toBe(true)
    finish(attachment()); await flushPromises()
    expect(ctx.wrapper.text()).not.toContain('单据.png')
  })
})

describe('附件预览', () => {
  it('仅使用带会话的私有接口和内存 URL，身份切换立即撤销 URL', async () => {
    const ctx = await setup()
    const preview = vi.spyOn(api, 'preview').mockResolvedValue({ kind: 'image', truncated: false })
    const content = vi.spyOn(api, 'content').mockResolvedValue(new Blob(['image'], { type: 'image/png' }))
    vi.stubGlobal('URL', class extends URL { static createObjectURL = vi.fn(() => 'blob:test-private'); static revokeObjectURL = vi.fn() })
    const wrapper = mount(AssistantAttachmentList, { props: { attachments: [attachment()] }, global: { stubs: { ElDialog: dialog } } })
    await button(wrapper, '预览').trigger('click'); await flushPromises()
    expect(preview).toHaveBeenCalledWith(fileId, id, expect.any(AbortSignal)); expect(content).toHaveBeenCalled()
    expect(wrapper.get('img').attributes('src')).toBe('blob:test-private')
    ctx.auth.setLoginSession({ staff_id: 2, name: '其他人', token: 'token2', roles: ['ADMIN'] }); await nextTick()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-private'); expect(wrapper.find('img').exists()).toBe(false)
  })
  it('文件名、HTML 与公式仅当文本显示，预览被截取有明确提示', async () => {
    await setup()
    vi.spyOn(api, 'preview').mockResolvedValue({ kind: 'text', text: '<script>alert(1)</script>\n=1+1', truncated: true })
    const wrapper = mount(AssistantAttachmentList, { props: { attachments: [attachment({ display_name: '<img onerror=bad>.txt' })] }, global: { stubs: { ElDialog: dialog } } })
    await button(wrapper, '预览').trigger('click'); await flushPromises()
    expect(wrapper.get('pre').text()).toContain('<script>'); expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false); expect(wrapper.text()).toContain('部分预览')
  })
})
