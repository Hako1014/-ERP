import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'

import StatusTag from '@/components/StatusTag.vue'
import { resolveStatusMeta, statusLabel } from '@/components/status'

describe('status tag', () => {
  it('resolves tone, icon, and text from a status code', () => {
    const meta = resolveStatusMeta('APPROVED')

    expect(meta.tone).toBe('success')
    expect(meta.icon).toBeTruthy()
    expect(statusLabel('APPROVED')).toBe('已审核')
  })

  it('renders color, icon, and label together', () => {
    const wrapper = mount(StatusTag, {
      props: {
        status: 'REJECTED',
        label: '审核不通过',
      },
      global: {
        plugins: [ElementPlus],
      },
    })

    expect(wrapper.text()).toContain('审核不通过')
    expect(wrapper.find('.el-tag--danger').exists()).toBe(true)
    expect(wrapper.find('.status-tag__icon').exists()).toBe(true)
    expect(wrapper.attributes('aria-label')).toContain('状态编码 REJECTED')
  })

  it('falls back to an information style for unknown status codes', () => {
    const meta = resolveStatusMeta('UNKNOWN_CODE')
    expect(meta.tone).toBe('info')
  })
})
