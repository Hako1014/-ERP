import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'

import ApprovalFlow from '@/components/business/ApprovalFlow.vue'
import OperationTimeline from '@/components/business/OperationTimeline.vue'

describe('business timeline components', () => {
  it('renders approval state, flow steps, and record actions', () => {
    const wrapper = mount(ApprovalFlow, {
      props: {
        flowName: '采购付款审批',
        autoLoad: false,
        state: {
          status: 'APPROVED',
          current_step: null,
          total_steps: 3,
          approved_steps: [1, 2, 3],
        },
        records: [
          {
            id: 1,
            step: 0,
            action: 'SUBMIT',
            status: 'DONE',
            approver_name: '张子华',
            audit_time: '2026-09-24T01:00:00Z',
          },
          {
            id: 2,
            step: 1,
            step_name: '主管',
            action: 'APPROVE',
            status: 'APPROVED',
            approver_name: '李主管',
            comment: '同意',
            audit_time: '2026-09-24T02:00:00Z',
          },
        ],
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('采购付款审批')
    expect(wrapper.text()).toContain('已通过')
    expect(wrapper.text()).toContain('主管 · 审批通过')
    expect(wrapper.text()).toContain('李主管')
  })

  it('renders operation audit details', () => {
    const wrapper = mount(OperationTimeline, {
      props: {
        autoLoad: false,
        items: [
          {
            id: 1,
            action: 'UPDATE',
            actor_name: '管理员',
            target_type: 'purchase_order',
            target_id: 'PO001',
            summary: '修改采购数量',
            reason: '供应商缺货',
            created_at: '2026-09-24T03:00:00Z',
          },
        ],
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('修改')
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('修改采购数量')
    expect(wrapper.text()).toContain('原因：供应商缺货')
    expect(wrapper.text()).toContain('purchase_order #PO001')
  })
})
