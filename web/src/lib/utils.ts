import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** 毫秒 → 人类可读（用于耗时） */
export function formatMs(ms: number | undefined | null): string {
  if (ms == null) return '—'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 分数按位宽对齐 —— 表格里数字必须能竖着比 */
export function formatScore(score: number | null | undefined): string {
  if (score == null) return '—'
  if (score === 0) return '0'
  if (score < 0.001) return score.toExponential(1)
  return score.toFixed(4)
}

/** 大数字缩写（文档数、chunk 数） */
export function formatCount(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** 标题路径 → 「第三章 差旅制度 › 3.2 住宿标准」 */
export function formatHeading(heading: string): string {
  return heading.split(' > ').join(' › ')
}
