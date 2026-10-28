import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MoneyText from '@/components/MoneyText.vue'
import { formatMoneyText } from '@/components/money'

describe('money display', () => {
  it('formats decimal strings without converting through floating point', () => {
    expect(formatMoneyText('1234567.8')).toBe('¥1,234,567.80')
    expect(formatMoneyText('-0.5')).toBe('-¥0.50')
    expect(formatMoneyText('0')).toBe('¥0.00')
  })

  it('preserves precision beyond the display precision and handles empty values', () => {
    expect(formatMoneyText('1.2345')).toBe('¥1.2345')
    expect(formatMoneyText(null)).toBe('--')
    expect(formatMoneyText('not-a-number')).toBe('--')
  })

  it('renders negative amounts with the danger style', () => {
    const wrapper = mount(MoneyText, {
      props: { value: '-12.30' },
    })

    expect(wrapper.text()).toBe('-¥12.30')
    expect(wrapper.classes()).toContain('is-negative')
  })
})
