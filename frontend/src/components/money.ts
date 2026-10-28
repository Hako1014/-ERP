import type { MoneyFormatOptions } from '@/components/types'

const MONEY_PATTERN = /^([+-]?)(\d+)(?:\.(\d+))?$/

export function formatMoneyText(
  value: string | number | bigint | null | undefined,
  options: MoneyFormatOptions = {},
): string {
  const {
    precision = 2,
    symbol = '¥',
    showSymbol = true,
    fallback = '--',
  } = options

  if (value === null || value === undefined || value === '') {
    return fallback
  }

  const source = String(value).trim()
  const matched = MONEY_PATTERN.exec(source)
  if (!matched) {
    return fallback
  }

  const [, sign, integerPart, rawFraction = ''] = matched
  const groupedInteger = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const fraction =
    rawFraction.length > precision
      ? rawFraction
      : rawFraction.padEnd(precision, '0')
  const decimalPart = precision > 0 || fraction ? `.${fraction}` : ''
  const normalizedSign = sign === '-' ? '-' : ''
  const prefix = showSymbol ? symbol : ''

  return `${normalizedSign}${prefix}${groupedInteger}${decimalPart}`
}
