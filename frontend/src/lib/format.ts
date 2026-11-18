/** 進度與用量的數字格式（進度視窗、右下角）。 */
import type { Progress } from '../bridge'

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function formatAmount(progress: Progress) {
  const { done, total, unit } = progress
  if (unit === 'rows') return `${done.toLocaleString()} / ${total.toLocaleString()} 行`
  if (unit === 'files') return `第 ${done + 1} / ${total} 個檔`
  if (unit === 'checks') return `${done} / ${total} 項`
  return `${formatBytes(done)} / ${formatBytes(total)}`
}

export function formatElapsed(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m >= 60
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

/** 用量的大小：0 寫成「0 MB」，其他同 formatBytes。 */
export function formatSize(n: number) {
  return n === 0 ? '0 MB' : formatBytes(n)
}
